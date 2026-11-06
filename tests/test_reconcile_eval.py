"""
Part 1 acceptance tests: reconcile_eval alignment and recall/precision on toy data (CI), and the generated report
(local only, skipped without DVC data).
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.facts.reconcile_eval import CATEGORIES, align, score  # noqa: E402

DOCS = {"cim.pdf": "DR-x-001", "deck.pdf": "DR-x-006"}
KEY = [{"claim_id": "k_growth", "trap": "T-GROWTH", "doc": "cim.pdf", "page": 3, "metric": "revenue_growth",
        "claim_value": 0.31, "expected_status": "discrepancy"},
       {"claim_id": "k_nrr", "trap": "T-NRR", "doc": "deck.pdf", "page": 7, "metric": "nrr", "claim_value": 1.28,
        "expected_status": "discrepancy"},
       {"claim_id": "k_period", "trap": "T-PERIOD", "doc": "cim.pdf", "page": 4, "metric": "revenue",
        "claim_value": 64.2e6, "expected_status": "period_mismatch"}]


def ours(cid, doc, page, metric, value, status):
    return {"claim_id": cid, "doc_id": doc, "page": page, "metric": metric, "value": value, "status": status}


OURS = [ours("C-g", "DR-x-001", 3, "revenue_growth", 0.31, "discrepancy"),
        ours("C-n", "DR-x-006", 7, "nrr", 1.28, "period_mismatch"),
        ours("C-r", "DR-x-001", 3, "revenue", 96.4e6, "definition_mismatch")]          # T-PERIOD claim not extracted


def test_alignment_by_document_page_metric():
    a = align(KEY, OURS, DOCS)
    assert a["k_growth"]["claim_id"] == "C-g" and a["k_nrr"]["claim_id"] == "C-n" and a["k_period"] is None


def test_caught_exact_and_precision():
    s = score(KEY, OURS, align(KEY, OURS, DOCS))
    assert s["T-GROWTH"] == {"traps": 1, "caught": 1, "exact": 1, "flagged": 1, "tp": 1}
    assert s["T-NRR"] == {"traps": 1, "caught": 1, "exact": 0, "flagged": 1, "tp": 1}      # flagged, other status
    assert s["T-PERIOD"] == {"traps": 1, "caught": 0, "exact": 0, "flagged": 1, "tp": 0}   # revenue flag is GROSSNET
    assert set(s) == set(CATEGORIES)


REPORT = ROOT / "reports" / "reconciliation.md"


@pytest.mark.skipif(not (ROOT / "data" / "facts" / "discrepancies.jsonl").exists(), reason="DVC data")
def test_report_has_the_five_categories_and_the_hidden_sheet_count():
    text = REPORT.read_text(encoding="utf-8")
    for cat in CATEGORIES:
        assert f"| {cat} |" in text
    assert "Hidden sheets skipped (seller xlsx) | 1 (Draft_v3 (do not use))" in text
