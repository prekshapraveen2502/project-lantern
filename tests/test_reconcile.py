"""
Part 1 item 6 tests: reconcile (src/facts/reconcile.py). Small fixtures of facts and claims; the Kestrel test reads
data/facts/discrepancies.jsonl and is skipped without DVC data.
"""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.facts.reconcile import FIELDS, ReconcileError, index_facts, reconcile_deal  # noqa: E402

TOL, HIGH = 0.01, 0.05


def fact(fid, metric, fy, value, fp="FY", end=None):
    return {"fact_id": fid, "metric": metric, "fiscal_year": fy, "fiscal_period": fp, "value": value,
            "end": end or f"{fy}-06-30"}


FACTS = [fact("F-r24", "revenue", 2024, 48.7e6), fact("F-r25", "revenue", 2025, 59.3e6),
         fact("F-g25", "gross_revenue", 2025, 96.4e6), fact("F-s25", "subscription_revenue", 2025, 17.4e6),
         fact("F-t25", "top1_customer_share", 2025, 0.168), fact("F-n25", "nrr", 2025, 1.12),
         fact("F-a25", "arr", 2025, 18.6e6, fp="instant"), fact("F-v25", "tpv", 2025, 4.87e9)]


def claim(cid, metric, value, comparator="eq", fy=2025, fp="FY", basis="fiscal", label="FY2025", qualifier=()):
    return {"claim_id": cid, "metric": metric, "value": value, "comparator": comparator, "fiscal_year": fy,
            "fiscal_period": fp, "period_basis": basis, "period_label": label, "qualifier": list(qualifier)}


def run(*claims, facts=FACTS):
    recs, fye = reconcile_deal(list(claims), facts, TOL, HIGH)
    return {r["claim_id"]: r for r in recs}, fye


def test_records_have_exactly_the_brief_fields():
    recs, fye = run(claim("C-1", "revenue", 59.3e6))
    assert list(recs["C-1"]) == FIELDS and fye == "0630"


def test_match_within_tolerance_and_discrepancy_severities():
    recs, _ = run(claim("C-m", "revenue", 59.5e6), claim("C-med", "tpv", 5.0e9, "approx"),
                  claim("C-hi", "revenue", 70e6))
    assert recs["C-m"]["status"] == "match" and recs["C-m"]["fact_id"] == "F-r25"
    assert (recs["C-med"]["status"], recs["C-med"]["severity"]) == ("discrepancy", "medium")   # gap 2.7%
    assert (recs["C-hi"]["status"], recs["C-hi"]["severity"]) == ("discrepancy", "high")
    assert abs(recs["C-hi"]["gap"] - (70e6 - 59.3e6) / 59.3e6) < 1e-12


def test_bounds():
    recs, _ = run(claim("C-over", "tpv", 4.9e9, "gt"),            # fact 4.87bn misses "over 4.9bn" by 0.6%: match
                  claim("C-cap", "top1_customer_share", 0.10, "le"),  # 16.8% > 10%: discrepancy
                  claim("C-ok", "top1_customer_share", 0.33, "lt"))   # 16.8% < 33%: match
    assert recs["C-over"]["status"] == "match"
    assert (recs["C-cap"]["status"], recs["C-cap"]["severity"]) == ("discrepancy", "high")
    assert recs["C-ok"]["status"] == "match"


def test_no_fact_and_period_mismatch():
    recs, _ = run(claim("C-null", None, 36.0), claim("C-head", "headcount", 410.0),
                  claim("C-cy", "revenue", 64.2e6, basis="calendar", label="CY2025", fy=2026, fp="other"),
                  claim("C-q4", "nrr", 1.28, fp="Q4", label="Q4 FY2025"))
    assert recs["C-null"]["status"] == recs["C-head"]["status"] == "no_fact"
    assert recs["C-cy"]["status"] == recs["C-q4"]["status"] == "period_mismatch"
    assert recs["C-cy"]["gap"] is None and recs["C-cy"]["fact_id"] is None


def test_a_calendar_claim_is_compared_when_the_fiscal_year_is_the_calendar_year():
    dec = [fact("F-1", "revenue", 2025, 100.0, end="2025-12-31")]
    recs, fye = run(claim("C-1", "revenue", 100.0, basis="calendar", label="CY2025"), facts=dec)
    assert fye == "1231" and recs["C-1"]["status"] == "match"


def test_definition_mismatch_for_run_rate_and_for_another_metrics_value():
    recs, _ = run(claim("C-arr", "arr", 21.0e6, fp="instant", qualifier=["run-rate"]),
                  claim("C-gross", "revenue", 96.4e6))
    assert recs["C-arr"]["status"] == "definition_mismatch" and recs["C-arr"]["fact_id"] == "F-a25"
    assert recs["C-gross"]["status"] == "definition_mismatch" and recs["C-gross"]["severity"] is None


def test_derived_growth_and_subscription_share():
    recs, _ = run(claim("C-g", "revenue_growth", 0.31), claim("C-s", "subscription_share", 0.92),
                  claim("C-g2", "revenue_growth", 59.3 / 48.7 - 1))
    assert recs["C-g"]["status"] == "discrepancy" and recs["C-g"]["fact_id"] == "F-r25"
    assert abs(recs["C-g"]["gap"] - (0.31 - (59.3 / 48.7 - 1)) / (59.3 / 48.7 - 1)) < 1e-12
    assert recs["C-s"]["status"] == "discrepancy" and recs["C-s"]["fact_id"] == "F-s25"
    assert recs["C-g2"]["status"] == "match"


def test_duplicate_facts_equal_use_lowest_id_different_stop():
    same = [fact("F-b", "reported_ebitda", 2025, 7.9e6), fact("F-a", "reported_ebitda", 2025, 7.9e6)]
    assert index_facts(same)[("reported_ebitda", 2025, "FY")]["fact_id"] == "F-a"
    with pytest.raises(ReconcileError, match="disagree"):
        index_facts([fact("F-a", "x", 2025, 1.0), fact("F-b", "x", 2025, 2.0)])


DISC = ROOT / "data" / "facts" / "discrepancies.jsonl"


@pytest.mark.skipif(not DISC.exists(), reason="data/facts/discrepancies.jsonl (DVC)")
def test_kestrel_one_record_per_claim_with_valid_fields():
    import duckdb
    recs = [json.loads(l) for l in open(DISC)]
    con = duckdb.connect(str(ROOT / "data" / "facts" / "facts.duckdb"), read_only=True)
    ids = {r[0] for r in con.execute("SELECT claim_id FROM claims").fetchall()}
    con.close()
    assert {r["claim_id"] for r in recs} == ids and len(recs) == len(ids)
    for r in recs:
        assert list(r) == FIELDS
        assert r["status"] in ("match", "discrepancy", "definition_mismatch", "period_mismatch", "no_fact")
        assert (r["severity"] in ("high", "medium")) == (r["status"] == "discrepancy")
