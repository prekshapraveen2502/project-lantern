"""Part 4 item 2: structured answers via instructor (through src/llm/client.py structured()).

The model fills ModelAnswer: claims, each with its text, a label (fact, management_claim, public_web), the numbers it
writes (each with exactly one reference: a fact_id, calc_id or claim_id from the evidence) and the evidence blocks it
relies on. Everything else is deterministic code:
  - references must be ids offered in the evidence; a claim_id (tier 4/5) only in a management_claim or public_web
    claim (brief Part 4 item 2);
  - citations (doc_id, page, bbox, block_id, tier) are built from the store, not written by the model: a fact cites
    its own source (iXBRL element, sheet block), a claim its page and bbox, a calc the sources of its input facts,
    and a cited chunk block its data-room record (page, bbox) or filing block;
  - a claim's tier is the highest tier among what it references (a derived value already inherits the lowest tier,
    i.e. the highest number, of its inputs).
The final Answer follows decision Q6: {status, reason, claims, rendered}.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

Status = Literal["answered", "partially_abstained", "not_disclosed"]
Label = Literal["fact", "management_claim", "public_web"]
RefType = Literal["fact_id", "calc_id", "claim_id"]


class ModelNumber(BaseModel):
    value_text: str = Field(description="the number exactly as written in the claim text, e.g. $59.3 million or 16.8%")
    ref_type: RefType
    ref: str = Field(description="the id of the one evidence item this number comes from")


class ModelClaim(BaseModel):
    text: str = Field(description="one sentence")
    label: Label
    numbers: list[ModelNumber] = Field(default_factory=list)
    citations: list[str] = Field(default_factory=list, description="evidence blocks as doc_id:block_id")


class ModelAnswer(BaseModel):
    abstain: bool = Field(description="true when the evidence does not answer the question")
    reason: str | None = Field(default=None, description="why the evidence is insufficient (when abstaining)")
    claims: list[ModelClaim] = Field(default_factory=list)


class NumberRef(BaseModel):
    value_text: str
    ref_type: RefType
    ref: str


class Citation(BaseModel):
    doc_id: str
    page: int | None
    bbox: list[float] | None
    block_id: str
    tier: int


class Claim(BaseModel):
    text: str
    label: Label
    tier: int
    numbers: list[NumberRef]
    citations: list[Citation]


class Answer(BaseModel):
    status: Status
    reason: str | None = None
    claims: list[Claim] = Field(default_factory=list)
    rendered: str


SYSTEM = """You answer questions for a private-equity screening team using ONLY the evidence given below.
Rules:
1. An answer is a list of short claims, one sentence each.
2. Every number you write in a claim must come from exactly one evidence item (a fact F-..., a calculation CALC-...,
   or a management/web claim C-...). List it in the claim's numbers with value_text exactly as written in the text
   and the item's id. Write the item's value, rounded to at most one decimal place in the unit you choose
   (e.g. 59300000 -> $59.3 million, 0.168 -> 16.8%). Write negative values with a minus sign, never brackets.
3. Never compute a number yourself and never use a number from your own knowledge. Do not write any other
   numbers (counts, durations, rankings) unless they are an evidence item's value. Years and fiscal-period labels
   such as FY2025 are allowed.
4. A value from a claim C-... is what management (or a website) says, not a fact: put it in its own claim labelled
   management_claim (public_web for web pages). Facts and calculations go in claims labelled fact.
5. citations: the evidence blocks (doc_id:block_id) that support the claim.
6. If the evidence does not answer the question, set abstain to true, give the reason, and write no claims."""


def _period(f: dict) -> str:
    fy = f.get("fiscal_year")
    fp = f.get("fiscal_period")
    when = f"FY{fy}" if fp == "FY" else f"at {f.get('end')}" if fp == "instant" else f"{fp} {fy}"
    return f"{when} ({f.get('start') or ''}..{f.get('end') or ''})"


def _value(v, unit) -> str:
    if v is None:
        return "no value"
    if unit == "pure":
        return f"{v!r} (= {v * 100:.4g}%)"
    return f"{v!r} {unit or ''}".strip()


def evidence_text(ev: dict) -> str:
    lines = ["FACTS (tier 1 = filed XBRL, tier 3 = seller financials)"]
    for f in ev["facts"]:
        name = f.get("metric") or (f.get("concept") or "").split(":")[-1]
        src = (f.get("citation") or {}).get("cell") or (f.get("citation") or {}).get("element_id") or ""
        lines.append(f"{f['fact_id']} | tier {f['tier']} | {f['entity_id']} | {name} | {_period(f)} | "
                     f"value {_value(f['value'], f['unit'])} | {f['doc_id']} {src}")
    lines.append("CALCULATIONS (derived from the facts above)")
    for c in ev["calcs"]:
        ins = ", ".join(t["fact_id"] for t in c["trace"])
        lines.append(f"{c['calc_id']} | tier {c['tier']} | {c['op']} {c['formula']} of {ins} | "
                     f"value {_value(c['value'], c['unit'])}")
    lines.append("MANAGEMENT / WEB CLAIMS (tier 4 = management documents, tier 5 = web; unverified)")
    for c in ev["claims"]:
        lines.append(f"{c['claim_id']} | tier {c['tier']} | {c['doc_id']} page {c.get('page')} | {c.get('metric')} "
                     f"{c.get('period_label') or ''} | value {_value(c['value'], c.get('unit'))} | \"{c['claim_text']}\"")
    lines.append("RETRIEVED BLOCKS")
    for ch in ev["chunks"]:
        cites = ", ".join(f"{ch['doc_id']}:{b}" for b in ch["block_ids"])
        lines.append(f"[{cites}] tier {ch['tier']}: {ch['text']}")
    return "\n".join(lines)


def messages(question: str, ev: dict, feedback: str | None = None) -> list[dict]:
    user = f"Question: {question}\n\nEvidence:\n{evidence_text(ev)}"
    if feedback:
        user += f"\n\nYour previous answer failed these checks; answer again and fix them:\n{feedback}"
    return [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}]


def ask(llm, params: dict, question: str, ev: dict, feedback: str | None = None) -> ModelAnswer:
    return llm.structured(ModelAnswer, messages(question, ev, feedback), model=params["llm"]["models"]["large"],
                          stage="qa", temperature=0, max_tokens=2048)


class Resolver:
    """Turns the model's references and cited blocks into Citations from the store (no model input is trusted)."""

    def __init__(self, ev: dict, records: dict):
        self.ev, self.records = ev, records
        self.facts = {f["fact_id"]: f for f in ev["facts"]}
        self.calcs = {c["calc_id"]: c for c in ev["calcs"]}
        self.claims = {c["claim_id"]: c for c in ev["claims"]}
        self.blocks = {(ch["doc_id"], b): ch for ch in ev["chunks"] for b in ch["block_ids"]}

    def known(self, ref_type: str, ref: str) -> bool:
        return ref in {"fact_id": self.facts, "calc_id": self.calcs, "claim_id": self.claims}[ref_type]

    def fact_citation(self, f: dict) -> Citation:
        c = f.get("citation") or {}
        block = c.get("element_id") or c.get("block_id") or \
            "|".join(str(c.get(k)) for k in ("concept", "context_ref", "unit_ref"))
        rec = self.records.get((f["doc_id"], block), {})
        return Citation(doc_id=f["doc_id"], page=rec.get("page"), bbox=rec.get("bbox"), block_id=block,
                        tier=int(f["tier"]))

    def ref_citations(self, ref_type: str, ref: str) -> list[Citation]:
        if ref_type == "fact_id":
            return [self.fact_citation(self.facts[ref])]
        if ref_type == "calc_id":
            return [self.fact_citation(self.facts[t["fact_id"]]) for t in self.calcs[ref]["trace"]
                    if t["fact_id"] in self.facts]
        c = self.claims[ref]
        return [Citation(doc_id=c["doc_id"], page=c.get("page"), bbox=list(c["bbox"]) if c.get("bbox") else None,
                         block_id=c["block_id"], tier=int(c["tier"]))]

    def block_citation(self, key: str) -> Citation | None:
        doc, _, block = key.partition(":")
        ch = self.blocks.get((doc, block))
        if ch is None:
            return None
        rec = self.records.get((doc, block), {})
        return Citation(doc_id=doc, page=rec.get("page", ch.get("page")), bbox=rec.get("bbox"), block_id=block,
                        tier=int(ch["tier"]))

    def ref_tier(self, ref_type: str, ref: str) -> int:
        item = {"fact_id": self.facts, "calc_id": self.calcs, "claim_id": self.claims}[ref_type][ref]
        return int(item["tier"])


def structure_problems(mc: ModelClaim, res: Resolver) -> list[str]:
    """Reference rules from brief Part 4 item 2; each problem is a sentence of linter feedback."""
    out = []
    for n in mc.numbers:
        if not res.known(n.ref_type, n.ref):
            out.append(f'"{n.value_text}" references {n.ref}, which is not a {n.ref_type} in the evidence.')
        elif n.ref_type == "claim_id" and mc.label == "fact":
            out.append(f'"{n.value_text}" comes from management/web claim {n.ref}, so the claim must be labelled '
                       f"management_claim or public_web, not fact.")
    return out


def resolve(mc: ModelClaim, res: Resolver) -> Claim:
    cites, seen = [], set()
    for n in mc.numbers:
        for c in res.ref_citations(n.ref_type, n.ref):
            if (c.doc_id, c.block_id) not in seen:
                seen.add((c.doc_id, c.block_id)); cites.append(c)
    for key in mc.citations:
        c = res.block_citation(key)
        if c is not None and (c.doc_id, c.block_id) not in seen:
            seen.add((c.doc_id, c.block_id)); cites.append(c)
    tiers = [res.ref_tier(n.ref_type, n.ref) for n in mc.numbers] or [c.tier for c in cites] or [1]
    return Claim(text=mc.text, label=mc.label, tier=max(tiers),
                 numbers=[NumberRef(**n.model_dump()) for n in mc.numbers], citations=cites)


def load_records(dataroom_dir: Path) -> dict:
    """(doc_id, block_id) -> {page, bbox, text} for every data-room record."""
    out = {}
    for f in sorted(Path(dataroom_dir).glob("*/export/records.jsonl")):
        for r in map(json.loads, open(f, encoding="utf-8")):
            out[(r["doc_id"], r["block_id"])] = {"page": r.get("page"), "bbox": r.get("bbox"), "text": r.get("text")}
    return out


def use_qa_caches(client, root: Path = Path("data/qa_cache")) -> None:
    """Decision 2026-11-06 Q2 (a): Q&A calls cache in data/qa_cache/{llm,embed}, apart from the facts and embed
    stages' caches. client.py is unchanged; its module-level paths are pointed here."""
    client.CACHE = Path(root) / "llm"
    client.EMB = Path(root) / "embed"
    client.CACHE.mkdir(parents=True, exist_ok=True)
    client.EMB.mkdir(parents=True, exist_ok=True)


def query_embedder(client, params: dict):
    """Question vectors through the same cache contract as the index (src/retrieve.py query_vector); ledger rows of a
    record run are written with stage qa."""
    from src.retrieve import query_vector

    def embed(which):
        def one(q):
            real = client.ledger
            client.ledger = lambda model, tin, tout, stage: real(model, tin, tout, "qa")
            try:
                return query_vector(q, which, params)
            finally:
                client.ledger = real
        return one
    return embed


def numeric_problems(mc: ModelClaim, res: Resolver, tol: float) -> list[str]:
    """Brief Part 4 item 3: every number in the claim text must match a value the same claim references."""
    from src.qa.lint import lint_claim
    refs = {}
    for n in mc.numbers:
        if res.known(n.ref_type, n.ref):
            item = {"fact_id": res.facts, "calc_id": res.calcs, "claim_id": res.claims}[n.ref_type][n.ref]
            refs[n.ref] = item.get("value")
    return lint_claim(mc.text, refs, tol)


def answer_question(question: str, scope: str, ctx, *, llm, records: dict, registry: Path,
                    checks=None) -> Answer:
    """Evidence -> model -> checks; a claim that fails a check is sent back once with the feedback
    (qa.max_regenerations); if it still fails it is dropped and the answer is marked partially abstained."""
    from src.calc import core as calc
    from src.qa.evidence import gather
    ev = gather(question, scope, ctx)
    res = Resolver(ev, records)
    tol = float(ctx.params["qa"]["numeric_tolerance_rel"])
    checks = checks or (structure_problems, lambda mc, r: numeric_problems(mc, r, tol))

    def problems(out: ModelAnswer) -> dict[int, list[str]]:
        found = {}
        for i, mc in enumerate(out.claims):
            p = [x for check in checks for x in check(mc, res)]
            if p:
                found[i] = p
        return found

    out = ask(llm, ctx.params, question, ev)
    bad = {} if out.abstain else problems(out)
    for _ in range(int(ctx.params["qa"]["max_regenerations"])):
        if not bad:
            break
        feedback = "\n".join(f'Claim "{out.claims[i].text}": ' + " ".join(p) for i, p in bad.items())
        out = ask(llm, ctx.params, question, ev, feedback)
        bad = {} if out.abstain else problems(out)
    if out.abstain or not out.claims:
        return not_disclosed(out.reason or "The evidence does not answer the question.")
    keep = [mc for i, mc in enumerate(out.claims) if i not in bad]
    if not keep:
        return not_disclosed("Every claim failed the numeric check after one regeneration.")
    claims = [resolve(mc, res) for mc in keep]
    calc.register([res.calcs[n.ref] for c in claims for n in c.numbers if n.ref_type == "calc_id"], registry)
    status = "partially_abstained" if bad else "answered"
    reason = f"{len(bad)} claim(s) dropped: they failed the numeric check after one regeneration." if bad else None
    return Answer(status=status, reason=reason, claims=claims, rendered=render(claims))


def not_disclosed(reason: str) -> Answer:
    return Answer(status="not_disclosed", reason=reason, claims=[], rendered="Not disclosed. " + reason)


def render(claims: list[Claim]) -> str:
    return " ".join(c.text for c in claims)
