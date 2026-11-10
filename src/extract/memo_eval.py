"""Part 5 item 4: reports/memo_comparison.md. No LLM. Scores the RAG-only and structured Kestrel memos and the
exemplar on every line of evals/rubrics/screening_memo.yaml (Phase A section 4 and the rubric's notes), and reports
the item 2 extraction results from data/structured/{deal}/extraction_eval.json. Every number in the report is
computed here from repo files, named next to each table.

Units: a memo line (one claim, bullet or Not-disclosed item) for our memos; for the exemplar, which has no
references, each bullet and each sentence. A line's references are the bracket group at its end.
  numeric_correctness  every number the Part 4 linter reads in a unit (references removed) must equal the value of
                       one of its references within qa.numeric_tolerance_rel; a number matched only by a management or
                       web claim (C-...) counts only if the unit carries an "unverified" tier label.
  trap_coverage        a trap is covered when one unit holds all words of one group (case-insensitive substrings) and
                       all its values (linter numbers within qa.numeric_tolerance_rel).
  provenance           units with numbers whose references all resolve (fact or claim in the store, calc in the
                       registry, doc:block in the data-room records) and are at least one; and units with tier-3/4/5
                       references that carry a tier label.
  not_disclosed        units with the literal "Not disclosed.": recall over the rubric's expected items; precision =
                       marks not matching a disclosed item / all marks.
  readability          proxies only (decision 2026-11-07 Q9 a): words, units, words per unit, units per section,
                       length relative to the exemplar.
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import statistics
from pathlib import Path

import yaml

from src.calc import core as calc
from src.qa import store
from src.qa.answer import load_records
from src.qa.lint import close, numbers

HEADINGS = ["Company Overview", "Business Model and Go-to-Market", "Financial Profile", "Customers and Concentration",
            "Management and Organization", "Process and Valuation Context", "Key Risks and Diligence Questions",
            "Disclosure Gaps"]
REFS = re.compile(r"\s*\[([^\[\]]+)\]\s*$")
LABEL = re.compile(r"\((?:per [^()]*)\)")
SENT = re.compile(r"(?<=[.!?])\s+(?=[A-Z\"(])")


# ---------------------------------------------------------------- parsing
def sections(md: str) -> dict[str, list[str]]:
    out, cur = {}, None
    for line in md.splitlines():
        if line.startswith("## "):
            cur = line[3:].strip()
            out[cur] = []
        elif cur is not None and not line.startswith("#"):
            out[cur].append(line)
    return out


def units(lines: list[str], with_refs: bool) -> list[dict]:
    """Our memos: one unit per non-empty line. Exemplar: bullets (with continuation lines) and prose sentences."""
    out = []
    if with_refs:
        for l in lines:
            if l.strip():
                m = REFS.search(l)
                text = (l[:m.start()] if m else l).strip()
                out.append({"text": text.lstrip("- ").strip(), "refs": m.group(1).split("; ") if m else []})
        return out
    blocks, cur = [], []
    for l in lines + [""]:
        if not l.strip() or l.startswith("- "):
            if cur:
                blocks.append(" ".join(cur))
            cur = [l[2:].strip()] if l.startswith("- ") else []
            if l.startswith("- "):
                continue
        else:
            cur.append(l.strip())
    for b in blocks:
        out += [{"text": s.strip(), "refs": []} for s in SENT.split(b) if s.strip()]
    return out


def strip_refs(md: str) -> str:
    return "\n".join(REFS.sub("", l) for l in md.splitlines())


# ---------------------------------------------------------------- scoring
class Refs:
    def __init__(self, db: Path, registry: Path, dataroom: Path):
        self.calcs = calc.load(registry)
        self.records = load_records(dataroom)
        con = store.connect(db)
        try:
            q = lambda sql: con.execute(sql).fetchall()
            self.facts = {r[0]: (r[1], int(r[2])) for r in q("select fact_id, value, tier from facts")}
            self.claims = {r[0]: (r[1], int(r[2])) for r in q("select claim_id, value, tier from claims")}
        finally:
            con.close()
        self.tiers = {}
        for f in dataroom.glob("*/export/records.jsonl"):
            for r in map(json.loads, open(f, encoding="utf-8")):
                self.tiers[(r["doc_id"], r["block_id"])] = r.get("tier")

    def item(self, ref: str):
        """(kind, value, tier) or None."""
        if ref in self.facts:
            return ("fact", *self.facts[ref])
        if ref in self.claims:
            return ("claim", *self.claims[ref])
        if ref in self.calcs:
            return ("calc", self.calcs[ref]["value"], int(self.calcs[ref]["tier"]))
        doc, _, block = ref.partition(":")
        if (doc, block) in self.records:
            return ("block", None, self.tiers.get((doc, block)))
        return None


def numeric(us: list[dict], refs: Refs, tol: float) -> dict:
    total = correct = 0
    wrong = []
    for u in us:
        items = [refs.item(r) for r in u["refs"]]
        unverified = "unverified" in u["text"]
        for n in numbers(u["text"]):
            total += 1
            ok = any(i and i[1] is not None and close(n.value, float(i[1]), tol) and (i[0] != "claim" or unverified)
                     for i in items)
            correct += ok
            if not ok:
                wrong.append(f"{n.text} in: {u['text'][:90]}")
    return {"numbers": total, "correct": correct, "share": correct / total if total else None, "wrong": wrong}


def unit_matches(u: dict, group: dict, tol: float) -> bool:
    t = u["text"].lower()
    if not all(w.lower() in t for w in group.get("words", [])):
        return False
    vals = [n.value for n in numbers(u["text"])]
    return all(any(close(v, float(x), tol) for v in vals) for x in group.get("values", []))


def traps(us: list[dict], line: dict, tol: float) -> dict:
    covered = [t["id"] for t in line["traps"] if any(unit_matches(u, g, tol) for u in us for g in t["any_of"])]
    return {"covered": covered, "n": len(line["traps"]), "share": len(covered) / len(line["traps"])}


def provenance(us: list[dict], refs: Refs) -> dict:
    withnum = [u for u in us if numbers(u["text"])]
    resolved = [u for u in withnum if u["refs"] and all(refs.item(r) for r in u["refs"])]
    tiered = [u for u in us if any((refs.item(r) or (None, None, None))[2] in (3, 4, 5) for r in u["refs"])]
    labelled = [u for u in tiered if LABEL.search(u["text"])]
    return {"numbered_units": len(withnum), "resolved": len(resolved),
            "share": len(resolved) / len(withnum) if withnum else None,
            "tiered_units": len(tiered), "labelled": len(labelled),
            "label_share": len(labelled) / len(tiered) if tiered else None}


def has_all(text: str, kws: list[str]) -> bool:
    return all(k.lower() in text.lower() for k in kws)


def not_disclosed(us: list[dict], line: dict) -> dict:
    marks = [u["text"] for u in us if "Not disclosed." in u["text"]]
    found = [e["id"] for e in line["expected"] if any(has_all(m, e["keywords"]) for m in marks)]
    wrong = [(m, d["id"]) for m in marks for d in line["disclosed"] if has_all(m, d["keywords"])]
    wrong_marks = {m for m, _ in wrong}
    return {"marks": len(marks), "expected_found": found, "recall": len(found) / len(line["expected"]),
            "wrong": [f"{d}: {m}" for m, d in wrong],
            "precision": (len(marks) - len(wrong_marks)) / len(marks) if marks else None}


def readability(secs: dict[str, list[dict]], words_ref: int | None) -> dict:
    us = [u for v in secs.values() for u in v]
    wc = [len(LABEL.sub("", u["text"]).split()) for u in us]
    words = sum(wc)
    return {"words": words, "units": len(us), "words_per_unit": words / len(us) if us else None,
            "max_words_per_unit": max(wc) if wc else None,
            "units_per_section": {h: len(secs.get(h, [])) for h in HEADINGS},
            "length_vs_exemplar": words / words_ref if words_ref else None}


def score_memo(md: str, rubric: dict, refs: Refs, tol: float, with_refs: bool, words_ref: int | None) -> dict:
    secs = {h: units(ls, with_refs) for h, ls in sections(md).items()}
    us = [u for h in secs for u in secs[h]]
    lines = {l["id"]: l for l in rubric["lines"]}
    return {"headings_exact": list(secs) == HEADINGS,
            "numeric_correctness": numeric(us, refs, tol) if with_refs else None,
            "trap_coverage": traps(us, lines["trap_coverage"], tol),
            "provenance": provenance(us, refs) if with_refs else None,
            "not_disclosed": not_disclosed(us, lines["not_disclosed"]),
            "readability": readability(secs, words_ref)}


# ---------------------------------------------------------------- costs
def memo_costs(bench: Path, ledger: Path, deal: str) -> dict:
    rows = [r for r in csv.DictReader(open(bench, encoding="utf-8")) if r["deal"] == deal]
    out = {}
    for mode in ("rag", "structured"):
        rs = [r for r in rows if r["mode"] == mode]
        cached = any(r["cached"] == "True" for r in rs)
        out[mode] = {"calls": len(rs), "regenerations": sum(1 for r in rs if r["attempt"] != "0"),
                     "usd": round(sum(float(r["usd"]) for r in rs), 6),
                     "seconds": None if cached else round(sum(float(r["seconds"]) for r in rs), 3),
                     "tokens_in": sum(int(r["tokens_in"]) for r in rs),
                     "tokens_out": sum(int(r["tokens_out"]) for r in rs)}
    led = [json.loads(l) for l in open(ledger, encoding="utf-8")]
    memo_rows = [r for r in led if r["stage"].startswith("memo:") and r["usd"] > 0]
    used = sum(v["calls"] for v in out.values())
    out["ledger_memo_llm_rows"] = len(memo_rows)
    out["superseded_llm_rows"] = len(memo_rows) - used
    out["superseded_usd"] = round(sum(r["usd"] for r in memo_rows) - sum(out[m]["usd"] for m in ("rag", "structured")),
                                  6)
    out["ledger_total_usd"] = round(sum(r["usd"] for r in led), 6)
    return out


# ---------------------------------------------------------------- report
def f3(x):
    return "n/a" if x is None else f"{x:.3f}"


def ci(d):
    return f"{d['accuracy']:.3f} [{d['ci95'][0]:.3f}, {d['ci95'][1]:.3f}]"


def zero_in(d) -> bool:
    return d["ci95"][0] <= 0 <= d["ci95"][1]


def diff(d):
    return f"{d['difference']:+.3f} [{d['ci95'][0]:+.3f}, {d['ci95'][1]:+.3f}]"


def cim_size(dataroom: Path, deal: str) -> tuple[int, int]:
    """(words, pages) of the CIM's text blocks in the deal's records.jsonl."""
    from src.extract.run import cim_blocks
    bs = cim_blocks(dataroom, deal)
    return sum(len(b["text"].split()) for b in bs), len({b["page"] for b in bs})


def extraction_section(ev: dict, words: int, pages: int) -> list[str]:
    S = ("single", "staged", "staged_reconciled")
    name = {"single": "(a) single prompt", "staged": "(b) staged", "staged_reconciled": "(c) staged + reconcile"}
    A, R, C = ev["accuracy"], ev["accuracy_raw"], ev["cost_latency"]
    L = ["## 1. Vendor claim: single-prompt vs staged extraction (Part 5 item 2)", "",
         f"Source: `data/structured/kestrel/extraction_eval.json` (scores, bootstrap {ev['bootstrap']['resamples']} "
         f"resamples, seed {ev['bootstrap']['seed']}, {ev['bootstrap']['interval']} interval over the "
         f"{ev['n_fields']} gold fields), `data/bench/cs2/extract_profile.csv` (cost, latency, tokens of the record "
         f"run). Gold: `{ev.get('gold')}`.", "",
         "Accuracy = correct gold fields / gold fields, with a 95% CI. *Truth*: the tier-3 value where one exists, "
         "else what the CIM states (main score). *As stated*: the CIM's figure (comparator included where the gold "
         "gives one). *After locate* (main): numbers whose quote is found in one CIM block; *raw*: the same model "
         "outputs scored before the locate step.", "",
         "| Strategy | Truth, after locate | As stated, after locate | Truth, raw | As stated, raw | LLM calls | "
         "Cost (USD) | Latency (s) | Tokens in / out |", "|---|---|---|---|---|---|---|---|---|"]
    for s in S:
        c = C[s]
        L.append(f"| {name[s]} | {ci(A['truth'][s])} | {ci(A['as_stated'][s])} | {ci(R['truth'][s])} | "
                 f"{ci(R['as_stated'][s])} | {c['llm_calls']} | {c['usd']} | {c['seconds']} | "
                 f"{c['tokens_in']} / {c['tokens_out']} |")
    L += ["", "Paired differences (same resamples):", "",
          "| Difference | Truth, after locate | As stated, after locate | Truth, raw |", "|---|---|---|---|"]
    for k, lab in (("staged-single", "(b) − (a)"), ("staged_reconciled-staged", "(c) − (b)")):
        L.append(f"| {lab} | {diff(A['truth'][k])} | {diff(A['as_stated'][k])} | {diff(R['truth'][k])} |")
    vendor = ev["vendor_single_prompt_accuracy"]
    L += ["", f"**Selected for `deal_profile.json`: {name[ev['selected']]}** (rule fixed before the run: "
              f"{ev['selection_rule']}).", "",
          "### Reading the result", "",
          f"- The vendor reports {vendor:.0%} single-prompt accuracy on CIM data points. On Kestrel the single "
          f"prompt scored {A['truth']['single']['accuracy']:.3f} on truth and "
          f"{A['as_stated']['single']['accuracy']:.3f} as stated "
          f"({'above' if A['truth']['single']['ci95'][0] > vendor else 'not clearly above'} {vendor:.0%} on truth, "
          "by its interval). But the Kestrel CIM is "
          f"short: {words} words of text on {pages} pages (`data/dataroom/kestrel/export/records.jsonl`), so the whole "
          "document fits easily in one prompt. The vendor's figure is plausibly from long CIMs; this test cannot "
          "confirm or refute it at that length.",
          f"- Staged vs single: {diff(A['truth']['staged-single'])} on truth after locate. "
          + ("The interval includes zero, so on this one short CIM staging is not shown to be better"
             + ("; the direction favours staging." if A['truth']['staged-single']['difference'] > 0 else ".")
             if zero_in(A['truth']['staged-single']) else "The interval excludes zero."),
          f"- Staged + reconciliation vs staged: {diff(A['truth']['staged_reconciled-staged'])} on truth; the "
          f"interval {'includes' if zero_in(A['truth']['staged_reconciled-staged']) else 'excludes'} zero. "
          "Reconciliation replaced CIM figures that Part 1 flagged (discrepancy or "
          "definition_mismatch) with the tier-3 values. As stated, it scores lower by construction "
          f"({diff(A['as_stated']['staged_reconciled-staged'])}): it no longer reports what the CIM says for those "
          "fields.",
          "- Cost of (c) = (b) + the Part 1 claims extraction (`facts:claims` ledger rows, which also cover the deck "
          "and transcript, so an upper bound); its latency adds the reconcile step only (the Part 1 extraction ran "
          "in the facts stage). Token counts include instructor's validation retries, which re-send the prompt "
          f"(the single call used {C['single']['tokens_in']} input tokens for one CIM).",
          "- Locate step: quotes are matched verbatim (`Source.match: exact`), else by their tokens in order within "
          "one block (`in_order`, decision 2026-11-07 Q14). `in_order` can match across table rows: it guarantees "
          "the cited block contains the value, not that the value sits on the quoted label. Label errors show up as "
          "wrong values in the gold scoring.",
          f"- Raw vs after locate for (b): {ci(R['truth']['staged'])} raw vs {ci(A['truth']['staged'])} after "
          "locate. Raw is lower because CS1 split the page-3 highlights table into one block per cell; the staged "
          "call quoted those cells as \"FY2023 Revenue 61.8\" (gross revenue labelled revenue), which cannot be "
          "located and are dropped after locate, so the merge keeps the page-9 table's correctly labelled values.",
          ""]
    F = {s: {f["id"]: f for f in ev["fields"][s]} for s in S}
    diffs = [i for i in F["single"] if len({F[s][i]["truth"] for s in S}) > 1 or not F["single"][i]["truth"]]
    L += ["### Fields missed by at least one strategy (truth, after locate)", "",
          "| Field | (a) | (b) | (c) |", "|---|---|---|---|"]
    mark = lambda f: "✓" if f["truth"] else ("as stated" if f["as_stated"] else "✗")
    for i in diffs:
        L.append(f"| `{i}` | {mark(F['single'][i])} | {mark(F['staged'][i])} | {mark(F['staged_reconciled'][i])} |")
    L.append("")
    return L


def memo_section(scores: dict, costs: dict, rubric: dict, ex_cost: dict) -> list[str]:
    cols = ("exemplar", "rag", "structured")
    head = "| Rubric line | Exemplar | RAG-only | Structured |"
    L = ["## 2. Screening memo: RAG-only vs structured payload (Part 5 items 3-4)", "",
         "Sources: `outputs/memos/kestrel_rag.md`, `outputs/memos/kestrel_structured.md`, "
         "`data/reference/exemplar_kestrel_memo.md`, scored by `src/extract/memo_eval.py` with "
         "`evals/rubrics/screening_memo.yaml`; references resolved against `data/facts/facts.duckdb`, "
         "`outputs/qa/calcs.jsonl` and the data-room records.", "",
         head, "|---|---|---|---|"]
    g = lambda c, *k: (lambda v: v)(scores[c]) if not k else _get(scores[c], k)
    nc = lambda c: "n/a (no references)" if scores[c]["numeric_correctness"] is None else \
        f"{scores[c]['numeric_correctness']['correct']}/{scores[c]['numeric_correctness']['numbers']} " \
        f"({f3(scores[c]['numeric_correctness']['share'])})"
    tc = lambda c: f"{len(scores[c]['trap_coverage']['covered'])}/{scores[c]['trap_coverage']['n']} " \
                   f"({f3(scores[c]['trap_coverage']['share'])})"
    pv = lambda c: "n/a (no references)" if scores[c]["provenance"] is None else \
        f"{scores[c]['provenance']['resolved']}/{scores[c]['provenance']['numbered_units']} numbered units resolve " \
        f"({f3(scores[c]['provenance']['share'])}); {scores[c]['provenance']['labelled']}/" \
        f"{scores[c]['provenance']['tiered_units']} tier-3/4/5 units labelled ({f3(scores[c]['provenance']['label_share'])})"
    nd = lambda c: f"recall {len(scores[c]['not_disclosed']['expected_found'])}/" \
                   f"{len(rubric_line(rubric, 'not_disclosed')['expected'])} ({f3(scores[c]['not_disclosed']['recall'])}); " \
                   f"precision {f3(scores[c]['not_disclosed']['precision'])} over {scores[c]['not_disclosed']['marks']} marks"
    rd = lambda c: f"{scores[c]['readability']['words']} words, {scores[c]['readability']['units']} units, " \
                   f"{scores[c]['readability']['words_per_unit']:.1f} words/unit (max " \
                   f"{scores[c]['readability']['max_words_per_unit']}), length × exemplar " \
                   f"{f3(scores[c]['readability']['length_vs_exemplar'])}"
    for lab, fn in (("numeric_correctness (linted)", nc), ("trap_coverage", tc), ("provenance", pv),
                    ("not_disclosed", nd), ("readability (proxies)", rd)):
        L.append(f"| {lab} | " + " | ".join(fn(c) for c in cols) + " |")
    L.append("| headings exactly as in Part 5 | " + " | ".join("yes" if scores[c]["headings_exact"] else "no"
                                                                for c in cols) + " |")
    trap_ids = [t["id"] for t in rubric_line(rubric, "trap_coverage")["traps"]]
    L += ["", "Trap coverage by trap:", "", head.replace("Rubric line", "Trap"), "|---|---|---|---|"]
    for t in trap_ids:
        L.append(f"| {t} | " + " | ".join("✓" if t in scores[c]["trap_coverage"]["covered"] else "✗" for c in cols)
                 + " |")
    L += ["", "Trap matching is by the rubric's keywords and values within one unit, so a paraphrase or a trap "
              "split over two lines does not count (e.g. the structured memo writes \"quality of earnings\" where "
              "the T-ADDBACK group needs \"qoe\"; both memos give gross and net revenue on separate lines, while "
              "T-GROSSNET needs both values in one unit). The rubric is used as provided.", ""]
    L += ["", "Not disclosed: marks the rubric counts as wrong (items the data room does disclose):", ""]
    for c in cols:
        w = scores[c]["not_disclosed"]["wrong"]
        L.append(f"- {c}: " + ("; ".join(w) if w else "none"))
    L += ["", "Numbers that failed the numeric check:", ""]
    for c in ("rag", "structured"):
        w = scores[c]["numeric_correctness"]["wrong"]
        L.append(f"- {c}: " + ("; ".join(w) if w else "none"))
    L += ["", "Units per section (readability proxy):", "", head.replace("Rubric line", "Section"),
          "|---|---|---|---|"]
    for h in HEADINGS:
        L.append(f"| {h} | " + " | ".join(str(scores[c]["readability"]["units_per_section"][h]) for c in cols) + " |")
    L += ["", "Generation cost and latency (record run; `data/bench/cs2/memo.csv`; ledger `logs/cost_ledger.jsonl`):",
          "", "| Mode | LLM calls | Regenerations | Cost (USD) | Latency (s) | Tokens in / out |",
          "|---|---|---|---|---|---|"]
    for m, lab in (("rag", "RAG-only"), ("structured", "Structured")):
        c = costs[m]
        L.append(f"| {lab} | {c['calls']} | {c['regenerations']} | {c['usd']} | {c['seconds']} | "
                 f"{c['tokens_in']} / {c['tokens_out']} |")
    L += ["", f"The structured memo also needs the DealProfile: with extraction strategy (c) that adds "
              f"{ex_cost['usd']} USD ({ex_cost['llm_calls']} LLM calls; `extraction_eval.json`), "
              f"{round(costs['structured']['usd'] + ex_cost['usd'], 6)} USD per structured memo in total.", ""]
    return L


def rubric_line(rubric: dict, lid: str) -> dict:
    return next(l for l in rubric["lines"] if l["id"] == lid)


def _get(d, keys):
    for k in keys:
        d = d[k]
    return d


def notes(costs: dict) -> list[str]:
    return ["## 3. Notes and known issues", "",
            "- **Deal-only retrieval for the RAG memo** (decision 2026-11-08 Q2 b): the memo's retrieval keeps only "
            "`deal_id == kestrel` chunks, an extra filter beyond the brief's scope rule (a deal scope returns the "
            "deal's records plus public ones). It is used because comps are pending; revisit in Part 6. Before the "
            "filter, public 10-K text crowded into the target's sections: drafts stated another company's headcount "
            "(Payoneer's) and another company's CEO as if they were Kestrel's. Those claims were dropped by the "
            "reference check, not by design.",
            "- **Non-number entries** (decision Q1 c): entries in a claim's numbers that hold no number the linter "
            "reads (names, titles, dates) are removed and a block-id reference moves to citations; every number in "
            "the text is still linted.",
            "- **Disclosure Gaps rendering**: a claim \"X is/are not disclosed.\" with no numbers is rendered as "
            "\"- X: Not disclosed.\" (the brief's literal), by `render_gap` in `src/extract/memo.py`, with no extra "
            "LLM call.",
            "- **Comps**: no comparable-company data in either payload (Part 6 pending, decision Q6 a); both memos "
            "say valuation context is pending.",
            f"- **Superseded memo calls**: {costs['superseded_llm_rows']} paid LLM calls "
            f"({costs['superseded_usd']} USD) from the two earlier memo record runs (the first truncated at "
            "memo.max_tokens 1500, now 4096; the second before the decisions above) remain in "
            "`data/extract_cache/llm` unused, and their ledger rows stay as spent.",
            "- **Ledger understates spend slightly**: one failed call (the max_tokens truncation) is not in the "
            "ledger, because `src/llm/client.py` logs only successful calls.",
            f"- Ledger total after Part 5: {costs['ledger_total_usd']} USD (`logs/cost_ledger.jsonl`).",
            "- **Deal A**: memos for deal_a in both modes (brief §8) wait until the generator builds Deal A.",
            ""]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--deal", default="kestrel")
    ap.add_argument("--rubric", default="evals/rubrics/screening_memo.yaml")
    ap.add_argument("--memos", default="outputs/memos")
    ap.add_argument("--dataroom", default="data/dataroom")
    ap.add_argument("--facts", default="data/facts/facts.duckdb")
    ap.add_argument("--registry", default="outputs/qa/calcs.jsonl")
    ap.add_argument("--structured", default="data/structured")
    ap.add_argument("--out", default="reports/memo_comparison.md")
    args = ap.parse_args()
    params = yaml.safe_load(open("params.yaml"))
    tol = float(params["qa"]["numeric_tolerance_rel"])
    rubric = yaml.safe_load(open(args.rubric, encoding="utf-8"))
    exemplar_path = Path(rubric_line(rubric, "readability")["compare_to"])
    refs = Refs(Path(args.facts), Path(args.registry), Path(args.dataroom))
    ex_md = exemplar_path.read_text(encoding="utf-8")
    scores = {"exemplar": score_memo(ex_md, rubric, refs, tol, False, None)}
    ref_words = scores["exemplar"]["readability"]["words"]
    scores["exemplar"]["readability"]["length_vs_exemplar"] = 1.0
    for mode in ("rag", "structured"):
        md = (Path(args.memos) / f"{args.deal}_{mode}.md").read_text(encoding="utf-8")
        scores[mode] = score_memo(md, rubric, refs, tol, True, ref_words)
    ev = json.load(open(Path(args.structured) / args.deal / "extraction_eval.json", encoding="utf-8"))
    costs = memo_costs(Path("data/bench/cs2/memo.csv"), Path("logs/cost_ledger.jsonl"), args.deal)
    words, pages = cim_size(Path(args.dataroom), args.deal)
    L = ["# Memo comparison and extraction test: Kestrel (Part 5)", "",
         "Generated by `python -m src.extract.memo_eval` (no LLM calls). Kestrel only: the Deal A memos wait for "
         "Deal A. The Kestrel data room is synthetic, with a public answer key.", ""]
    L += extraction_section(ev, words, pages)
    L += memo_section(scores, costs, rubric, ev["cost_latency"]["staged_reconciled"])
    L += notes(costs)
    Path(args.out).write_text("\n".join(L).rstrip() + "\n", encoding="utf-8")
    print(json.dumps({c: {k: v for k, v in s.items() if k != "readability"} for c, s in scores.items()},
                     indent=1, default=str)[:4000])


if __name__ == "__main__":
    main()
