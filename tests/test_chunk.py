"""
Part 2 items 1-2 tests: chunking strategies and table chunks (src/chunk.py) on toy blocks. Token counts use a
whitespace word count so CI needs no tokenizer files; the real stage uses the bge tokenizer (data/tokenizer, DVC).
"""
import json
import sys
from pathlib import Path

import openpyxl
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.chunk import ChunkError, chunk_set, dataroom_blocks, filing_blocks, table_chunks  # noqa: E402

count = lambda t: len(t.split())
PHRASES = {"thousands": 3, "millions": 6, "billions": 9}


def text(bid, words, section="Item 1", deal="public", doc="D1"):
    return {"doc_id": doc, "deal_id": deal, "source_type": "edgar_filing", "tier": 2, "page": None,
            "section": section, "heading": f"{section} | heading", "block_id": bid, "kind": "text",
            "text": ". ".join(f"w{i}" for i in range(words))}


def table(bid, nrows, caption="Revenue by segment (in millions)", doc="D1"):
    rows = [["", "FY2024", "FY2025"]] + [[f"line{i}", "1.0", "2.0"] for i in range(nrows)]
    return {"doc_id": doc, "deal_id": "public", "source_type": "edgar_filing", "tier": 2, "page": None,
            "section": "Item 7", "block_id": bid, "kind": "table", "rows": rows, "caption": caption,
            "scale_rows": [], "text": ""}


@pytest.mark.parametrize("strategy,overlap", [("block", 0), ("section", 0), ("recursive", 5)])
def test_no_chunk_exceeds_the_budget(strategy, overlap):
    blocks = [text("1_b0001", 30), text("1_b0002", 80), text("1_b0003", 15, section="Item 1A"), table("7_b0004", 40)]
    rows = chunk_set(blocks, strategy, 50, overlap, count, PHRASES)
    assert rows and max(r["tokens"] for r in rows) <= 50
    assert all(r["deal_id"] == "public" and r["block_ids"] for r in rows)


def test_block_strategy_merges_within_a_section_and_never_crosses_it():
    blocks = [text("1_b0001", 10), text("1_b0002", 10), text("1_b0003", 10, section="Item 1A")]
    rows = chunk_set(blocks, "block", 50, 0, count, PHRASES)
    assert [r["block_ids"] for r in rows] == [["1_b0001", "1_b0002"], ["1_b0003"]]


def test_section_differs_from_block_and_every_section_text_chunk_starts_with_its_heading():
    blocks = [text("1_b0001", 12), text("1_b0002", 12), text("1_b0003", 12), text("1_b0004", 6, section="Item 1A")]
    block = chunk_set(blocks, "block", 30, 0, count, PHRASES)
    section = chunk_set(blocks, "section", 30, 0, count, PHRASES)
    assert [r["text"] for r in block] != [r["text"] for r in section]
    for r in section:
        if r["kind"] == "text":
            assert r["text"].startswith(f"{r['section']} | heading\n") and r["tokens"] <= 30
    assert any(len(r["block_ids"]) > 1 and r["block_ids"] != ["1_b0001", "1_b0002"] for r in section)   # spans paragraphs


def test_tables_are_their_own_chunks_and_split_tables_repeat_caption_scale_and_header():
    rows = [r for r in chunk_set([text("7_b0001", 5), table("7_b0002", 30)], "block", 40, 0, count, PHRASES)
            if r["kind"] == "table"]
    assert len(rows) > 1 and all(r["block_ids"] == ["7_b0002"] for r in rows)
    for r in rows:
        lines = r["text"].splitlines()
        assert lines[:3] == ["Revenue by segment (in millions)", "Scale: millions", "FY2024 | FY2025"]   # compacted
    assert [r["table_part"] for r in rows] == list(range(1, len(rows) + 1))
    assert not any("w0" in r["text"] for r in rows)               # never merged with text


def test_rows_are_compacted_in_chunk_text_only():
    """Docling repeats a merged cell's text per spanned column and keeps empty columns (BILL 7_b0074)."""
    b = table("7_b0001", 1, caption=None)
    b["rows"] = [["", "FY2026", "FY2026", "", "FY2025", "FY2025"],
                 ["Income before taxes", "Income before taxes", "(10,131)", "(10,131)", "", "30,410"]]
    original = [list(r) for r in b["rows"]]
    chunk = table_chunks(b, 100, count, PHRASES)[0]
    assert chunk["text"].splitlines() == ["FY2026 | FY2025", "Income before taxes | (10,131) | 30,410"]
    assert b["rows"] == original                                  # the block itself is unchanged


def test_a_table_row_longer_than_the_budget_stops():
    b = table("7_b0001", 1)
    b["rows"][1] = ["x " * 60, "1", "2"]
    with pytest.raises(ChunkError, match="one table row"):
        table_chunks(b, 40, count, PHRASES)


def test_filing_markdown_becomes_paragraph_and_table_blocks(tmp_path):
    fdir = tmp_path / "XYZ" / "10-K" / "0000000000-26-000001"
    (fdir / "items").mkdir(parents=True)
    (fdir / "filing.json").write_text(json.dumps({"accn": "0000000000-26-000001"}))
    (fdir / "items" / "item_7.md").write_text("Item 7. MD&A\n\nResults (in thousands)\n\n| | FY2025 |\n|---|---|\n| Revenue | 5 |\n")
    for f in ("item_1.md", "item_1a.md"):
        (fdir / "items" / f).write_text("Text.\n")
    blocks = filing_blocks(tmp_path, ["XYZ"])
    tbl = [b for b in blocks if b["kind"] == "table"][0]
    assert (tbl["block_id"], tbl["section"], tbl["caption"]) == ("7_b0003", "Item 7", "Results (in thousands)")
    assert tbl["rows"] == [["", "FY2025"], ["Revenue", "5"]] and tbl["deal_id"] == "public"


def test_hidden_sheets_are_left_out_and_cid_tokens_removed_from_sections(tmp_path):
    export, raw = tmp_path / "export", tmp_path / "raw"
    export.mkdir(); raw.mkdir()
    wb = openpyxl.Workbook(); wb.active.title = "P&L"; d = wb.create_sheet("Draft"); d.sheet_state = "hidden"
    wb.save(raw / "financials.xlsx")
    (export / "manifest.csv").write_text("accession,file,form\nDR-kestrel-005,financials.xlsx,financials\n"
                                         "DR-kestrel-006,deck.pdf,deck\n")
    def rec(doc, bid, btype, section, kind="sheet_cell", table=None, text="t", page=None):
        return {"doc_id": doc, "deal_id": "kestrel", "source_type": "dataroom", "tier": 3, "page": page,
                "section": section, "block_id": bid, "block_type": btype, "text": text, "table": table,
                "citation": {"kind": kind}}
    recs = [rec("DR-kestrel-005", "s001", "Table", "P&L", table={"columns": ["Co – P&L"], "rows": [[None, "FY2025"], ["Net revenue", 59.3]]}),
            rec("DR-kestrel-005", "s002", "Table", "Draft", table={"columns": ["Draft"], "rows": [["Net revenue", 60.1]]}),
            rec("DR-kestrel-006", "p0002_b001", "Title", "(cid:127) Payments + software", kind="pdf_bbox", page=2)]
    (export / "records.jsonl").write_text("".join(json.dumps(r) + "\n" for r in recs))
    blocks, left = dataroom_blocks(export, raw)
    assert left == [{"doc_id": "DR-kestrel-005", "block_id": "s002", "sheet": "Draft"}]
    assert [b["block_id"] for b in blocks] == ["s001", "p0002_b001"]
    assert blocks[1]["section"] == "Payments + software" and blocks[0]["deal_id"] == "kestrel"
