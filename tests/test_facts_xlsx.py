"""
Part 1 item 4 tests: seller xlsx -> tier-3 facts (src/facts/xlsx_read.py), following DECISIONS.md 2026-11-05.
CI tests build small workbooks in a temporary folder; the Kestrel tests read the real data room (DVC) and are
skipped when it is absent.
"""
import json
import sys
from pathlib import Path

import openpyxl
import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.facts.vocab import load_vocab  # noqa: E402
from src.facts.xlsx_read import XlsxError, decimals_from_format, read_workbook  # noqa: E402

VOCAB = load_vocab(ROOT / "config" / "metrics_vocab.yaml")
M1 = "#,##0.0;(#,##0.0);\"—\""
NOTES = [["Fiscal year ends June 30. FY2025 = July 1, 2024 to June 30, 2025."]]


def make(tmp_path, sheets: dict, hidden=()):
    """sheets: {title: rows}; a cell is a value or (value, number_format)."""
    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    for title, rows in sheets.items():
        ws = wb.create_sheet(title)
        for r, row in enumerate(rows, 1):
            for c, cell in enumerate(row, 1):
                value, fmt = cell if isinstance(cell, tuple) else (cell, None)
                if value is not None:
                    ws.cell(r, c, value)
                    if fmt:
                        ws.cell(r, c).number_format = fmt
        if title in hidden:
            ws.sheet_state = "hidden"
    p = tmp_path / "financials.xlsx"
    wb.save(p)
    return p


def read(path, sheets):
    blocks = {t: f"s{i:03d}" for i, t in enumerate(sheets, 1)}
    return read_workbook(path, "DR-kestrel-005", "kestrel", VOCAB, 7, blocks, "0" * 64, "test")


def pl(*rows, unit="USD millions"):
    return [["Co – P&L"], [unit, "Fiscal year ended June 30"], [None, "FY2024", "FY2025"], *rows]


def facts_at(rows, cell):
    return [r for r in rows if json.loads(r["citation"])["cell"] == cell]


# ---- scale ----
def test_a_scale_word_in_the_row_label_beats_the_sheet_header(tmp_path):
    sheets = {"KPIs": pl(["Total payment volume (TPV), USD thousands", (3980000, "#,##0"), (4870000, "#,##0")]),
              "Notes": NOTES}
    rows, _ = read(make(tmp_path, sheets), sheets)
    f = facts_at(rows, "KPIs!C4")[0]
    assert (f["metric"], f["value"], f["scale"], f["unit"]) == ("tpv", 4870000000.0, 3, "USD")
    assert json.loads(f["lineage"])["scale_source"]["kind"] == "row_label"


def test_sheet_header_scale_and_a_currency_row_with_no_scale_stops(tmp_path):
    sheets = {"P&L": pl(["Net revenue", (48.7, M1), (59.3, M1)]), "Notes": NOTES}
    rows, _ = read(make(tmp_path, sheets), sheets)
    f = facts_at(rows, "P&L!C4")[0]
    assert (f["value"], f["scale"], f["decimals"]) == (59300000.0, 6, -5)
    assert json.loads(f["lineage"])["scale_source"] == {"kind": "sheet_header", "cell": "P&L!A2"}
    sheets = {"P&L": pl(["Net revenue", (48.7, M1), (59.3, M1)], unit=None), "Notes": NOTES}
    with pytest.raises(XlsxError, match="no stated scale"):
        read(make(tmp_path, sheets), sheets)


def test_a_percent_metric_that_is_not_percent_formatted_stops(tmp_path):
    sheets = {"KPIs": pl(["Net revenue retention (trailing 12 months)", 1.09, 1.12]), "Notes": NOTES}
    with pytest.raises(XlsxError, match="not %-formatted"):
        read(make(tmp_path, sheets), sheets)


def test_percent_rows_are_pure_and_text_cells_give_no_fact(tmp_path):
    sheets = {"KPIs": pl(["Net revenue retention (trailing 12 months)", "n/a", (1.12, "0.0%")]), "Notes": NOTES}
    rows, log = read(make(tmp_path, sheets), sheets)
    assert [(r["metric"], r["value"], r["unit"], r["scale"], r["decimals"]) for r in rows] == [("nrr", 1.12, "pure", 0, 3)]
    assert log["text_cells"] == [{"cell": "KPIs!B4", "label": "Net revenue retention (trailing 12 months)", "text": "n/a"}]


# ---- sheets, fiscal year end, periods ----
def test_a_hidden_sheet_is_skipped_and_logged(tmp_path):
    sheets = {"P&L": pl(["Net revenue", (48.7, M1), (59.3, M1)]), "Notes": NOTES,
              "Draft": [["Draft"], [None, "FY2024", "FY2025"], ["Net revenue", 48.7, 60.1]]}
    rows, log = read(make(tmp_path, sheets, hidden=("Draft",)), sheets)
    assert log["hidden_sheets_skipped"] == [{"sheet": "Draft", "state": "hidden", "rows": 3}]
    assert all(not json.loads(r["citation"])["cell"].startswith("Draft") for r in rows)


def test_fiscal_year_dates_and_labels(tmp_path):
    sheets = {"Balance Sheet": pl(["Cash and equivalents", (9.8, M1), (14.6, M1)]),
              "P&L": pl(["Net revenue", (48.7, M1), (59.3, M1)]), "Notes": NOTES}
    rows, log = read(make(tmp_path, sheets), sheets)
    cash, rev = facts_at(rows, "Balance Sheet!C4")[0], facts_at(rows, "P&L!C4")[0]
    assert (cash["period_type"], cash["start"], cash["end"], cash["fiscal_period"]) == ("instant", None, "2025-06-30", "instant")
    assert (rev["start"], rev["end"], rev["fiscal_year"], rev["fiscal_period"]) == ("2024-07-01", "2025-06-30", 2025, "FY")
    assert log["fiscal_year_end"] == "0630" and log["fy_range_cells"] == ["Notes!A1"]


def test_without_an_explicit_range_the_end_year_convention_is_recorded(tmp_path):
    sheets = {"P&L": pl(["Net revenue", (48.7, M1), (59.3, M1)])}
    rows, _ = read(make(tmp_path, sheets), sheets)
    assert json.loads(rows[0]["lineage"])["fy_label_convention"] == "end_year"


@pytest.mark.parametrize("notes,match", [
    ([["Fiscal year ends June 30."], ["Fiscal year ends March 31."]], "disagree"),
    ([["Fiscal year ends June 30. FY2025 = July 1, 2024 to June 29, 2025."]], "derived"),
])
def test_fiscal_year_statements_must_agree(tmp_path, notes, match):
    sheets = {"P&L": pl(["Net revenue", (48.7, M1), (59.3, M1)]), "Notes": notes}
    with pytest.raises(XlsxError, match=match):
        read(make(tmp_path, sheets), sheets)


def test_fy_columns_without_a_fiscal_year_end_statement_stop(tmp_path):
    sheets = {"P&L": [["Co"], ["USD millions"], [None, "FY2024", "FY2025"], ["Net revenue", (48.7, M1), (59.3, M1)]]}
    with pytest.raises(XlsxError, match="no fiscal year end"):
        read(make(tmp_path, sheets), sheets)


def test_a_non_fy_column_label_stops(tmp_path):
    sheets = {"P&L": [["Co"], ["USD millions", "Fiscal year ended June 30"], [None, "FY2024", "CY2025"],
                      ["Net revenue", (48.7, M1), (59.3, M1)]], "Notes": NOTES}
    with pytest.raises(XlsxError, match="other than FYyyyy"):
        read(make(tmp_path, sheets), sheets)


def test_a_single_value_column_sheet_takes_its_period_from_the_title(tmp_path):
    sheets = {"QoE Add-backs": [["EBITDA bridge, FY2025 (USD millions)"], [], ["Reported EBITDA", (7.9, M1)],
                                ["QoE-adjusted EBITDA", (13.7, M1)]], "Notes": NOTES}
    rows, _ = read(make(tmp_path, sheets), sheets)
    f = facts_at(rows, "QoE Add-backs!B4")[0]
    assert (f["metric"], f["value"], f["fiscal_year"], f["fiscal_period"]) == ("adjusted_ebitda", 13700000.0, 2025, "FY")
    assert json.loads(f["lineage"])["scale_source"] == {"kind": "sheet_header", "cell": "QoE Add-backs!A1"}


def test_a_heading_row_naming_another_fy_than_the_column_stops(tmp_path):
    sheets = {"C&P": pl(["Top partner share of net revenue", (0.152, "0.0%"), (0.168, "0.0%")], [],
                        ["Largest partners and customers, FY2024 share of net revenue"],
                        ["MedLedger Systems", None, (0.168, "0.0%")]), "Notes": NOTES}
    with pytest.raises(XlsxError, match="heading naming FY2024"):
        read(make(tmp_path, sheets), sheets)


# ---- sign, unmapped rows ----
def test_a_negative_cost_is_stored_positive_with_lineage(tmp_path):
    sheets = {"P&L": pl(["Pass-through costs (interchange, network fees)", (-30.2, M1), (-37.1, M1)]), "Notes": NOTES}
    rows, log = read(make(tmp_path, sheets), sheets)
    f = facts_at(rows, "P&L!C4")[0]
    assert f["value"] == 37100000.0
    assert {k: json.loads(f["lineage"])[k] for k in ("sign_flipped", "value_as_shown")} == {"sign_flipped": True,
                                                                                           "value_as_shown": -37.1}
    assert len(log["sign_flipped"]) == 2


def test_unmapped_rows_are_stored_on_a_single_period_sheet(tmp_path):
    sheets = {"P&L": pl(["Net revenue", (48.7, M1), (59.3, M1)], ["Operating expenses (excl. D&A)", (43.3, M1), (51.4, M1)]),
              "Notes": NOTES}
    rows, log = read(make(tmp_path, sheets), sheets)
    f = facts_at(rows, "P&L!C5")[0]
    assert (f["metric"], f["value"], f["period_type"], f["tier"]) == (None, 51400000.0, "duration", 3)
    assert len(log["unmapped_stored"]) == 2


def test_unmapped_rows_on_a_mixed_or_unmapped_sheet_are_logged_not_stored(tmp_path):
    sheets = {"KPIs": pl(["Total payment volume (TPV), USD thousands", (3980000, "#,##0"), (4870000, "#,##0")],
                         ["Subscription ARR (June 30)", (13.9, M1), (18.6, M1)], ["Other figure", (1.0, M1), (2.0, M1)]),
              "Misc": pl(["Something else", (1.0, M1), (2.0, M1)]), "Notes": NOTES}
    rows, log = read(make(tmp_path, sheets), sheets)
    assert not [r for r in rows if r["metric"] is None]
    assert sorted({u["reason"] for u in log["unmapped_not_stored"]}) == ["sheet has no mapped rows",
                                                                          "sheet mixes instant and duration rows"]


@pytest.mark.parametrize("fmt,scale,expected", [(M1, 6, -5), ("#,##0", 3, -3), ("0.0%", 0, 3), ("0.000%", 0, 5),
                                                ("General", 6, None), (None, 6, None)])
def test_decimals_from_format(fmt, scale, expected):
    assert decimals_from_format(fmt, scale) == expected


# ---- Kestrel (local only) ----
KESTREL = ROOT / "data" / "dataroom" / "kestrel"
needs_kestrel = pytest.mark.skipif(not (KESTREL / "export" / "records.jsonl").exists(), reason="Kestrel data room (DVC)")


@pytest.fixture(scope="module")
def kestrel():
    from src.facts.build import build_dataroom
    rows, log = build_dataroom("kestrel", ROOT / "data" / "dataroom", VOCAB, 7)
    return {json.loads(r["citation"])["cell"]: r for r in rows}, log["DR-kestrel-005"]


@needs_kestrel
def test_kestrel_tpv_fy2025_from_the_row_label(kestrel):
    f = kestrel[0]["KPIs!D4"]
    assert (f["metric"], f["value"], f["unit"], f["scale"]) == ("tpv", 4870000000.0, "USD", 3)
    assert json.loads(f["lineage"])["scale_source"] == {"kind": "row_label", "cell": "KPIs!A4"}


@needs_kestrel
def test_kestrel_draft_sheet_is_skipped(kestrel):
    cells, log = kestrel
    assert log["hidden_sheets_skipped"] == [{"sheet": "Draft_v3 (do not use)", "state": "hidden", "rows": 4}]
    assert not [c for c in cells if c.startswith("Draft_v3")]


@needs_kestrel
def test_kestrel_opex_excl_da_is_stored_unmapped(kestrel):
    got = [(kestrel[0][c]["metric"], kestrel[0][c]["value"], kestrel[0][c]["period_type"]) for c in ("P&L!B9", "P&L!C9", "P&L!D9")]
    assert got == [(None, -35200000.0, "duration"), (None, -43300000.0, "duration"), (None, -51400000.0, "duration")]


@needs_kestrel
def test_kestrel_nrr(kestrel):
    f = kestrel[0]["KPIs!D6"]
    assert (f["metric"], f["value"], f["unit"]) == ("nrr", 1.12, "pure")
    assert "KPIs!B6" not in kestrel[0]
    assert {"cell": "KPIs!B6", "label": "Net revenue retention (trailing 12 months)", "text": "n/a"} in kestrel[1]["text_cells"]


@needs_kestrel
def test_kestrel_every_fact_is_tier_3_with_a_sheet_cell_citation(kestrel):
    blocks = {json.loads(l)["block_id"] for l in open(KESTREL / "export" / "records.jsonl")
              if json.loads(l)["doc_id"] == "DR-kestrel-005"}
    for cell, f in kestrel[0].items():
        c = json.loads(f["citation"])
        assert f["tier"] == 3 and f["deal_id"] == "kestrel" and c["kind"] == "sheet_cell" and c["block_id"] in blocks


@needs_kestrel
def test_kestrel_values_match_the_answer_key_by_value(kestrel):
    """Compared by cell and value only; the key's unit field is the generator's source unit (DECISIONS 2026-11-05)."""
    key = yaml.safe_load(open(KESTREL / "answer_key.yaml"))["facts"]
    # answer key states these positive; generator fix pending (PROGRESS.md, generator to-do)
    abs_only = {"P&L!B9", "P&L!C9", "P&L!D9"}

    def ours(k):
        v = kestrel[0][k["cell"]]["value"]
        return abs(v) if k["cell"] in abs_only else v

    bad = [(k["cell"], k["value"], kestrel[0].get(k["cell"], {}).get("value")) for k in key
           if k["cell"] not in kestrel[0] or abs(ours(k) - k["value"]) > 1e-9 * abs(k["value"])]
    assert len(key) == 53 and bad == []


@needs_kestrel
def test_kestrel_pass_through_costs_are_positive(kestrel):
    f = kestrel[0]["P&L!D5"]
    assert (f["metric"], f["value"]) == ("pass_through_costs", 37100000.0) and json.loads(f["lineage"])["sign_flipped"]
