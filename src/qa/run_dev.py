"""Part 4 (decision Q7 a): run the dev suite through the answering pipeline and check Part 4's acceptance.

  python -m src.qa.run_dev        (LANTERN_LLM_MODE=replay works offline once data/qa_cache holds the calls)

Writes
  outputs/qa/dev.jsonl          one line per qa task: task_id and answer (the Part 4 answer schema)
  outputs/qa/calcs.jsonl        the calculator registry (calc_ids referenced by the answers)
  outputs/qa/dev_summary.json   the acceptance checks:
    numbers     every number in every rendered answer, re-linted against the values the store holds for the
                claim's references (data/facts/facts.duckdb, the calc registry), not against the model's evidence;
                plus claims dropped by the linter
    citations   every citation resolves to an existing block (data-room record, filing block, or iXBRL tag); every
                cited number is contained in its block after normalisation (decision Q3 a: a number n in the block
                text with n or n x 10^scale within qa.numeric_tolerance_rel of the value; for an iXBRL tag, DECISIONS
                2026-11-05: (-1 if sign == "-" else 1) x text x 10^scale, 1e-9 relative)
    coverage    answerable dev questions not abstained (status answered or partially_abstained)
    lint        precision on tests/fixtures/lint/labelled_numbers.jsonl (src/qa/lint.py score_labelled)
No dev answer or expected value is used by the pipeline; expected values are not read here (Part 8 scores them).
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import yaml

from src.calc import core as calc
from src.qa import lint, store


class Blocks:
    """Existence and text of cited blocks: data-room records, filing Item blocks, iXBRL tags."""

    def __init__(self, records: dict, comps_dir: Path):
        self.records, self.comps_dir = records, Path(comps_dir)
        self._filing, self._html = None, {}

    def filing(self) -> dict:
        if self._filing is None:
            from src.chunk import filing_blocks
            tickers = sorted(p.parent.name for p in self.comps_dir.glob("*/companyfacts.json"))
            self._filing = {(b["doc_id"], b["block_id"]): b.get("text") or "" for b in filing_blocks(self.comps_dir,
                                                                                                    tickers)}
        return self._filing

    def text(self, doc: str, block: str) -> str | None:
        if (doc, block) in self.records:
            return self.records[(doc, block)].get("text") or ""
        return self.filing().get((doc, block))

    def ixbrl_exists(self, db: Path, doc: str, element_id: str) -> bool:
        """An iXBRL citation's block is the tagged element (DECISIONS 2026-11-05): the fact with this doc_id and
        element_id must exist and its tag must be in the filing's HTML."""
        con = store.connect(db)
        try:
            row = con.execute("select citation from facts where doc_id = ? and "
                              "json_extract_string(citation, '$.element_id') = ? limit 1", [doc, element_id]).fetchone()
        finally:
            con.close()
        return bool(row) and self.ixbrl_tag(json.loads(row[0])) is not None

    def ixbrl_tag(self, citation: dict):
        from lxml import etree
        path = citation.get("source_path")
        if path not in self._html:
            self._html[path] = etree.parse(path, etree.HTMLParser(huge_tree=True)) if path and Path(path).exists() \
                else None
        tree = self._html[path]
        if tree is None:
            return None
        if citation.get("element_id"):
            got = tree.xpath(f'//*[@id="{citation["element_id"]}"]')
        else:
            got = [t for t in tree.xpath(f'//*[@contextref="{citation.get("context_ref")}"]')
                   if t.get("name") == citation.get("concept") and t.get("unitref") == citation.get("unit_ref")]
        return got[0] if got else None


def ref_items(ref_type: str, ref: str, db: Path, calcs: dict) -> list[dict]:
    """The stored items behind a reference: a fact or claim, or a calc's input facts."""
    if ref_type == "calc_id":
        c = calcs.get(ref)
        return [store.get(db, t["fact_id"]) for t in c["trace"]] if c else []
    got = store.get(db, ref)
    return [got] if got else []


def ref_value(ref_type: str, ref: str, db: Path, calcs: dict):
    if ref_type == "calc_id":
        return (calcs.get(ref) or {}).get("value")
    return (store.get(db, ref) or {}).get("value")


def contains(blocks: Blocks, item: dict, tol: float) -> tuple[bool, str]:
    """Is the item's value shown in its cited block? Returns (ok, where)."""
    from src.facts.xbrl_read import citation_value
    cit = item.get("citation") or {}
    if item.get("kind") == "fact" and cit.get("kind") in ("ixbrl_element", "ixbrl_context"):
        tag = blocks.ixbrl_tag(cit)
        if tag is None:
            return False, f"{item['doc_id']}: iXBRL tag not found"
        v = citation_value("".join(tag.itertext()).strip(), int(tag.get("scale") or 0), tag.get("sign"))
        ok = v is not None and abs(v - item["value"]) <= 1e-9 * max(1.0, abs(item["value"]))
        return ok, f"{item['doc_id']}:{cit.get('element_id')}"
    block = cit.get("block_id") if item.get("kind") == "fact" else item.get("block_id")
    text = blocks.text(item["doc_id"], block)
    if text is None:
        return False, f"{item['doc_id']}:{block} not found"
    scale = 10 ** int(item.get("scale") or 0)
    shown = -item["value"] if (item.get("lineage") or {}).get("sign_flipped") else item["value"]   # Part 1: costs
    ok = any(lint.close(n.value, shown, tol) or lint.close(n.value * scale, shown, tol)          # stored positive
             for n in lint.numbers(text))
    return ok, f"{item['doc_id']}:{block}"


def check_answer(ans: dict, db: Path, calcs: dict, blocks: Blocks, tol: float) -> dict:
    out = {"numbers": 0, "numbers_resolved": 0, "unresolved": [], "citations": 0, "citations_resolved": 0,
           "cited_numbers": 0, "cited_numbers_contained": 0, "citation_problems": []}
    claim_numbers = 0
    for c in ans["claims"]:
        refs = {n["ref"]: ref_value(n["ref_type"], n["ref"], db, calcs) for n in c["numbers"]}
        nums = lint.numbers(c["text"])
        claim_numbers += len(nums)
        problems = lint.lint_claim(c["text"], refs, tol)
        out["numbers"] += len(nums)
        out["numbers_resolved"] += len(nums) - len(problems)
        out["unresolved"] += problems
        for cit in c["citations"]:
            out["citations"] += 1
            exists = bool(cit["block_id"]) and (blocks.text(cit["doc_id"], cit["block_id"]) is not None or
                                                blocks.ixbrl_exists(db, cit["doc_id"], cit["block_id"]))
            out["citations_resolved"] += exists
            if not exists:
                out["citation_problems"].append(f"{cit['doc_id']}:{cit['block_id']} does not resolve")
        for n in c["numbers"]:
            for item in ref_items(n["ref_type"], n["ref"], db, calcs):
                ok, where = contains(blocks, item, tol)
                out["cited_numbers"] += 1
                out["cited_numbers_contained"] += ok
                if not ok:
                    out["citation_problems"].append(f"{n['ref']}: value {item['value']!r} not shown in {where}")
    rendered = len(lint.numbers(ans["rendered"]))
    if rendered != claim_numbers:                                  # labels add no numbers; a reason may not either
        out["numbers"] += rendered - claim_numbers
        out["unresolved"].append(f"rendered answer has {rendered} numbers, its claims {claim_numbers}")
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description="Part 4: run the dev suite and check acceptance")
    ap.add_argument("--tasks", default="evals/tasks/dev.jsonl")
    ap.add_argument("--out", default="outputs/qa/dev.jsonl")
    ap.add_argument("--summary", default="outputs/qa/dev_summary.json")
    ap.add_argument("--calcs", default="outputs/qa/calcs.jsonl")
    ap.add_argument("--labels", default="tests/fixtures/lint/labelled_numbers.jsonl")
    args = ap.parse_args()
    from src.llm import client
    from src.qa.answer import answer_question, load_records, query_embedder, use_qa_caches
    from src.qa.evidence import Context
    params = yaml.safe_load(Path("params.yaml").read_text(encoding="utf-8"))
    use_qa_caches(client)
    db, tol = Path("data/facts/facts.duckdb"), float(params["qa"]["numeric_tolerance_rel"])
    ctx = Context(params, facts_db=db, index_dir=Path("data/index"), comps_dir=Path("data/edgar_comps"),
                  dataroom_dir=Path("data/dataroom"), vocab_path=Path("config/metrics_vocab.yaml"),
                  embed_query_for=query_embedder(client, params))
    records = load_records(Path("data/dataroom"))
    tasks = [json.loads(l) for l in open(args.tasks, encoding="utf-8") if l.strip()]
    tasks = [t for t in tasks if t.get("type") == "qa"]
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    answers = []
    for t in tasks:
        a = answer_question(t["question"], t["scope"], ctx, llm=client, records=records, registry=Path(args.calcs))
        answers.append({"task_id": t["task_id"], "answer": a.model_dump()})
        print(f"[INFO] {t['task_id']}: {a.status} | {a.rendered[:110]}")
    Path(args.out).write_text("".join(json.dumps(a, sort_keys=True) + "\n" for a in answers), encoding="utf-8")

    calcs = calc.load(Path(args.calcs))
    blocks = Blocks(records, Path("data/edgar_comps"))
    per = {a["task_id"]: check_answer(a["answer"], db, calcs, blocks, tol) for a in answers}
    total = lambda k: sum(p[k] for p in per.values())
    status = {s: sum(a["answer"]["status"] == s for a in answers) for s in ("answered", "partially_abstained",
                                                                           "not_disclosed")}
    answerable = [t for t in tasks if t.get("answerable")]
    by_id = {a["task_id"]: a["answer"] for a in answers}
    covered = [t["task_id"] for t in answerable if by_id[t["task_id"]]["status"] != "not_disclosed"]
    summary = {
        "tasks": len(tasks), "status": status,
        "numbers": {"in_answers": total("numbers"), "linted_and_resolved": total("numbers_resolved"),
                    "unresolved": {k: p["unresolved"] for k, p in per.items() if p["unresolved"]},
                    "answers_with_dropped_claims": status["partially_abstained"]},
        "citations": {"total": total("citations"), "resolve_to_a_block": total("citations_resolved"),
                      "cited_numbers": total("cited_numbers"),
                      "cited_numbers_contained_in_block": total("cited_numbers_contained"),
                      "problems": {k: p["citation_problems"] for k, p in per.items() if p["citation_problems"]}},
        "coverage": {"answerable": len(answerable), "not_abstained": len(covered),
                     "rate": round(len(covered) / len(answerable), 4) if answerable else None,
                     "abstained": sorted(set(t["task_id"] for t in answerable) - set(covered))},
        "lint": lint.score_labelled(args.labels),
    }
    Path(args.summary).write_text(json.dumps(summary, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print(f"[INFO] numbers {summary['numbers']['linted_and_resolved']}/{summary['numbers']['in_answers']} resolved; "
          f"citations {summary['citations']['resolve_to_a_block']}/{summary['citations']['total']} resolve, "
          f"cited numbers {summary['citations']['cited_numbers_contained_in_block']}/"
          f"{summary['citations']['cited_numbers']} contained; coverage {len(covered)}/{len(answerable)}; "
          f"lint precision {summary['lint']['precision']}")


if __name__ == "__main__":
    main()
