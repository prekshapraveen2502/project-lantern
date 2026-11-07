"""Part 5 item 2: the extract_profile stage. For each deal, three DealProfiles from the CIM:
  (a) single   one call over the whole CIM
  (b) staged   one call per CIM section (CS1 section labels; a section holding only headings is merged into the next;
               decision 2026-11-07 Q4 a), partial profiles merged by profile.merge()
  (c) staged_reconciled  (b) + Part 1 reconciliation (src/extract/link.py)
Both (a) and (b) see the same input: every non-empty CIM block, tables included, in page order, with the same system
prompt and the same response model, through src/llm/client.py (instructor, temperature 0).

Calls cache in data/extract_cache/llm (decision Q13), ledger stages extract:single and extract:staged. In record mode
each call's wall time, tokens and cost (ledger delta) are written to data/bench/cs2/extract_profile.csv (outside the
stage outputs, like src/ingest/runstats.py); replay never writes it (decision Q11). Then src/extract/evaluate.py
scores the three profiles against the gold file and writes deal_profile.json.
"""
from __future__ import annotations

import argparse
import csv
import json
import time
from datetime import datetime, timezone
from pathlib import Path

import duckdb
import yaml

from src.extract import evaluate, link
from src.extract.profile import Builder, extraction_model, merge
from src.facts.claims import deal_calendar
from src.facts.reconcile import index_facts
from src.qa.evidence import doc_forms
from src.qa.store import _row

BENCH = Path("data/bench/cs2/extract_profile.csv")
BENCH_FIELDS = ["run_at", "deal", "strategy", "call", "section", "cached", "seconds", "tokens_in", "tokens_out", "usd"]

SYSTEM = (
    "You extract a structured deal profile from a Confidential Information Memorandum (CIM) for a private-equity "
    "screening team. Use only the text given; each block starts with its [block_id]. Fill a field only when the text "
    "states it, otherwise leave it null or empty. For every number, copy the shortest clause containing it verbatim "
    "from one block as quote, give the number as written with its scale word and unit, and never compute, convert or "
    "correct a number. Record what the document says, labelled the way the document labels it, and choose the "
    "vocabulary metric that matches the document's own definition (e.g. revenue before pass-through costs is "
    "gross_revenue). Use period labels FYyyyy, CYyyyy or 'Qn FYyyyy'. disclosure_gaps lists only items the document "
    "itself says are not disclosed."
)


def use_extract_cache(client, root: Path = Path("data/extract_cache")) -> None:
    """Decision Q13: extraction and memo calls cache in data/extract_cache/llm; client.py is unchanged."""
    client.CACHE = Path(root) / "llm"
    client.CACHE.mkdir(parents=True, exist_ok=True)


def cim_blocks(dataroom: Path, deal: str) -> list[dict]:
    forms = doc_forms(dataroom)
    recs = [json.loads(l) for l in open(dataroom / deal / "export" / "records.jsonl", encoding="utf-8")]
    cims = sorted({r["doc_id"] for r in recs if forms.get(r["doc_id"]) == "cim"})
    if len(cims) != 1:
        raise ValueError(f"{deal}: expected one CIM, found {cims}")
    return [r for r in recs if r["doc_id"] == cims[0] and (r.get("text") or "").strip()]


def sections(blocks: list[dict]) -> list[tuple[str, list[dict]]]:
    """Consecutive blocks with the same CS1 section label; a section of only Title blocks joins the next one."""
    out: list[tuple[str, list[dict]]] = []
    for b in blocks:
        if out and out[-1][0] == b["section"]:
            out[-1][1].append(b)
        else:
            out.append((b["section"], [b]))
    merged, carry = [], []
    for name, bs in out:
        if all(b["block_type"] == "Title" for b in bs):
            carry += bs
            continue
        merged.append((name, carry + bs))
        carry = []
    if carry:
        merged.append((carry[0]["section"], carry))
    return merged


def messages(blocks: list[dict], vocab: dict, what: str) -> list[dict]:
    from src.facts.claims import metric_menu
    text, page = [], None
    for b in blocks:
        if b.get("page") != page:
            page = b.get("page")
            text.append(f"--- page {page} ---")
        text.append(f"[{b['block_id']}] {' '.join(b['text'].split())}")
    return [{"role": "system", "content": SYSTEM},
            {"role": "user", "content": f"Metrics:\n{metric_menu(vocab)}\n\n{what}:\n" + "\n".join(text)}]


class Recorder:
    """One bench row per call, record mode only (timings and tokens are not stage outputs)."""

    def __init__(self, client, deal: str):
        self.client, self.deal, self.rows = client, deal, []
        self.run_at = datetime.now(timezone.utc).isoformat(timespec="seconds")

    def call(self, strategy: str, i: int, section: str, rmodel, msgs, params: dict):
        c, model = self.client, params["llm"]["models"]["large"]
        cached = (c.CACHE / f"{c.cache_key(c.PROVIDER, model, msgs, temperature=0, max_tokens=int(params['extract']['max_tokens']), schema=rmodel.model_json_schema())}.json").exists()
        n0 = sum(1 for _ in open(c.LEDGER)) if Path(c.LEDGER).exists() else 0
        t0 = time.perf_counter()
        out = c.structured(rmodel, msgs, model=model, stage=f"extract:{strategy}", temperature=0,
                           max_tokens=int(params["extract"]["max_tokens"]))
        secs = time.perf_counter() - t0
        new = [json.loads(l) for l in open(c.LEDGER)][n0:] if Path(c.LEDGER).exists() else []
        self.rows.append({"run_at": self.run_at, "deal": self.deal, "strategy": strategy, "call": i,
                          "section": section, "cached": cached, "seconds": round(secs, 3),
                          "tokens_in": sum(r["in"] for r in new), "tokens_out": sum(r["out"] for r in new),
                          "usd": round(sum(r["usd"] for r in new), 6)})
        return out

    def check_cap(self, strategy: str, cap: float) -> None:
        usd = sum(r["usd"] for r in self.rows if r["strategy"] == strategy)
        if usd > cap:
            raise RuntimeError(f"{self.deal} {strategy}: {usd} USD > budget.usd_cap_per_task {cap}")

    def write(self, extra: list[dict]) -> None:
        if self.client.MODE != "record":
            return
        old = list(csv.DictReader(open(BENCH, encoding="utf-8"))) if BENCH.exists() else []
        keep = [r for r in old if r["deal"] != self.deal]
        BENCH.parent.mkdir(parents=True, exist_ok=True)
        with open(BENCH, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=BENCH_FIELDS)
            w.writeheader()
            w.writerows(keep + self.rows + [{"run_at": self.run_at, "deal": self.deal, **x} for x in extra])


def load_store(db: Path, deal: str) -> tuple[list[dict], list[dict]]:
    con = duckdb.connect(str(db), read_only=True)
    try:
        out = []
        for table in ("facts", "claims"):
            cur = con.execute(f"select * from {table} where deal_id = ?", [deal])
            out.append([_row(cur, r) for r in cur.fetchall()])
        return out[0], out[1]
    finally:
        con.close()


def run_deal(deal: str, args, params: dict, vocab: dict, client) -> None:
    out = Path(args.out) / deal
    out.mkdir(parents=True, exist_ok=True)
    facts, claims = load_store(Path(args.facts), deal)
    facts = [f for f in facts if f["tier"] == 3]
    fye, latest_fy = deal_calendar(facts)
    idx, by_id = index_facts(facts), {f["fact_id"]: f for f in facts}
    disc = {d["claim_id"]: d for d in map(json.loads, open(args.discrepancies, encoding="utf-8"))}
    tol, cap = float(params["reconcile"]["tolerance_rel"]), float(params["budget"]["usd_cap_per_task"])
    blocks = cim_blocks(Path(args.dataroom), deal)
    rmodel = extraction_model(list(vocab["metrics"]))
    rec, logs = Recorder(client, deal), {}

    ex_single = rec.call("single", 0, "all", rmodel, messages(blocks, vocab, "CIM, all pages"), params)
    rec.check_cap("single", cap)
    ex_staged = []
    for i, (name, bs) in enumerate(sections(blocks)):
        ex_staged.append((name, bs, rec.call("staged", i, name, rmodel, messages(bs, vocab, f"CIM section '{name}'"),
                                             params)))
    rec.check_cap("staged", cap)
    claims_by_id = {c["claim_id"]: c for c in claims}

    def profiles_from(raw: bool, logs: dict) -> tuple[dict, float]:
        """The three strategies from the same model outputs; raw=True keeps unlocated numbers (decision Q14)."""
        b = Builder(deal, blocks, vocab, latest_fy, raw=raw)
        single = b.build(ex_single, "single")
        logs["single"], logs["staged"], parts = b.log, [], []
        for name, bs, ex in ex_staged:
            sb = Builder(deal, bs, vocab, latest_fy, raw=raw)
            parts.append(sb.build(ex, "staged"))
            logs["staged"] += [{"section": name, **x} for x in sb.log]
        staged = merge(parts, "staged", logs["staged"])
        for name, p in (("single", single), ("staged", staged)):
            link.link(p, claims, idx, by_id, tol, logs[name])
        t0 = time.perf_counter()
        recon = staged.model_copy(deep=True)
        recon.strategy = "staged_reconciled"
        logs["staged_reconciled"] = []
        link.reconcile(recon, claims_by_id, disc, idx, by_id, logs["staged_reconciled"])
        return {"single": single, "staged": staged, "staged_reconciled": recon}, time.perf_counter() - t0

    logs, raw_logs = {}, {}
    profiles, recon_secs = profiles_from(False, logs)
    raw_profiles, _ = profiles_from(True, raw_logs)
    part1 = [json.loads(l) for l in open(client.LEDGER)] if Path(client.LEDGER).exists() else []
    part1_usd = round(sum(r["usd"] for r in part1 if r["stage"] == "facts:claims"), 6)
    rec.write([{"strategy": "staged_reconciled", "call": 0, "section": "reconcile", "cached": False,
                "seconds": round(recon_secs, 3), "tokens_in": 0, "tokens_out": 0, "usd": 0},
               {"strategy": "staged_reconciled", "call": 1, "section": "part1_claims (facts:claims ledger rows)",
                "cached": False, "seconds": "", "tokens_in": "", "tokens_out": "", "usd": part1_usd}])

    for name, p in profiles.items():
        (out / f"{name}.json").write_text(p.model_dump_json(indent=1) + "\n", encoding="utf-8")
    (out / "extract_log.json").write_text(json.dumps(logs, indent=1, sort_keys=True, default=str) + "\n",
                                          encoding="utf-8")
    evaluate.run(deal, profiles, params, out, BENCH, raw_profiles=raw_profiles)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--deals", nargs="+", required=True)
    ap.add_argument("--dataroom", default="data/dataroom")
    ap.add_argument("--facts", default="data/facts/facts.duckdb")
    ap.add_argument("--discrepancies", default="data/facts/discrepancies.jsonl")
    ap.add_argument("--out", default="data/structured")
    args = ap.parse_args()
    from src.llm import client
    use_extract_cache(client)
    params = yaml.safe_load(open("params.yaml"))
    vocab = yaml.safe_load(open("config/metrics_vocab.yaml", encoding="utf-8"))
    for deal in args.deals:
        run_deal(deal, args, params, vocab, client)


if __name__ == "__main__":
    main()
