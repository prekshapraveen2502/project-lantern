"""Part 1 item 2: the `facts` stage. Builds data/facts/facts.duckdb from each comparable's ingested 10-K and 10-Q.

Per comp (params comps.tickers):
  1. CIK and fiscal year end from data/edgar_comps/{T}/submissions.json
  2. read both filings' iXBRL (src/facts/xbrl_read.py: offline, data/arelle_cache, guard) and group repeated tags
  3. choose each statement metric's concept once, from the latest 10-K (choose_concepts), and map facts to metrics;
     a period where the chosen concept is missing but another listed concept is present is logged, not filled
  4. write every numeric fact (tier 1, deal_id public, metric NULL if unmapped) with an ixbrl_element citation
  5. cross-check against companyfacts.json (same accession, concept, unit, period; no dims): counts and mismatches
The log (--log) holds counts and lists only: no timestamps or timings (it is a stage output and must be identical
on rerun). Timings go to data/bench/cs2/facts.csv through runstats, as in Part 0.

Usage: python -m src.facts.build --out data/facts/facts.duckdb --log data/facts/build_log.json
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from importlib.metadata import version
from pathlib import Path

import pandas as pd
import yaml

from src.facts.periods import fiscal_labels
from src.facts.claims import extract_deal
from src.facts.schema import CLAIM_COLUMNS, FACT_COLUMNS, create_db
from src.facts.vocab import load_vocab
from src.facts.xbrl_read import check_unconvertible, citation_value, group_tags, read_filing
from src.facts.xlsx_read import read_workbook
from src.ingest.runstats import RunStats

FORMS = ("10-K", "10-Q")


def choose_concepts(tenk_facts: list[dict], vocab: dict) -> dict[str, dict]:
    """Decision 2: per metric, the first listed concept the latest 10-K reports with no dims (xbrl_select: first),
    or every listed concept it reports (xbrl_select: all). If none is undimensioned and the metric allows
    xbrl_dims, the first concept reported on an allowed axis. Returns {metric: {concepts, dims_axes}}."""
    plain = {f["concept"] for f in tenk_facts if f["dims"] == "{}"}
    on_axis = {(f["concept"], ax) for f in tenk_facts for ax in json.loads(f["dims"])}
    chosen = {}
    for name, m in vocab["metrics"].items():
        concepts = m.get("xbrl") or []
        if not concepts:
            continue
        if m.get("xbrl_select", "first") == "all":
            hit = [c for c in concepts if c in plain]
            if hit:
                chosen[name] = {"concepts": hit, "dims_axes": []}
            continue
        first = next((c for c in concepts if c in plain), None)
        if first:
            chosen[name] = {"concepts": [first], "dims_axes": []}
        elif m.get("xbrl_dims"):
            first = next((c for c in concepts if any((c, ax) in on_axis for ax in m["xbrl_dims"])), None)
            if first:
                chosen[name] = {"concepts": [first], "dims_axes": list(m["xbrl_dims"])}
    return chosen


def metric_index(chosen: dict[str, dict]) -> dict[str, tuple[str, list[str]]]:
    """concept -> (metric, allowed axes). A concept chosen for two metrics is an error in the vocabulary."""
    idx = {}
    for name, ch in chosen.items():
        for c in ch["concepts"]:
            if c in idx:
                raise ValueError(f"concept {c} chosen for both {idx[c][0]} and {name}")
            idx[c] = (name, ch["dims_axes"])
    return idx


def metric_for(fact: dict, idx: dict) -> str | None:
    """A fact gets a metric only when its concept was chosen and it has no dims, or only allowed axes."""
    hit = idx.get(fact["concept"])
    if hit is None:
        return None
    name, axes = hit
    dims = json.loads(fact["dims"])
    if not dims or (axes and set(dims) <= set(axes)):
        return name
    return None


def missing_periods(facts: list[dict], chosen: dict, vocab: dict, doc_id: str) -> list[dict]:
    """Periods where another listed concept has an undimensioned fact but the chosen one does not (logged, not
    filled: decision 2)."""
    out = []
    plain = [f for f in facts if f["dims"] == "{}"]
    for name, ch in chosen.items():
        if vocab["metrics"][name].get("xbrl_select", "first") != "first" or ch["dims_axes"]:
            continue
        have = {(f["start"], f["end"]) for f in plain if f["concept"] == ch["concepts"][0]}
        others = [c for c in vocab["metrics"][name]["xbrl"] if c != ch["concepts"][0]]
        for f in plain:
            if f["concept"] in others and (f["start"], f["end"]) not in have:
                out.append({"metric": name, "chosen": ch["concepts"][0], "other": f["concept"], "doc_id": doc_id,
                            "start": str(f["start"]) if f["start"] else None, "end": str(f["end"])})
    return sorted(out, key=lambda r: (r["metric"], r["end"], r["start"] or "", r["other"]))


def crosscheck(rows: list[dict], companyfacts: dict) -> dict:
    """Compare undimensioned tier-1 facts with companyfacts.json (same accession, concept, unit, start, end)."""
    idx: dict[tuple, list[float]] = {}
    for ns, concepts in companyfacts.get("facts", {}).items():
        for name, data in concepts.items():
            for unit, entries in data.get("units", {}).items():
                for x in entries:
                    key = (f"{ns}:{name}", unit, x.get("accn"), x.get("start"), x.get("end"))
                    idx.setdefault(key, []).append(x["val"])
    compared = matched = absent = 0
    mismatches = []
    for r in rows:
        if r["dims"] != "{}":
            continue
        key = (r["concept"], r["unit"], r["doc_id"], r["start"], r["end"])
        vals = idx.get(key)
        if vals is None:
            absent += 1
            continue
        compared += 1
        if any(abs(v - r["value"]) <= 1e-6 * max(1.0, abs(v)) for v in vals):
            matched += 1
        else:
            mismatches.append({"fact_id": r["fact_id"], "concept": r["concept"], "doc_id": r["doc_id"],
                               "start": r["start"], "end": r["end"], "value": r["value"],
                               "companyfacts": sorted(set(vals))})
    return {"compared": compared, "matched": matched, "mismatched": len(mismatches),
            "not_in_companyfacts": absent, "mismatches": sorted(mismatches, key=lambda m: m["fact_id"])}


def fact_id(accession: str, element_id: str) -> str:
    return "F-" + hashlib.sha256(f"{accession}|{element_id}".encode()).hexdigest()[:12]


def fact_id_for(accession: str, f: dict) -> str:
    """sha256(accession|element id); for a tag without an id, sha256(accession|concept|contextRef|unitRef),
    which is unique because it is R1's grouping key."""
    if f.get("element_id"):
        return fact_id(accession, f["element_id"])
    key = f"{accession}|{f['concept']}|{f['context_ref']}|{f['unit_ref']}"
    return "F-" + hashlib.sha256(key.encode()).hexdigest()[:12]


def citation_for(f: dict, accession: str, cik: str, doc: str, source_path: str) -> dict:
    """ixbrl_element when the cited tag has an id (url anchored on it), otherwise ixbrl_context (brief item 3).
    text is the tag's text as shown and sign its sign attribute: value = (-1 if sign == "-" else 1) x text x 10^scale."""
    url = f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accession.replace('-', '')}/{doc}"
    common = {"doc_id": accession, "context_ref": f["context_ref"], "unit_ref": f["unit_ref"],
              "also_tagged": [i for i in f["also_tagged"] if i], "source_path": source_path,
              "text": f["text"], "sign": f["sign"]}
    if f.get("element_id"):
        return {"kind": "ixbrl_element", "element_id": f["element_id"], "url": f"{url}#{f['element_id']}", **common}
    return {"kind": "ixbrl_context", "concept": f["concept"], "url": url, **common}


def text_check(facts: list[dict]) -> dict:
    """How many cited tags' text, with sign and scale, gives back the stored value (the Part 4 citation check)."""
    resolves, not_numeric, bad = 0, 0, []
    for f in facts:
        v = citation_value(f["text"], f["scale"], f["sign"])
        if v is None:
            not_numeric += 1
        elif abs(v - f["value"]) <= 1e-9 * max(1.0, abs(f["value"])):
            resolves += 1
        else:
            bad.append({"element_id": f["element_id"], "concept": f["concept"], "text": f["text"],
                        "sign": f["sign"], "scale": f["scale"], "value": f["value"]})
    return {"resolves": resolves, "not_numeric": not_numeric, "mismatched": len(bad),
            "mismatches": sorted(bad, key=lambda b: str(b["element_id"]))}


def to_row(f: dict, filing: dict, cik: str, fye: str, tol: int, metric: str | None, src_sha: str,
           extractor: str, source_path: str) -> dict:
    accn = filing["accn"]
    fy, fp = fiscal_labels(f["start"], f["end"], fye, tol)
    raw = f["decimals"]
    exact = raw is not None and str(raw).upper() == "INF"
    lineage = {"parents": [], "source_sha256": src_sha}
    if exact:
        lineage["decimals_raw"] = "INF"
    citation = citation_for(f, accn, cik, filing["doc"], source_path)
    return {
        "fact_id": fact_id_for(accn, f), "deal_id": "public", "entity_id": cik, "concept": f["concept"],
        "metric": metric, "value": f["value"], "unit": f["unit"], "scale": f["scale"],
        "period_type": f["period_type"], "start": str(f["start"]) if f["start"] else None, "end": str(f["end"]),
        "fiscal_year": fy, "fiscal_period": fp, "dims": f["dims"],
        "decimals": None if exact or raw in (None, "") else int(raw), "tier": 1, "source_type": "edgar_xbrl",
        "doc_id": accn, "filed": filing["date"], "citation": json.dumps(citation, sort_keys=True),
        "extractor": extractor, "lineage": json.dumps(lineage, sort_keys=True),
    }


def build_comp(ticker: str, comps_dir: Path, vocab: dict, cache: Path, tol: int, extractor: str) -> tuple[list, dict]:
    tdir = comps_dir / ticker
    subs = json.loads((tdir / "submissions.json").read_text(encoding="utf-8"))
    cik, fye = str(subs["cik"]).zfill(10), subs["fiscalYearEnd"]
    vocab_concepts = {c for m in vocab["metrics"].values() for c in m.get("xbrl", [])}
    filings = {}
    for form in FORMS:
        fdir = next((tdir / form).iterdir())
        meta = json.loads((fdir / "filing.json").read_text(encoding="utf-8"))
        htm = fdir / "unpacked" / meta["doc"]
        read = read_filing(htm, cache)
        check_unconvertible(read["unconvertible"], vocab_concepts, f"{ticker} {meta['accn']}")
        facts, inconsistent = group_tags(read["tags"])
        filings[form] = {"meta": meta, "facts": facts, "inconsistent": inconsistent, "read": read,
                         "sha": hashlib.sha256(htm.read_bytes()).hexdigest(), "source_path": htm.as_posix()}
    chosen = choose_concepts(filings["10-K"]["facts"], vocab)
    idx = metric_index(chosen)
    rows, log_filings, missing = [], {}, []
    for form, fl in filings.items():
        accn = fl["meta"]["accn"]
        for f in fl["facts"]:
            rows.append(to_row(f, fl["meta"], cik, fye, tol, metric_for(f, idx), fl["sha"], extractor,
                               fl["source_path"]))
        missing += missing_periods(fl["facts"], chosen, vocab, accn)
        rd = fl["read"]
        log_filings[accn] = {"form": form, "ix_nonfraction_tags": rd["ix_nonfraction"],
                             "numeric_tags_loaded": rd["loaded"], "tags_stored": len(rd["tags"]),
                             "tags_nil": rd["nil"], "tags_unconvertible_skipped": len(rd["unconvertible"]),
                             "unconvertible": sorted(rd["unconvertible"], key=lambda u: u["element_id"]),
                             "facts": len(fl["facts"]), "inconsistent_groups": len(fl["inconsistent"]),
                             "inconsistent": fl["inconsistent"], "citation_text": text_check(fl["facts"]),
                             "citations_ixbrl_context": sum(1 for f in fl["facts"] if not f.get("element_id"))}
    if len({r["fact_id"] for r in rows}) != len(rows):
        raise RuntimeError(f"{ticker}: duplicate fact_id")
    cf = json.loads((tdir / "companyfacts.json").read_text(encoding="utf-8"))
    stmt = {n for n, m in vocab["metrics"].items() if m["kind"] == "statement"}
    log = {"cik": cik, "fiscal_year_end": fye, "filings": log_filings,
           "concepts_chosen": {k: v["concepts"] for k, v in sorted(chosen.items())},
           "statement_metrics_with_facts": len({r["metric"] for r in rows if r["metric"] in stmt}),
           "missing_periods": missing, "crosscheck": crosscheck(rows, cf)}
    return rows, log


def build_dataroom(deal: str, root: Path, vocab: dict, tol: int) -> tuple[list[dict], dict]:
    """Item 4: every .xlsx in data/dataroom/{deal}/export/manifest.csv -> tier-3 facts. deal_id is the folder name."""
    import csv
    from importlib.metadata import version as pkg_version
    export = root / deal / "export"
    manifest = list(csv.DictReader(open(export / "manifest.csv", encoding="utf-8")))
    records = [json.loads(line) for line in open(export / "records.jsonl", encoding="utf-8")]
    rows, log = [], {}
    for m in manifest:
        if not m["file"].lower().endswith(".xlsx"):
            continue
        if m["deal_id"] != deal:
            raise RuntimeError(f"{export / 'manifest.csv'}: {m['file']} has deal_id {m['deal_id']}, folder is {deal}")
        block_ids = {r["section"]: r["block_id"] for r in records if r["doc_id"] == m["accession"]}
        r, log[m["accession"]] = read_workbook(root / deal / "raw" / m["file"], m["accession"], deal, vocab, tol,
                                               block_ids, m["sha256"], f"openpyxl {pkg_version('openpyxl')}")
        log[m["accession"]]["file"] = m["file"]
        rows += r
    return rows, log


def json_safe(obj):
    if isinstance(obj, dict):
        return {k: json_safe(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [json_safe(v) for v in obj]
    return obj if isinstance(obj, (str, int, float, bool)) or obj is None else str(obj)


def main() -> None:
    ap = argparse.ArgumentParser(description="Part 1 facts stage")
    ap.add_argument("--out", default="data/facts/facts.duckdb")
    ap.add_argument("--log", default="data/facts/build_log.json")
    ap.add_argument("--params", default="params.yaml")
    ap.add_argument("--vocab", default="config/metrics_vocab.yaml")
    ap.add_argument("--comps", default="data/edgar_comps")
    ap.add_argument("--stats", default=None, help="run-stats CSV (default data/bench/cs2/facts.csv)")
    ap.add_argument("--deals", nargs="*", default=[], help="data-room folders under --dataroom (item 4)")
    ap.add_argument("--dataroom", default="data/dataroom")
    args = ap.parse_args()

    params = yaml.safe_load(Path(args.params).read_text(encoding="utf-8"))
    vocab = load_vocab(args.vocab)
    cache, tol = Path(params["facts"]["arelle_cache"]), int(params["facts"]["fye_tolerance_days"])
    extractor = f"arelle-release {version('arelle-release')}"
    stats = RunStats("facts", args.stats)
    all_rows, comps_log, failed = [], {}, []
    for t in params["comps"]["tickers"]:
        with stats.item(t, "facts") as s:
            rows, comps_log[t] = build_comp(t, Path(args.comps), vocab, cache, tol, extractor)
            all_rows += rows
            s["units"] = len(rows)
        if s["status"] != "ok":
            failed.append(t)
    dataroom_log, claim_rows, claims_log = {}, [], {}
    for deal in args.deals:
        with stats.item(f"dataroom:{deal}", "facts") as s:
            rows, dataroom_log[deal] = build_dataroom(deal, Path(args.dataroom), vocab, tol)
            all_rows += rows
            s["units"] = len(rows)
        if s["status"] != "ok":
            failed.append(f"dataroom:{deal}")
            continue
        with stats.item(f"claims:{deal}", "claims") as s:               # item 5: tier-4 claims, via the LLM client
            crow, claims_log[deal] = extract_deal(deal, Path(args.dataroom), vocab, rows, params)
            claim_rows += crow
            s["units"] = len(crow)
        if s["status"] != "ok":
            failed.append(f"claims:{deal}")
    stats.finish()
    if failed:
        sys.exit(f"[ERROR] facts: failed for {failed} (see data/bench/cs2/facts.csv); no store written")

    con = create_db(args.out)
    df = pd.DataFrame(all_rows, columns=FACT_COLUMNS).astype(
        {"scale": "Int64", "decimals": "Int64", "fiscal_year": "Int64", "tier": "Int64"})
    con.register("new_facts", df)
    con.execute("INSERT INTO facts SELECT * FROM new_facts")
    n = con.execute("SELECT count(*) FROM facts").fetchone()[0]
    if claim_rows:
        cdf = pd.DataFrame(claim_rows, columns=CLAIM_COLUMNS).astype(
            {"scale": "Int64", "fiscal_year": "Int64", "tier": "Int64", "page": "Int64"})
        con.register("new_claims", cdf)
        con.execute(f"INSERT INTO claims ({', '.join(chr(34) + c + chr(34) for c in CLAIM_COLUMNS)}) "
                    f"SELECT * FROM new_claims")
    n_claims = con.execute("SELECT count(*) FROM claims").fetchone()[0]
    con.close()

    totals = {k: sum(c["crosscheck"][k] for c in comps_log.values())
              for k in ("compared", "matched", "mismatched", "not_in_companyfacts")}
    totals.update({"facts": n,
                   "inconsistent_groups": sum(f["inconsistent_groups"] for c in comps_log.values()
                                              for f in c["filings"].values()),
                   "missing_periods": sum(len(c["missing_periods"]) for c in comps_log.values()),
                   "tags_unconvertible_skipped": sum(f["tags_unconvertible_skipped"] for c in comps_log.values()
                                                     for f in c["filings"].values()),
                   **{f"citation_text_{k}": sum(f["citation_text"][k] for c in comps_log.values()
                                                for f in c["filings"].values())
                      for k in ("resolves", "not_numeric", "mismatched")},
                   "citations_ixbrl_context": sum(f["citations_ixbrl_context"] for c in comps_log.values()
                                                  for f in c["filings"].values())})
    Path(args.log).parent.mkdir(parents=True, exist_ok=True)
    totals["dataroom_facts"] = sum(d["facts"] for deal in dataroom_log.values() for d in deal.values())
    totals["claims"] = n_claims
    Path(args.log).write_text(json.dumps(json_safe({"totals": totals, "comps": comps_log, "dataroom": dataroom_log,
                                                    "claims": claims_log}),
                                         indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print(f"[INFO] facts: {n} facts -> {args.out}; totals {totals}; log -> {args.log}")


if __name__ == "__main__":
    main()
