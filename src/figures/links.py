"""Part 6 item 3: link every Figure block to its caption by a documented rule (no model).

Input: a deal's Part 0 records (data/dataroom/{deal}/export/records.jsonl). For every Figure block:

  1. Candidates: Title, Text and List blocks on the same page of the same document, with non-empty text, whose
     x-range overlaps the figure's, that are not running headers/footers. A running line is one whose first
     RUNNING_PREFIX characters (lower-case, whitespace collapsed) start a block on at least half of the
     document's pages (catches "Confidential – Project ..." even where layout clipped it).
  2. Above: a candidate whose top is above the figure's top and whose bottom is at most CAPTION_MAX_GAP_PT above
     the figure's top (a title overlapping the figure's top edge has gap 0). The nearest wins; on a tie, a Title
     wins, then the lower block_id.
  3. Else below: the same, for a candidate whose top is at or below the figure's bottom, within the gap.
  4. Else no caption (rule "none"); logged, never guessed.
Nesting: a Figure covering at least NEST_COVER of another Figure on the same page is its container. Every Figure is
linked; only a Figure inside no other is chart_read_target, so a chart detected twice (an outer box and a box
inside it) is read once (Part 6 item 4). The outer box is read because on Kestrel's deck the detector's inner box
cuts off the chart's own title and clips the last bar.
"""
from __future__ import annotations

import json
import re
from collections import defaultdict
from pathlib import Path

CAPTION_TYPES = ("Title", "Text", "List")
CAPTION_MAX_GAP_PT = 72.0     # one inch
RUNNING_PREFIX = 20
NEST_COVER = 0.8
TYPE_RANK = {"Title": 0, "Text": 1, "List": 2}


def norm(text: str | None) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().lower())


def running_prefixes(records: list[dict]) -> set[str]:
    """Prefixes that start a block on at least half of a document's pages (per document)."""
    pages: dict[str, set[int]] = defaultdict(set)
    seen: dict[tuple[str, str], set[int]] = defaultdict(set)
    for r in records:
        if r.get("page") is None:
            continue
        pages[r["doc_id"]].add(r["page"])
        t = norm(r.get("text"))
        if len(t) >= RUNNING_PREFIX:
            seen[(r["doc_id"], t[:RUNNING_PREFIX])].add(r["page"])
    return {f"{d}|{p}" for (d, p), pg in seen.items() if len(pg) * 2 >= len(pages[d]) and len(pages[d]) > 1}


def is_running(r: dict, running: set[str]) -> bool:
    t = norm(r.get("text"))
    return len(t) >= RUNNING_PREFIX and f"{r['doc_id']}|{t[:RUNNING_PREFIX]}" in running


def x_overlap(a: list[float], b: list[float]) -> bool:
    return min(a[2], b[2]) > max(a[0], b[0])


def cover(inner: list[float], outer: list[float]) -> float:
    """Share of inner's area inside outer."""
    w = max(0.0, min(inner[2], outer[2]) - max(inner[0], outer[0]))
    h = max(0.0, min(inner[3], outer[3]) - max(inner[1], outer[1]))
    area = (inner[2] - inner[0]) * (inner[3] - inner[1])
    return w * h / area if area > 0 else 0.0


def caption_for(fig: dict, page_blocks: list[dict], running: set[str]) -> tuple[dict | None, str, float | None]:
    fb = fig["bbox"]
    cands = [b for b in page_blocks if b["block_type"] in CAPTION_TYPES and norm(b.get("text"))
             and b.get("bbox") and x_overlap(b["bbox"], fb) and not is_running(b, running)]
    above = [(max(0.0, fb[1] - b["bbox"][3]), b) for b in cands if b["bbox"][1] < fb[1]]
    above = [(g, b) for g, b in above if g <= CAPTION_MAX_GAP_PT]
    below = [(b["bbox"][1] - fb[3], b) for b in cands if b["bbox"][1] >= fb[3]]
    below = [(g, b) for g, b in below if g <= CAPTION_MAX_GAP_PT]
    for rule, group in (("above", above), ("below", below)):
        if group:
            g, b = min(group, key=lambda x: (x[0], TYPE_RANK[x[1]["block_type"]], x[1]["block_id"]))
            return b, rule, round(g, 2)
    return None, "none", None


def link_deal(records: list[dict]) -> list[dict]:
    running = running_prefixes(records)
    by_page: dict[tuple[str, int], list[dict]] = defaultdict(list)
    for r in records:
        if r.get("page") is not None:
            by_page[(r["doc_id"], r["page"])].append(r)
    out = []
    for r in records:
        if r["block_type"] != "Figure":
            continue
        blocks = by_page[(r["doc_id"], r["page"])]
        figs = [b for b in blocks if b["block_type"] == "Figure" and b["block_id"] != r["block_id"]]
        contained_in = sorted(b["block_id"] for b in figs if cover(r["bbox"], b["bbox"]) >= NEST_COVER
                              and not (cover(b["bbox"], r["bbox"]) >= NEST_COVER and r["block_id"] < b["block_id"]))
        # two boxes covering each other: the lower block_id is the outer one, so exactly one of them is read
        contains = sorted(b["block_id"] for b in figs if cover(b["bbox"], r["bbox"]) >= NEST_COVER
                          and not (cover(r["bbox"], b["bbox"]) >= NEST_COVER and b["block_id"] < r["block_id"]))
        cap, rule, gap = caption_for(r, blocks, running)
        out.append({"deal_id": r["deal_id"], "doc_id": r["doc_id"], "page": r["page"], "block_id": r["block_id"],
                    "bbox": r["bbox"], "tier": r["tier"], "figure_path": r.get("figure_path"),
                    "caption_block_id": cap["block_id"] if cap else None,
                    "caption_text": (cap.get("text") or "").strip() if cap else None,
                    "caption_rule": rule, "caption_gap_pt": gap,
                    "contained_in": contained_in, "contains": contains, "chart_read_target": not contained_in})
    return out


def load_records(path: Path) -> list[dict]:
    return [json.loads(l) for l in open(path, encoding="utf-8") if l.strip()]
