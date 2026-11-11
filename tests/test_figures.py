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


def test_nested_figures_are_all_linked_and_only_the_inner_one_is_read():
    rows = L.link_deal(deck([blk("t", "Text", [64, 62, 225, 88], "Growth story"),
                             blk("outer", "Figure", [117, 110, 674, 612]),
                             blk("inner", "Figure", [131, 153, 629, 389])]))
    o, i = one(rows, "outer"), one(rows, "inner")
    assert o["contains"] == ["inner"] and not o["chart_read_target"]
    assert i["contained_in"] == ["outer"] and i["chart_read_target"]
    assert o["caption_block_id"] == i["caption_block_id"] == "t"


def test_two_boxes_covering_each_other_are_read_once():
    rows = L.link_deal(deck([blk("a", "Figure", [100, 100, 500, 300]), blk("b", "Figure", [101, 101, 500, 300])]))
    assert sum(r["chart_read_target"] for r in rows) == 1
