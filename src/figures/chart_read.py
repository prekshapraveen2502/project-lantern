"""Part 6 item 4: chart reads with the pinned vision model -> chart_read claims, reconciled against the tables.

For every Figure in links.jsonl with chart_read_target true:
  1. one structured call through src/llm/client.py (llm.models.vision, temperature 0, cache data/figures_cache/llm,
     ledger stage figures:chart_read). The user message holds the caption text from links.jsonl and the figure's
     PNG (data/dataroom/{deal}/export/work/figures/, written by ingest_dataroom) as a base64 image_url part, so the
     cache key covers the image bytes. The model returns what the chart shows: its printed title, the metric (an
     enum of metrics_vocab.yaml names, or null), the scale and unit flags, and one point per category (the axis
     label and the number as printed, and whether it was read from a data label or estimated from the axis);
  2. code turns each point into a claim the way Part 1 does (src/facts/claims.py to_value and claim_period): value
     in base units, period from the category label (FYyyyy, CYyyyy, 'Qn FYyyyy'; a point with any other label is
     logged and dropped), unit from the metric's unit_type. method is chart_read and the tier is the document's
     (brief Part 6 item 4); the citation is the Figure block (page, bbox);
  3. each claim is reconciled with Part 1's reconcile_claim against the deal's tier-3 facts in facts.duckdb, with
     reconcile.tolerance_rel and reconcile.high_gap_rel. A discrepancy is a chart-vs-table discrepancy.
Output, one line per claim: the claim's columns (the facts.duckdb claims schema) plus "reconcile" (the brief
Section 8 fields: metric, period, claim_id, fact_id, status, gap, severity) and "read_from".
"""
from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path
from typing import Literal, Optional

from pydantic import BaseModel, Field, create_model

from src.facts.claims import SCALES, ClaimsError, claim_period, metric_menu, to_value
from src.facts.reconcile import FIELDS, fiscal_year_end, index_facts, reconcile_claim

STAGE = "figures:chart_read"
SYSTEM = (
    "You read the values of one chart from a confidential data-room document. Report what the chart itself shows: "
    "its title as printed, the metric it plots, the scale (thousand / million / billion as stated in the title, "
    "axis or caption), and one point per category on the category axis, in axis order. For each point copy the "
    "category label exactly as printed and give the number exactly as printed in the data label above or beside "
    "the bar or marker (read_from data_label). Only when a point has no printed label, estimate it from the value "
    "axis (read_from axis). Do not compute, round, or correct values, and do not use any knowledge beyond the "
    "image and the caption given. Set metric only when the title or caption states it; otherwise null."
)


def response_model(vocab: dict) -> type[BaseModel]:
    names = tuple(sorted(vocab["metrics"]))
    point = create_model(
        "ChartPoint",
        category=(str, Field(description="the category-axis label exactly as printed, e.g. FY2025")),
        number=(float, Field(description="the value as printed in the data label: 62.0 for '62.0', 3120 for "
                                         "'3,120.0'")),
        read_from=(Literal["data_label", "axis"], Field(description="data_label if a printed label gives the "
                                                                     "value, axis if estimated from the axis")),
    )
    return create_model(
        "ChartRead",
        title=(Optional[str], Field(description="the chart's title as printed, or null")),
        metric=(Optional[Literal[names]], Field(description="the metrics_vocab name the chart plots, or null")),
        scale_word=(Literal[tuple(SCALES)], Field(description="thousand / million / billion if stated, else none")),
        is_percent=(bool, Field(description="true if the values are percentages")),
        is_currency=(bool, Field(description="true if the values are amounts of money")),
        points=(list[point], ...),
    )


def figure_png(link: dict, root: Path = Path("data/dataroom")) -> Path:
    return root / link["deal_id"] / "export" / "work" / "figures" / link["figure_path"]


def messages(link: dict, png: bytes, vocab: dict) -> list[dict]:
    url = "data:image/png;base64," + base64.b64encode(png).decode("ascii")
    text = (f"Metrics:\n{metric_menu(vocab)}\n\nCaption (the nearest heading above the chart): "
            f"{link['caption_text'] or '(none)'}\n\nRead the chart in the image.")
    return [{"role": "system", "content": SYSTEM},
            {"role": "user", "content": [{"type": "text", "text": text},
                                          {"type": "image_url", "image_url": {"url": url, "detail": "high"}}]}]


def chart_claim_id(doc_id: str, block_id: str, category: str) -> str:
    return "C-" + hashlib.sha256(f"{doc_id}|{block_id}|chart_read|{category}".encode()).hexdigest()[:12]


def to_claims(read, link: dict, manifest_row: dict, png_sha: str, vocab: dict, fye: str, latest_fy: int, tol: int,
              extractor: str, log: list[dict]) -> list[dict]:
    meta = vocab["metrics"].get(read.metric) if read.metric else None
    if meta:
        unit = {"currency": "USD", "percent": "pure", "count": "count", "ratio": "pure"}[meta["unit_type"]]
        period_type = meta["period"]
    else:
        unit = "pure" if read.is_percent else ("USD" if read.is_currency else "count")
        period_type = "duration"
    rows = []
    for p in read.points:
        try:
            per = claim_period(p.category.strip(), fye, latest_fy, period_type, tol)
        except ClaimsError as e:
            log.append({"block_id": link["block_id"], "doc_id": link["doc_id"], "category": p.category,
                        "dropped": str(e)})
            continue
        value, scale = to_value(p.number, read.scale_word, read.is_percent)
        citation = {"kind": "pdf_bbox", "doc_id": link["doc_id"], "block_id": link["block_id"], "page": link["page"],
                    "bbox": link["bbox"]}
        lineage = {"parents": [f"{link['doc_id']}:{link['block_id']}"], "source_sha256": manifest_row["sha256"],
                   "figure_sha256": png_sha, "caption_block_id": link["caption_block_id"],
                   "chart_title": read.title, "category": p.category, "number_as_written": p.number,
                   "scale_word": read.scale_word, "is_percent": read.is_percent, "is_currency": read.is_currency}
        label = read.title or link["caption_text"] or "chart"
        rows.append({
            "claim_id": chart_claim_id(link["doc_id"], link["block_id"], p.category), "deal_id": link["deal_id"],
            "entity_id": link["deal_id"], "metric": read.metric, "value": value, "unit": unit, "scale": scale,
            "period_type": per["period_type"], "start": per["start"], "end": per["end"],
            "fiscal_year": per["fiscal_year"], "fiscal_period": per["fiscal_period"],
            "period_label": per["period_label"], "period_basis": per["period_basis"], "qualifier": [],
            "claim_text": f"{label}: {p.category} {p.number:g}", "method": "chart_read",
            "tier": int(manifest_row["tier"]), "source_type": "dataroom", "doc_id": link["doc_id"],
            "block_id": link["block_id"], "page": link["page"], "bbox": link["bbox"],
            "citation": json.dumps(citation, sort_keys=True), "extractor": extractor,
            "lineage": json.dumps(lineage, sort_keys=True), "comparator": "eq", "read_from": p.read_from})
    return rows


def reconcile(claims: list[dict], facts: list[dict], tol: float, high: float) -> list[dict]:
    fye, idx = fiscal_year_end(facts), index_facts(facts)
    out = []
    for c in claims:
        rec = reconcile_claim(c, idx, fye, tol, high)
        out.append({**c, "reconcile": {k: rec[k] for k in FIELDS}})
    return out
