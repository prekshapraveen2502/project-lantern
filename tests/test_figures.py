"""Part 6 item 3: the caption rule (synthetic blocks, no files)."""
from src.figures import links as L


def blk(bid, btype, bbox, text="", page=1, doc="DR-x-001"):
    return {"deal_id": "kestrel", "doc_id": doc, "page": page, "block_id": bid, "block_type": btype, "bbox": bbox,
            "text": text, "tier": 4, "figure_path": None}


FOOT = "Confidential – Project X | Deck"


def deck(page_blocks):
    """Page 1 holds the blocks under test; pages 2-3 carry the running footer only."""
    return page_blocks + [blk(f"p{p}_f", "Text", [50, 570, 250, 578], FOOT, page=p) for p in (2, 3)]


def one(rows, bid):
    return next(r for r in rows if r["block_id"] == bid)


def test_nearest_block_above_wins_and_a_title_overlapping_the_top_edge_counts():
    rows = L.link_deal(deck([blk("t1", "Title", [70, 75, 140, 94], "Growth"),
                             blk("t2", "Title", [220, 112, 410, 125], "Net revenue by year (USD m)"),
                             blk("f", "Figure", [97, 121, 510, 312])]))
    r = one(rows, "f")
    assert (r["caption_block_id"], r["caption_rule"], r["caption_gap_pt"]) == ("t2", "above", 0.0)


def test_running_footer_is_never_a_caption_even_when_clipped():
    rows = L.link_deal(deck([blk("f", "Figure", [100, 300, 500, 560]),
                             blk("foot", "Text", [50, 570, 130, 578], "Confidential – Project")]))
    assert one(rows, "f")["caption_rule"] == "none" and one(rows, "f")["caption_block_id"] is None


def test_below_is_used_only_without_an_above_candidate_and_gap_is_limited():
    rows = L.link_deal(deck([blk("f", "Figure", [100, 100, 500, 300]),
                             blk("src", "Text", [100, 310, 400, 320], "Source: company data")]))
    assert (one(rows, "f")["caption_block_id"], one(rows, "f")["caption_rule"]) == ("src", "below")
    far = L.link_deal(deck([blk("f", "Figure", [100, 200, 500, 300]),
                            blk("t", "Title", [100, 50, 400, 60], "Far title")]))
    assert one(far, "f")["caption_rule"] == "none"


def test_a_block_not_overlapping_horizontally_is_not_a_caption():
    rows = L.link_deal(deck([blk("f", "Figure", [300, 100, 500, 300]),
                             blk("t", "Title", [50, 80, 200, 95], "Left column title")]))
    assert one(rows, "f")["caption_rule"] == "none"


def test_nested_figures_are_all_linked_and_only_the_outer_one_is_read():
    rows = L.link_deal(deck([blk("t", "Text", [64, 62, 225, 88], "Growth story"),
                             blk("outer", "Figure", [117, 110, 674, 612]),
                             blk("inner", "Figure", [131, 153, 629, 389])]))
    o, i = one(rows, "outer"), one(rows, "inner")
    assert o["contains"] == ["inner"] and o["chart_read_target"]
    assert i["contained_in"] == ["outer"] and not i["chart_read_target"]
    assert o["caption_block_id"] == i["caption_block_id"] == "t"


def test_two_boxes_covering_each_other_are_read_once():
    rows = L.link_deal(deck([blk("a", "Figure", [100, 100, 500, 300]), blk("b", "Figure", [101, 101, 500, 300])]))
    assert sum(r["chart_read_target"] for r in rows) == 1


# ---- Part 6 item 4: chart reads (no LLM: a ChartRead built by hand) ----
import json  # noqa: E402

import yaml  # noqa: E402

from src.figures import chart_read as CR  # noqa: E402

VOCAB = yaml.safe_load(open("config/metrics_vocab.yaml", encoding="utf-8"))
LINK = {"deal_id": "kestrel", "doc_id": "DR-kestrel-006", "page": 4, "block_id": "p0004_b002", "bbox": [1, 2, 3, 4],
        "figure_path": "x.png", "caption_block_id": "p0004_b001", "caption_text": "Net revenue"}
MANIFEST = {"accession": "DR-kestrel-006", "tier": "4", "sha256": "ab" * 32}
FACTS = [{"fact_id": f"F-rev{y}", "metric": "revenue", "fiscal_year": y, "fiscal_period": "FY", "value": v,
          "end": f"{y}-12-31"} for y, v in ((2024, 48.7e6), (2025, 59.3e6))]


def chart(points, metric="revenue"):
    rm = CR.response_model(VOCAB)
    return rm(title="Net revenue (USD m)", metric=metric, scale_word="million", is_percent=False, is_currency=True,
              points=[{"category": c, "number": n, "read_from": "data_label"} for c, n in points])


def test_chart_points_become_chart_read_claims_with_the_documents_tier():
    log = []
    rows = CR.to_claims(chart([("FY2024", 48.7), ("FY2025", 62.0), ("Jun 2025", 5.4)]), LINK, MANIFEST, "cd" * 32,
                        VOCAB, "1231", 2025, 7, "openai:gpt-4.1-mini", log)
    assert [r["period_label"] for r in rows] == ["FY2024", "FY2025"] and len(log) == 1   # "Jun 2025" dropped, logged
    r = rows[1]
    assert (r["method"], r["tier"], r["value"], r["unit"], r["metric"]) == ("chart_read", 4, 62.0e6, "USD", "revenue")
    assert json.loads(r["citation"]) == {"kind": "pdf_bbox", "doc_id": "DR-kestrel-006", "block_id": "p0004_b002",
                                         "page": 4, "bbox": [1, 2, 3, 4]}
    assert r["claim_id"] != rows[0]["claim_id"]


def test_chart_vs_table_reconcile_flags_the_bar_that_disagrees():
    rows = CR.to_claims(chart([("FY2024", 48.7), ("FY2025", 62.0)]), LINK, MANIFEST, "cd" * 32, VOCAB, "1231", 2025,
                        7, "x", [])
    out = CR.reconcile(rows, FACTS, 0.01, 0.05)
    assert [o["reconcile"]["status"] for o in out] == ["match", "discrepancy"]
    rec = out[1]["reconcile"]
    assert rec["fact_id"] == "F-rev2025" and rec["severity"] == "medium" and abs(rec["gap"] - (62.0 / 59.3 - 1)) < 1e-12
    assert set(rec) == {"metric", "period", "claim_id", "fact_id", "status", "gap", "severity"}


def test_the_message_carries_the_image_and_caption():
    msgs = CR.messages(LINK, b"\x89PNG fake", VOCAB)
    parts = msgs[1]["content"]
    assert parts[1]["image_url"]["url"].startswith("data:image/png;base64,") and "Net revenue" in parts[0]["text"]
