"""Part 1 item 5: management claims (CIM, deck, transcript) -> claims table, tier 4.

One extraction call per CIM page / deck slide and one for the transcript (Text and Title blocks only; Table and
Figure blocks, and Text blocks that are table-cell fragments, are left out and counted). Every call goes through src/llm/client.py (structured, temperature 0,
cached). The model returns, per claim: a verbatim quote, the metric (an enum of metrics_vocab.yaml names, or null),
the number as written with scale/percent/currency flags, a comparator, the period as written and qualifiers. Code
then locates the quote in a block (block_id, page, bbox, character span), converts the number to base units, and
derives the period from the deal's fiscal year end. A quote that cannot be located is dropped and logged.
The answer key is never read here.
"""
from __future__ import annotations

import csv
import hashlib
import json
import re
from datetime import date, timedelta
from pathlib import Path
from typing import Literal, Optional

from pydantic import BaseModel, Field, create_model

from src.facts.periods import fiscal_labels, fye_date, quarter_ends

SCALES = {"none": 0, "thousand": 3, "million": 6, "billion": 9}
QUALIFIERS = ("adjusted", "run-rate", "pro forma", "LTM", "FY")
COMPARATORS = ("eq", "approx", "lt", "le", "gt", "ge")
FY_RE, CY_RE, Q_RE = re.compile(r"^FY(\d{4})$"), re.compile(r"^CY(\d{4})$"), re.compile(r"^Q([1-4]) FY(\d{4})$")


class ClaimsError(ValueError):
    """A case the item 5 rules do not cover: the run stops."""


def response_model(vocab: dict) -> type[BaseModel]:
    names = tuple(sorted(vocab["metrics"]))
    claim = create_model(
        "Claim",
        quote=(str, Field(description="the shortest clause containing exactly one number, copied verbatim from one "
                                      "block")),
        metric=(Optional[Literal[names]], Field(description="the metrics_vocab name the clause states, or null")),
        number=(float, Field(description="the number as written: 4.9 for '$4.9bn', 31 for '31%', 14 for 'fourteen'")),
        scale_word=(Literal[tuple(SCALES)], Field(description="thousand / million / billion if written, else none")),
        is_percent=(bool, Field(description="true if the number is a percentage")),
        is_currency=(bool, Field(description="true if the number is an amount of money")),
        comparator=(Literal[COMPARATORS], Field(description="eq exact; approx for about/nearly/roughly; lt/le for "
                                                            "less than / no more than; gt/ge for over / at least")),
        period_label=(Optional[str], Field(description="FYyyyy, CYyyyy or 'Qn FYyyyy' if the text states it, else "
                                                       "null")),
        qualifiers=(list[Literal[QUALIFIERS]], Field(description="adjusted, run-rate (annualized or x12 figures, "
                                                                 "including when a footnote or note on the page says "
                                                                 "so), pro forma, LTM (trailing twelve months), FY")),
    )
    return create_model("PageClaims", claims=(list[claim], ...))


SYSTEM = (
    "You extract numeric claims that management makes about the company in a confidential data-room document. "
    "A claim is a number stated together with what it measures (revenue, growth, volume, customers, employees, "
    "shares of revenue, retention, take rate, EBITDA and its add-backs, contract terms, team sizes and so on). "
    "Do not extract dates, years, ages, page or reference numbers, document identifiers, or numbers whose subject "
    "is not stated in the same block. Copy each quote exactly as it appears in one block. Write numbers given in "
    "words as digits in `number`. If the speaker corrects a number, extract only the corrected one and quote the "
    "correction. Set `metric` only when the clause states that metric; otherwise null. Use only the text given."
)


def metric_menu(vocab: dict) -> str:
    lines = []
    for name, m in sorted(vocab["metrics"].items()):
        extra = "; ".join(x for x in (m.get("description"), ", ".join(m.get("synonyms", []))) if x)
        lines.append(f"- {name}: {extra}")
    return "\n".join(lines)


def page_messages(blocks: list[dict], vocab: dict) -> list[dict]:
    text = "\n".join(f"[{b['block_id']}] {' '.join((b['text'] or '').split())}" for b in blocks)
    return [{"role": "system", "content": SYSTEM},
            {"role": "user", "content": f"Metrics:\n{metric_menu(vocab)}\n\nBlocks:\n{text}"}]


MONTH_WORDS = re.compile(r"\b(jan(uary)?|feb(ruary)?|mar(ch)?|apr(il)?|may|june?|july?|aug(ust)?|sep(t(ember)?)?|"
                         r"oct(ober)?|nov(ember)?|dec(ember)?)\b", re.I)


def is_grid_fragment(text: str | None) -> bool:
    """A Text block that is only a table cell split out by layout ("442.4", "Apr 2025", "(23.5)"): nothing is left
    once month names, digits, punctuation and spaces are removed. Left out like Table blocks."""
    return not re.sub(r"[^a-z]", "", MONTH_WORDS.sub("", (text or "").lower()))


def keep_block(r: dict) -> bool:
    return r["block_type"] in ("Text", "Title") and not is_grid_fragment(r["text"])


def locate(quote: str, blocks: list[dict]) -> tuple[dict, int, int] | None:
    """(block, start, end) of the quote inside one block's original text, whitespace-insensitive."""
    tokens = quote.split()
    if not tokens:
        return None
    pat = re.compile(r"\s+".join(map(re.escape, tokens)))
    for b in blocks:
        m = pat.search(b["text"] or "")
        if m:
            return b, m.start(), m.end()
    return None


def to_value(number: float, scale_word: str, is_percent: bool) -> tuple[float, int]:
    """(value in base units, scale exponent of the number as shown). Percent -> fraction, scale 0."""
    if is_percent:
        return number / 100.0, 0
    s = SCALES[scale_word]
    return number * 10 ** s, s


def claim_period(label: str | None, fye: str, latest_fy: int, period_type: str, tol: int) -> dict:
    """Period fields from the label as written; no label -> the deal's latest fiscal year, flagged as assumed."""
    out = {"period_label": label, "period_assumed": False}
    if label is None:
        fy, out["period_assumed"], basis, q = latest_fy, True, "unknown", None
    elif m := FY_RE.match(label):
        fy, basis, q = int(m.group(1)), "fiscal", None
    elif m := Q_RE.match(label):
        fy, basis, q = int(m.group(2)), "fiscal", int(m.group(1))
    elif m := CY_RE.match(label):
        y = int(m.group(1))
        start, end = date(y, 1, 1), date(y, 12, 31)
        fy_, fp = fiscal_labels(start, end, fye, tol)
        return {**out, "period_basis": "calendar", "period_type": "duration", "start": str(start), "end": str(end),
                "fiscal_year": fy_, "fiscal_period": fp}
    else:
        raise ClaimsError(f"period label {label!r} is not FYyyyy, CYyyyy or 'Qn FYyyyy'")
    if q is None:
        end = fye_date(fy, fye)
        start = None if period_type == "instant" else fye_date(fy - 1, fye) + timedelta(days=1)
    else:
        qe = quarter_ends(fy, fye)
        end = qe[q - 1]
        start = None if period_type == "instant" else (qe[q - 2] if q > 1 else fye_date(fy - 1, fye)) + timedelta(days=1)
    fy_, fp = fiscal_labels(start, end, fye, tol)
    return {**out, "period_basis": basis, "period_type": period_type, "start": str(start) if start else None,
            "end": str(end), "fiscal_year": fy_, "fiscal_period": fp}


def claim_id(doc_id: str, block_id: str, start: int, end: int) -> str:
    return "C-" + hashlib.sha256(f"{doc_id}|{block_id}|{start}-{end}".encode()).hexdigest()[:12]


def deal_calendar(facts: list[dict]) -> tuple[str, int]:
    """(fiscal year end MMDD, latest fiscal year) from the deal's tier-3 FY facts."""
    fy = [f for f in facts if f["fiscal_period"] == "FY"]
    ends = {f["end"][5:].replace("-", "") for f in fy}
    if len(ends) != 1:
        raise ClaimsError(f"deal facts have no single fiscal year end: {sorted(ends)}")
    return ends.pop(), max(f["fiscal_year"] for f in fy)


def extract_deal(deal: str, root: Path, vocab: dict, facts: list[dict], params: dict) -> tuple[list[dict], dict]:
    """Claims rows for every tier-4 document of the deal (manifest tier 4: CIM, deck, transcript)."""
    from src.llm import client                                   # reads params.yaml; replay needs no key

    export = root / deal / "export"
    manifest = [m for m in csv.DictReader(open(export / "manifest.csv", encoding="utf-8")) if m["tier"] == "4"]
    records = [json.loads(line) for line in open(export / "records.jsonl", encoding="utf-8")]
    fye, latest_fy = deal_calendar(facts)
    tol = int(params["facts"]["fye_tolerance_days"])
    model = params["llm"]["models"]["large"]
    cap = float(params["budget"]["usd_cap_per_task"])
    rmodel = response_model(vocab)
    rows, log = [], {"fiscal_year_end": fye, "latest_fiscal_year": latest_fy, "documents": {}}
    for m in manifest:
        if m["deal_id"] != deal:
            raise ClaimsError(f"{m['file']}: manifest deal_id {m['deal_id']} != folder {deal}")
        doc = m["accession"]
        recs = [r for r in records if r["doc_id"] == doc]
        groups: dict = {}
        for r in recs:
            if keep_block(r):
                groups.setdefault(r["page"], []).append(r)
        dlog = {"file": m["file"], "calls": len(groups), "claims": 0, "dropped": [],
                "blocks_left_out": sum(1 for r in recs if not keep_block(r))}
        spent0 = client.spent()
        for page in sorted(groups, key=lambda p: (p is None, p)):
            blocks = groups[page]
            out = client.structured(rmodel, page_messages(blocks, vocab), model=model, stage="facts:claims",
                                    temperature=0)
            for c in out.claims:
                hit = locate(c.quote, blocks)
                if hit is None:
                    dlog["dropped"].append({"page": page, "quote": c.quote, "reason": "quote not found in a block"})
                    continue
                b, s, e = hit
                rows.append(make_row(c, b, s, e, m, deal, vocab, fye, latest_fy, tol, model, params))
                dlog["claims"] += 1
        dlog["usd"] = round(client.spent() - spent0, 6)
        if dlog["usd"] > cap:
            raise ClaimsError(f"{m['file']}: extraction cost {dlog['usd']} USD > budget.usd_cap_per_task {cap}")
        log["documents"][doc] = dlog
    ids = [r["claim_id"] for r in rows]
    if len(ids) != len(set(ids)):
        raise ClaimsError("two claims share one quote span (claim_id collision)")
    return rows, log


def make_row(c, b: dict, s: int, e: int, m: dict, deal: str, vocab: dict, fye: str, latest_fy: int, tol: int,
             model: str, params: dict) -> dict:
    metric = c.metric
    meta = vocab["metrics"].get(metric) if metric else None
    value, scale = to_value(c.number, c.scale_word, c.is_percent)
    if meta:
        unit = {"currency": "USD", "percent": "pure", "count": "count", "ratio": "pure"}[meta["unit_type"]]
        period_type = meta["period"]
    else:
        unit = "pure" if c.is_percent else ("USD" if c.is_currency else "count")
        period_type = "duration"
    per = claim_period(c.period_label, fye, latest_fy, period_type, tol)
    cit = b["citation"]
    if cit["kind"] == "text_span":
        citation = {"kind": "text_span", "doc_id": m["accession"], "block_id": b["block_id"],
                    "line_start": cit["line_start"], "line_end": cit["line_end"],
                    "char_start": cit["char_start"] + s, "char_end": cit["char_start"] + e}
    else:
        citation = {"kind": cit["kind"], "doc_id": m["accession"], "block_id": b["block_id"], "page": b["page"],
                    "bbox": b["bbox"], "char_start": s, "char_end": e}
    lineage = {"parents": [f"{m['accession']}:{b['block_id']}"], "source_sha256": m["sha256"],
               "number_as_written": c.number, "scale_word": c.scale_word, "is_percent": c.is_percent,
               "is_currency": c.is_currency, "period_assumed": per["period_assumed"]}
    return {
        "claim_id": claim_id(m["accession"], b["block_id"], s, e), "deal_id": deal, "entity_id": deal,
        "metric": metric, "value": value, "unit": unit, "scale": scale, "period_type": per["period_type"],
        "start": per["start"], "end": per["end"], "fiscal_year": per["fiscal_year"],
        "fiscal_period": per["fiscal_period"], "period_label": per["period_label"],
        "period_basis": per["period_basis"], "qualifier": list(c.qualifiers), "claim_text": b["text"][s:e],
        "method": "text", "tier": int(m["tier"]), "source_type": "dataroom", "doc_id": m["accession"],
        "block_id": b["block_id"], "page": b["page"], "bbox": b["bbox"],
        "citation": json.dumps(citation, sort_keys=True), "extractor": f"{params['llm']['provider']}:{model}",
        "lineage": json.dumps(lineage, sort_keys=True), "comparator": c.comparator,
    }
