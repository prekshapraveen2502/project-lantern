"""Part 5 item 3: the screening memo, two ways from one template (config/memo_template.yaml):
  rag         RAG-only (decision 2026-11-07 Q5 b): per section, the template's query is retrieved in the deal's scope
              (pinned collection, hybrid, retrieval.k); the model sees the chunks plus only the facts and claims
              located in the retrieved blocks, and calcs over those facts. No vocabulary lookup.
  structured  the structured payload (decision Q6 a): data/structured/{deal}/deal_profile.json (numbers with a fact,
              calc or claim reference only; decision Q7), the deal's tier-3 facts, its claims, and calcs over the
              facts. No retrieved chunks. Comps: none (Part 6 pending); the template says so.
One call per section (decision Q8), the same system prompt and instruction in both modes; every claim goes through
the Part 4 checks (references exist, management numbers only in management claims, src/qa/lint.py numeric check);
a failing section is regenerated once with the feedback (qa.max_regenerations); claims that still fail are dropped
and counted. Each claim is one line: text, tier label (Part 4 tier_label) and its references in brackets.
Calls cache in data/extract_cache (llm, embed), ledger stages memo:rag and memo:structured; record mode writes
per-call timings to data/bench/cs2/memo.csv. Calcs used are registered in outputs/qa/calcs.jsonl (GET /calc).
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path

import yaml
from pydantic import BaseModel, Field

from src.calc import core as calc
from src.qa import store
from src.qa.answer import (ModelClaim, Resolver, load_records, numeric_problems, resolve, structure_problems,
                           tier_label, evidence_text)
from src.qa.evidence import DERIVED_INPUTS, Context, calcs_for, doc_forms
from src.qa.lint import numbers as lint_numbers
from src.extract.profile import DealProfile, iter_nums

BENCH = Path("data/bench/cs2/memo.csv")
BENCH_FIELDS = ["run_at", "deal", "mode", "section", "attempt", "cached", "seconds", "tokens_in", "tokens_out", "usd"]


class MemoSection(BaseModel):
    claims: list[ModelClaim] = Field(default_factory=list)
    gaps: list[str] = Field(default_factory=list, description="Disclosure Gaps section only: each item not "
                                                              "disclosed, in a few words, without numbers")


SYSTEM = """You write one section of a private-equity screening memo using ONLY the evidence given below.
Rules:
1. The section is a list of short claims, one sentence each, in plain language.
2. Every number you write in a claim must come from exactly one evidence item (a fact F-..., a calculation CALC-...,
   or a management/web claim C-...). List it in the claim's numbers with value_text exactly as written in the text
   and the item's id. Write the item's value, rounded to at most one decimal place in the unit you choose
   (e.g. 59300000 -> $59.3 million, 0.168 -> 16.8%). Write a negative value with the minus sign before the
   currency sign, never brackets.
3. Never compute a number yourself and never use a number from your own knowledge. Do not write any other
   numbers (counts, durations, rankings) unless they are an evidence item's value. Years and fiscal-period labels
   such as FY2025 are allowed.
4. A value from a claim C-... is what management (or a website) says, not a fact: put it in its own claim labelled
   management_claim (public_web for web pages). Facts and calculations go in claims labelled fact. When a
   management claim and the seller financials differ, write both, each in its own claim.
5. citations: the evidence blocks (doc_id:block_id) that support the claim, when the evidence shows blocks.
6. Write only what the evidence supports. If the evidence says nothing for this section, return no claims.
7. gaps is used only in the Disclosure Gaps section.
8. numbers lists only numbers; names, titles and dates are not numbers (cite their blocks in citations).
9. Write counts that are part of a metric's name in words (top ten customers, top partner)."""


def section_messages(sec: dict, evidence: str, feedback: str | None = None) -> list[dict]:
    user = (f"Section: {sec['heading']}\nWhat to cover: {sec['instruction']}\n\nEvidence:\n{evidence}")
    if feedback:
        user += f"\n\nYour previous section failed these checks; write it again and fix them:\n{feedback}"
    return [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}]


# ---------------------------------------------------------------- evidence
def rag_evidence(query: str, scope: str, ctx: Context) -> dict:
    """Retrieval only: chunks, facts and claims located in the retrieved blocks, calcs over those facts.
    Decision 2026-11-08 Q2 (b): beyond the brief's scope rule (deal + public), the memo keeps only the deal's own
    chunks, because comps are pending (Part 6; to revisit there): public 10-K text otherwise crowded into the target's
    sections. src/retrieve.py is unchanged: the scoped hybrid ranking runs over the whole collection and the top
    retrieval.k chunks with deal_id == scope are kept, in fused order."""
    from src.retrieve import rank
    allowed = [scope]                                              # facts and claims: the deal's own as well
    ranked = rank(query, scope, collection=ctx.collection, which=ctx.pinned["which"], params=ctx.params,
                  depth=ctx.collection.count(), embed_query=ctx.embed_query)
    ids = [cid for cid, _ in ranked["hybrid"] if ranked["meta"][cid]["deal_id"] == scope][
        :int(ctx.params["retrieval"]["k"])]
    docs = dict(zip(*[ctx.collection.get(ids=ids, include=["documents"])[x] for x in ("ids", "documents")])) \
        if ids else {}
    chunks = []
    for cid in ids:
        m = ranked["meta"][cid]
        chunks.append({"chunk_id": cid, "doc_id": m["doc_id"], "deal_id": m["deal_id"], "tier": m["tier"],
                       "page": None if m["page"] == -1 else m["page"], "block_ids": json.loads(m["block_ids"]),
                       "text": docs.get(cid, "")})
    blocks = list(dict.fromkeys((c["doc_id"], b) for c in chunks for b in c["block_ids"]))
    q_in = lambda xs: ",".join("?" * len(xs))
    con = store.connect(ctx.facts_db)
    try:
        facts, seen = [], set()
        for doc, b in blocks:
            cur = con.execute(f"select * from facts where deal_id in ({q_in(allowed)}) and doc_id = ? and "
                              "json_extract_string(citation, '$.block_id') = ? order by fact_id", allowed + [doc, b])
            for row in cur.fetchall():
                f = store._row(cur, row)
                if f["fact_id"] not in seen:
                    seen.add(f["fact_id"]); facts.append(f)
        cur = con.execute(f"select * from claims where deal_id in ({q_in(allowed)}) order by claim_id", allowed)
        claims = [c for c in (store._row(cur, r) for r in cur.fetchall()) if (c["doc_id"], c["block_id"]) in set(blocks)]
    finally:
        con.close()
    return {"chunks": chunks, "facts": facts, "claims": claims, "calcs": calcs_over(facts)}


def calcs_over(facts: list[dict]) -> list[dict]:
    """Part 4 calcs_for over the facts that have a vocabulary metric (unmapped seller rows have neither metric nor
    concept, so they have no growth or ratio to compute)."""
    return calcs_for([f for f in facts if f["metric"]], list(DERIVED_INPUTS))


def profile_text(p: DealProfile) -> tuple[str, int]:
    """The DealProfile as payload text; numbers without a reference are left out (decision Q7), counted."""
    lines = ["DEAL PROFILE (structured extraction from the CIM; reconciled with the seller financials)"]
    left_out = 0
    for area in ("overview", "business_model", "customers", "process"):
        for name, v in getattr(p, area):
            if v is None or v == [] or hasattr(v, "value"):
                continue
            vals = [x.name if hasattr(x, "name") else x for x in v] if isinstance(v, list) else v
            lines.append(f"{area}.{name}: {vals}")
    if p.history.fiscal_year_end:
        lines.append(f"history.fiscal_year_end: {p.history.fiscal_year_end}")
    for m in p.management:
        lines.append(f"management: {m.name}, {m.title}, joined {m.joined_year}; {m.background or ''}")
    for k in p.kpis:
        if k.definition_note:
            lines.append(f"kpi {k.metric} definition note: {k.definition_note}")
    for fid, metric, n in iter_nums(p):
        if not n.ref:
            left_out += 1
            continue
        line = f"{fid}: value {n.value!r} {n.unit} -> reference {n.ref} (CIM: \"{n.as_written}\")"
        if n.reconcile:
            line += (f"; reconciled: management claimed {n.reconcile.claimed_value!r} ({n.reconcile.claim_id}), "
                     f"status {n.reconcile.status}")
        lines.append(line)
    if p.disclosure_gaps:
        lines.append(f"CIM states not disclosed: {p.disclosure_gaps}")
    lines.append("COMPARABLE COMPANIES: pending; no comps data.")
    return "\n".join(lines), left_out


def structured_evidence(deal: str, db: Path, profile_path: Path) -> tuple[dict, str, int]:
    con = store.connect(db)
    try:
        out = []
        for table in ("facts", "claims"):
            cur = con.execute(f"select * from {table} where deal_id = ? order by 1", [deal])
            out.append([store._row(cur, r) for r in cur.fetchall()])
    finally:
        con.close()
    facts = [f for f in out[0] if int(f["tier"]) <= 3]
    p = DealProfile.model_validate_json(Path(profile_path).read_text(encoding="utf-8"))
    text, left_out = profile_text(p)
    return {"chunks": [], "facts": facts, "claims": out[1], "calcs": calcs_over(facts)}, \
        text, left_out


# ---------------------------------------------------------------- generation and checks
BLOCK_KEY = re.compile(r"^[A-Za-z0-9_.\-]+:[A-Za-z0-9_.\-]+$")


def clean(out: MemoSection) -> int:
    """Decision 2026-11-08 Q1 (c), in place: a numbers entry whose value_text holds no number the linter reads (a
    name, title or date) carries no number, so it is removed; a block-id reference in it moves to citations. Every
    number in the claim text is still linted. Returns how many entries were removed."""
    moved = 0
    for mc in out.claims:
        keep = []
        for n in mc.numbers:
            if lint_numbers(n.value_text):
                keep.append(n)
                continue
            moved += 1
            if BLOCK_KEY.match(n.ref) and n.ref not in mc.citations:
                mc.citations.append(n.ref)
        mc.numbers = keep
    return moved


def section_problems(out: MemoSection, res: Resolver, tol: float) -> dict[int, list[str]]:
    bad = {}
    for i, mc in enumerate(out.claims):
        p = structure_problems(mc, res) + numeric_problems(mc, res, tol)
        if p:
            bad[i] = p
    for j, g in enumerate(out.gaps):
        if lint_numbers(g):
            bad[1000 + j] = [f'gap "{g}" contains a number; write it without numbers.']
    return bad


def feedback(out: MemoSection, bad: dict) -> str:
    say = lambda i: f'Gap "{out.gaps[i - 1000]}": ' if i >= 1000 else f'Claim "{out.claims[i].text}": '
    return "\n".join(say(i) + " ".join(p) for i, p in bad.items())


def refs_of(c) -> list[str]:
    out = [n.ref for n in c.numbers] + [f"{x.doc_id}:{x.block_id}" for x in c.citations]
    return list(dict.fromkeys(out))


GAP_SENTENCE = re.compile(r"^(?:the\s+)?(.+?)\s+(?:is|are)\s+not\s+disclosed\b.*$", re.I)


def render_gap(c, forms: dict) -> str | None:
    """Disclosure Gaps (brief: bullets with the literal "Not disclosed."): a number-free claim "X is/are not
    disclosed ..." is rendered as "- X: Not disclosed. (tier label) [refs]" (the tier label as in Part 4 item 4);
    anything else is rendered as a normal bullet."""
    m = GAP_SENTENCE.match(c.text.strip())
    if c.numbers or not m:
        return None
    item = m.group(1).strip()
    label, refs = tier_label(c, forms), refs_of(c)
    return (f"- {item[0].upper() + item[1:]}: Not disclosed." + (f" ({label})" if label else "")
            + (f" [{'; '.join(refs)}]" if refs else ""))


def render_line(c, forms: dict, bullet: bool) -> str:
    label = tier_label(c, forms)
    text = c.text.strip()
    if label:
        end = text[-1] if text and text[-1] in ".!?" else "."
        text = f"{text.rstrip('.!?')} ({label}){end}"
    refs = refs_of(c)
    line = f"{text} [{'; '.join(refs)}]" if refs else text
    return f"- {line}" if bullet else line


class Runner:
    def __init__(self, client, params: dict, deal: str):
        self.client, self.params, self.deal, self.rows = client, params, deal, []
        self.run_at = datetime.now(timezone.utc).isoformat(timespec="seconds")

    def call(self, mode: str, sec: dict, attempt: int, msgs: list[dict]) -> MemoSection:
        c, model, mt = self.client, self.params["llm"]["models"]["large"], int(self.params["memo"]["max_tokens"])
        key = c.cache_key(c.PROVIDER, model, msgs, temperature=0, max_tokens=mt, schema=MemoSection.model_json_schema())
        cached = (c.CACHE / f"{key}.json").exists()
        n0 = sum(1 for _ in open(c.LEDGER)) if Path(c.LEDGER).exists() else 0
        t0 = time.perf_counter()
        out = c.structured(MemoSection, msgs, model=model, stage=f"memo:{mode}", temperature=0, max_tokens=mt)
        secs = time.perf_counter() - t0
        new = [json.loads(l) for l in open(c.LEDGER)][n0:] if Path(c.LEDGER).exists() else []
        self.rows.append({"run_at": self.run_at, "deal": self.deal, "mode": mode, "section": sec["heading"],
                          "attempt": attempt, "cached": cached, "seconds": round(secs, 3),
                          "tokens_in": sum(r["in"] for r in new), "tokens_out": sum(r["out"] for r in new),
                          "usd": round(sum(r["usd"] for r in new), 6)})
        return out

    def write_bench(self) -> None:
        if self.client.MODE != "record":
            return
        old = list(csv.DictReader(open(BENCH, encoding="utf-8"))) if BENCH.exists() else []
        keep = [r for r in old if r["deal"] != self.deal]
        BENCH.parent.mkdir(parents=True, exist_ok=True)
        with open(BENCH, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=BENCH_FIELDS)
            w.writeheader()
            w.writerows(keep + self.rows)


def write_memo(mode: str, deal: str, template: dict, evidence_for, runner: Runner, records: dict, forms: dict,
               tol: float, registry: Path, codename: str) -> tuple[str, dict]:
    """Returns (markdown, stats). evidence_for(section) -> (ev dict, extra payload text)."""
    lines = [f"# {template['title'].format(codename=codename, mode=template['modes'][mode])}", ""]
    stats = {"sections": {}, "dropped_claims": 0, "dropped_gaps": 0, "regenerated_sections": 0,
             "number_entries_removed": 0}
    calcs_used = []
    for sec in template["sections"]:
        ev, extra = evidence_for(sec)
        res = Resolver(ev, records)
        text = (extra + "\n\n" if extra else "") + evidence_text(ev)
        out = runner.call(mode, sec, 0, section_messages(sec, text))
        stats["number_entries_removed"] += clean(out)
        bad = section_problems(out, res, tol)
        for attempt in range(1, int(runner.params["qa"]["max_regenerations"]) + 1):
            if not bad:
                break
            stats["regenerated_sections"] += 1
            out = runner.call(mode, sec, attempt, section_messages(sec, text, feedback(out, bad)))
            stats["number_entries_removed"] += clean(out)
            bad = section_problems(out, res, tol)
        keep = [resolve(mc, res) for i, mc in enumerate(out.claims) if i not in bad]
        gaps = [g for j, g in enumerate(out.gaps) if 1000 + j not in bad]
        stats["dropped_claims"] += sum(1 for i in bad if i < 1000)
        stats["dropped_gaps"] += sum(1 for i in bad if i >= 1000)
        calcs_used += [res.calcs[n.ref] for c in keep for n in c.numbers if n.ref_type == "calc_id"]
        lines += [f"## {sec['heading']}", ""]
        if sec["style"] == "gaps":
            body = [f"- {g.strip().rstrip('.')}: Not disclosed." for g in gaps] + \
                   [render_gap(c, forms) or render_line(c, forms, True) for c in keep]
        else:
            body = [render_line(c, forms, sec["style"] == "bullets") for c in keep]
        lines += (body or ["Not disclosed."]) + [""]
        stats["sections"][sec["heading"]] = {"claims": len(keep), "gaps": len(gaps), "failed": len(bad),
                                             "evidence": {k: len(ev[k]) for k in ("chunks", "facts", "claims",
                                                                                  "calcs")}}
    calc.register(calcs_used, registry)
    return "\n".join(lines).rstrip() + "\n", stats


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--deal", required=True)
    ap.add_argument("--modes", nargs="+", default=["rag", "structured"], choices=["rag", "structured"])
    ap.add_argument("--template", default="config/memo_template.yaml")
    ap.add_argument("--dataroom", default="data/dataroom")
    ap.add_argument("--facts", default="data/facts/facts.duckdb")
    ap.add_argument("--index", default="data/index")
    ap.add_argument("--comps", default="data/edgar_comps")
    ap.add_argument("--structured", default="data/structured")
    ap.add_argument("--registry", default="outputs/qa/calcs.jsonl")
    ap.add_argument("--out", default="outputs/memos")
    args = ap.parse_args()
    from src.llm import client
    from src.retrieve import query_vector
    root = Path("data/extract_cache")
    client.CACHE, client.EMB = root / "llm", root / "embed"
    client.CACHE.mkdir(parents=True, exist_ok=True)
    client.EMB.mkdir(parents=True, exist_ok=True)
    params = yaml.safe_load(open("params.yaml"))
    template = yaml.safe_load(open(args.template, encoding="utf-8"))
    deal = args.deal
    codename = json.load(open(Path(args.dataroom) / "variant.json"))["deals"][deal]
    records, forms = load_records(Path(args.dataroom)), doc_forms(Path(args.dataroom))
    tol = float(params["qa"]["numeric_tolerance_rel"])
    cap = float(params["budget"]["usd_cap_per_task"])
    runner = Runner(client, params, deal)
    Path(args.out).mkdir(parents=True, exist_ok=True)
    summary = {}
    for mode in args.modes:
        if mode == "rag":
            def embed_for(which):
                def one(q):
                    real = client.ledger
                    client.ledger = lambda model, tin, tout, stage: real(model, tin, tout, "memo:rag")
                    try:
                        return query_vector(q, which, params)
                    finally:
                        client.ledger = real
                return one
            ctx = Context(params, facts_db=Path(args.facts), index_dir=Path(args.index), comps_dir=Path(args.comps),
                          dataroom_dir=Path(args.dataroom), vocab_path=Path("config/metrics_vocab.yaml"),
                          embed_query_for=embed_for)
            evidence_for = lambda sec: (rag_evidence(sec["query"], deal, ctx), "")
            left_out = None
        else:
            ev, text, left_out = structured_evidence(deal, Path(args.facts),
                                                     Path(args.structured) / deal / "deal_profile.json")
            evidence_for = lambda sec: (ev, text)
        md, stats = write_memo(mode, deal, template, evidence_for, runner, records, forms, tol, Path(args.registry),
                               codename)
        usd = sum(r["usd"] for r in runner.rows if r["mode"] == mode)
        if usd > cap:
            raise RuntimeError(f"{deal} memo {mode}: {usd} USD > budget.usd_cap_per_task {cap}")
        (Path(args.out) / f"{deal}_{mode}.md").write_text(md, encoding="utf-8")
        summary[mode] = {**stats, "profile_numbers_left_out": left_out}
    runner.write_bench()
    print(json.dumps(summary, indent=1, sort_keys=True))


if __name__ == "__main__":
    main()
