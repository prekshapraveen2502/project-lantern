"""
Part 0 tests: ingest_dataroom record cleaning and the mixed text-layer/scanned PDF path. No data room
needed: each test builds tiny records, or tiny CS1 stage outputs in a temporary folder.
"""
import csv
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.ingest import dataroom as dr  # noqa: E402
from src.ingest.records import write_jsonl  # noqa: E402

SHA = "0" * 64
ROW = {"stem": "doc", "accession": "DR-kestrel-001", "deal_id": "kestrel", "source_type": "dataroom",
       "file": "doc.pdf", "form": "cim", "tier": 4, "sha256": SHA}
BOX = [70.8, 239.97, 283.12, 252.28]
ZERO_WIDTH = [70.8, 217.78, 70.8, 229.78]          # the Kestrel CIM bullet, p2 b005


def block(block_id, block_type, text, bbox=BOX, table=None):
    rec = dr.base_record(ROW, "kestrel", "Toy Co", "pdfplumber", "0")
    rec.update({"page": 1, "section": None, "block_id": block_id, "block_type": block_type, "bbox": bbox,
                "text": text, "table": table,
                "citation": {"kind": "pdf_bbox", "doc_id": ROW["accession"], "page": 1, "bbox": bbox}})
    return rec


def test_zero_size_glyph_only_block_is_skipped():
    kept, skipped = dr.clean_and_filter([block("p0001_b001", "Text", "(cid:127)", ZERO_WIDTH)])
    assert kept == []
    assert skipped == [{"doc_id": "DR-kestrel-001", "page": 1, "block_id": "p0001_b001",
                        "reason": "zero_size_glyph_only"}]


def test_zero_size_block_with_content_is_kept():
    """Only empty blocks are skipped; a bad bbox on a block with content still fails validation."""
    kept, skipped = dr.clean_and_filter([block("p0001_b001", "Text", "(cid:127) Net revenue 59.3", ZERO_WIDTH)])
    assert skipped == [] and kept[0]["text"] == "Net revenue 59.3"


@pytest.mark.parametrize("block_type", ["Text", "Title", "List"])
@pytest.mark.parametrize("text", ["(cid:3) ", "", "   "])
def test_text_types_empty_after_cleaning_are_skipped_whatever_bbox(block_type, text):
    kept, skipped = dr.clean_and_filter([block("p0001_b002", block_type, text)])
    assert kept == [] and skipped[0]["reason"] == "empty_after_cleaning"


def test_figure_and_table_never_skipped_for_empty_text():
    recs = [block("p0001_b003", "Figure", None),
            block("p0001_b004", "Table", "", table={"status": "no_valid_table"})]
    kept, skipped = dr.clean_and_filter(recs)
    assert skipped == [] and [r["block_id"] for r in kept] == ["p0001_b003", "p0001_b004"]


def test_cid_removed_from_text_and_flagged():
    kept, _ = dr.clean_and_filter([block("p0001_b005", "Text", "(cid:127) Net revenue grew 21.8%\n(cid:127) TPV 4,870"),
                                   block("p0001_b006", "Text", "No glyphs here")])
    assert kept[0]["text"] == "Net revenue grew 21.8%\nTPV 4,870"
    assert "127" not in kept[0]["text"]
    assert kept[0]["ingest_flags"] == ["unmapped_glyphs_removed"]
    assert kept[1]["ingest_flags"] == []


def test_cid_removed_from_table_cells_and_flagged_once():
    layout_table = {"status": "accepted", "rows": [["(cid:127) Net revenue", "59.3"], ["Gross revenue", "96.4"]]}
    xlsx_table = {"sheet": "P&L", "range": "P&L!A1:B2", "columns": ["(cid:1)Metric", None],
                  "rows": [["Net revenue", 59.3]]}
    kept, _ = dr.clean_and_filter([block("p0001_b007", "Table", "(cid:127) Net revenue 59.3", table=layout_table),
                                   block("p0001_b008", "Table", "Metric", table=xlsx_table)])
    assert kept[0]["table"]["rows"] == [["Net revenue", "59.3"], ["Gross revenue", "96.4"]]
    assert kept[0]["ingest_flags"] == ["unmapped_glyphs_removed"]          # text and cells: one flag
    assert kept[1]["table"]["columns"] == ["Metric", None]
    assert kept[1]["table"]["rows"] == [["Net revenue", 59.3]]             # numbers untouched
    assert kept[1]["table"]["range"] == "P&L!A1:B2"
    assert kept[1]["ingest_flags"] == ["unmapped_glyphs_removed"]


def test_mixed_pdf_text_page_then_scanned_page(tmp_path):
    """Page 1 has a text layer, page 2 is a scan. Before the fix, page 1 overwrote the 'ocr' params
    argument with a bool and page 2 failed on ocr["ocr_line_overlap"] (TypeError)."""
    work = tmp_path / "work"
    (work / "parsed").mkdir(parents=True)
    (work / "layout").mkdir()
    with open(work / "parsed" / "ocr_log.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["doc_id", "stem", "page", "triggered"])
        w.writeheader()
        w.writerows([{"doc_id": "DR-kestrel-001", "stem": "doc", "page": 1, "triggered": "False"},
                     {"doc_id": "DR-kestrel-001", "stem": "doc", "page": 2, "triggered": "True"}])
    words = [{"page": 2, "text": t, "bbox": [x, 100.0, x + 40, 112.0], "conf": 90.0}
             for t, x in (("Change", 72.0), ("of", 116.0), ("control", 160.0))]
    (work / "parsed" / "doc.words.jsonl").write_text("".join(json.dumps(w) + "\n" for w in words))
    layout = {"page": 1, "section": None, "block_id": "p0001_b001", "type": "Text", "bbox": BOX,
              "text": "Master services agreement", "table": None, "ocr": False, "ocr_conf": None,
              "model": "tf_efficientdet_d0", "source": "model", "score": 0.9, "figure_path": None}
    (work / "layout" / "doc.blocks.jsonl").write_text(json.dumps(layout) + "\n")

    recs = dr.pdf_records(ROW, "kestrel", "Toy Co", work, {}, {"ocr_line_overlap": 0.5, "ocr_para_gap_ratio": 1.0},
                          {"pdfplumber": "0", "tesseract": "0"})
    assert [(r["page"], r["ocr"]) for r in recs] == [(1, False), (2, True)]
    assert recs[1]["text"] == "Change of control"
    assert write_jsonl(recs, tmp_path / "records.jsonl") == 2               # both validate
