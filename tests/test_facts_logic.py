"""
Part 1 item 2 tests: the fact store's pure logic (no Arelle, no DVC data). Cases come from the evidence gathered for
the decisions of 2026-11-05: BILL's repeated Goodwill tags, PAYO's two revenue concepts, share classes (FLYW, TOST,
XYZ), BILL's June fiscal year end and a 52/53-week year.
"""
import json
import sys
from datetime import date
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.facts.build import choose_concepts, crosscheck, fact_id, metric_for, metric_index, missing_periods  # noqa: E402
from src.facts.periods import fiscal_labels  # noqa: E402
from src.facts.vocab import load_vocab  # noqa: E402
from src.facts.xbrl_read import check_unconvertible, group_tags, period_dates, read_filing, unit_measure  # noqa: E402

VOCAB = load_vocab(ROOT / "config" / "metrics_vocab.yaml")
FY26 = (date(2025, 7, 1), date(2026, 6, 30))


def tag(concept, value, decimals, element_id, order, context="c-1", unit="usd", dims="{}", start=FY26[0], end=FY26[1]):
    return {"concept": concept, "value": value, "decimals": decimals, "element_id": element_id, "order": order,
            "context_ref": context, "unit_ref": unit, "dims": dims, "start": start, "end": end, "unit": "USD",
            "scale": 3, "period_type": "duration"}


# ---- repeated tags (R1) ----
def test_most_precise_tag_wins_and_rounded_repeats_are_consistent():
    """BILL Goodwill: 2,396,509,000 (decimals -3) and 2,400,000,000 (-8) are the same fact."""
    facts, bad = group_tags([tag("us-gaap:Goodwill", 2400000000.0, "-8", "f-9", 0),
                             tag("us-gaap:Goodwill", 2396509000.0, "-3", "f-1", 5)])
    assert len(facts) == 1 and bad == []
    assert (facts[0]["value"], facts[0]["element_id"], facts[0]["also_tagged"]) == (2396509000.0, "f-1", ["f-9"])


def test_a_repeat_that_does_not_round_to_the_precise_value_is_inconsistent():
    facts, bad = group_tags([tag("us-gaap:Goodwill", 2396509000.0, "-3", "f-1", 0),
                             tag("us-gaap:Goodwill", 2500000000.0, "-8", "f-2", 1)])
    assert len(facts) == 1 and len(bad) == 1 and bad[0]["differs"][0]["element_id"] == "f-2"


def test_ties_go_to_the_first_tag_in_document_order_and_inf_is_most_precise():
    facts, _ = group_tags([tag("c", 5.0, "-3", "late", 7), tag("c", 5.0, "-3", "early", 2)])
    assert facts[0]["element_id"] == "early"
    facts, _ = group_tags([tag("c", 5.0, "-3", "a", 0), tag("c", 5.0, "INF", "b", 1)])
    assert facts[0]["element_id"] == "b"


def test_different_contexts_stay_separate_facts():
    facts, _ = group_tags([tag("c", 1.0, "-3", "a", 0, context="c-1"), tag("c", 2.0, "-3", "b", 1, context="c-2")])
    assert len(facts) == 2


# ---- period dates: Arelle's end and instant datetimes are exclusive ----
def test_instant_and_duration_end_are_shifted_back_one_day():
    from datetime import datetime
    assert period_dates(True, None, datetime(2026, 7, 1)) == (None, date(2026, 6, 30))
    assert period_dates(False, datetime(2025, 7, 1), datetime(2026, 7, 1)) == (date(2025, 7, 1), date(2026, 6, 30))


@pytest.mark.skipif(not list((ROOT / "data" / "edgar_comps" / "BILL" / "10-K").glob("*/unpacked")),
                    reason="BILL 10-K not present (DVC data)")
def test_bill_balance_sheet_instant_is_june_30_through_arelle():
    """Local only: BILL's FY2026 balance sheet (Assets) is stored at 2026-06-30, not 2026-07-01."""
    import yaml
    fdir = next((ROOT / "data" / "edgar_comps" / "BILL" / "10-K").iterdir())
    doc = json.loads((fdir / "filing.json").read_text())["doc"]
    cache = ROOT / yaml.safe_load((ROOT / "params.yaml").read_text())["facts"]["arelle_cache"]
    read = read_filing(fdir / "unpacked" / doc, cache)
    ends = {t["end"] for t in read["tags"] if t["concept"] == "us-gaap:Assets" and t["period_type"] == "instant"
            and t["dims"] == "{}"}
    assert date(2026, 6, 30) in ends and date(2026, 7, 1) not in ends
    assert read["loaded"] == len(read["tags"]) + read["nil"] + len(read["unconvertible"])


# ---- numbers Arelle cannot convert (ixt-sec:numwordsen) ----
def test_an_unconvertible_vocabulary_concept_stops_the_stage():
    u = [{"concept": "us-gaap:Revenues", "element_id": "f-1", "text": "one billion", "format": "ixt-sec:numwordsen"}]
    with pytest.raises(RuntimeError, match="us-gaap:Revenues"):
        check_unconvertible(u, {"us-gaap:Revenues"}, "TEST 0000000000-26-000001")


def test_an_unconvertible_non_vocabulary_concept_is_only_skipped():
    u = [{"concept": "us-gaap:NumberOfOperatingSegments", "element_id": "f-2", "text": "one",
          "format": "ixt-sec:numwordsen"}]
    check_unconvertible(u, {"us-gaap:Revenues"}, "TEST")      # no error


# ---- units ----
@pytest.mark.parametrize("num,den,expected", [(["iso4217:USD"], [], "USD"), (["xbrli:shares"], [], "shares"),
                                              (["xbrli:pure"], [], "pure"),
                                              (["iso4217:USD"], ["xbrli:shares"], "USD/shares"),
                                              (["flyw:Segment"], [], "flyw:Segment")])
def test_unit_measure(num, den, expected):
    assert unit_measure(num, den) == expected


# ---- fiscal labels (tolerance 7 days) ----
@pytest.mark.parametrize("start,end,fye,expected", [
    (date(2025, 7, 1), date(2026, 6, 30), "0630", (2026, "FY")),        # BILL FY2026
    (date(2026, 1, 1), date(2026, 3, 31), "0630", (2026, "Q3")),
    (date(2025, 7, 1), date(2026, 3, 31), "0630", (2026, "9M")),
    (None, date(2025, 12, 31), "0630", (2026, "instant")),
    (None, date(2026, 6, 30), "0630", (2026, "instant")),
    (date(2026, 1, 1), date(2026, 6, 30), "1231", (2026, "H1")),
    (date(2025, 1, 1), date(2025, 12, 31), "1231", (2025, "FY")),
    (date(2024, 7, 1), date(2025, 6, 30), "0630", (2025, "FY")),        # Kestrel FY2025 (its Notes sheet)
    (date(2024, 9, 29), date(2025, 9, 27), "0930", (2025, "FY")),       # 52/53-week: last Saturday of September
    (date(2024, 9, 29), date(2024, 12, 28), "0930", (2025, "Q1")),
    (date(2025, 1, 4), date(2026, 1, 2), "1231", (2025, "FY")),         # year ending 2 days after Dec 31
    (date(2025, 2, 1), date(2025, 5, 15), "1231", (2025, "other")),
])
def test_fiscal_labels(start, end, fye, expected):
    assert fiscal_labels(start, end, fye, 7) == expected


def test_outside_the_tolerance_is_not_a_fiscal_year_end():
    assert fiscal_labels(date(2025, 1, 15), date(2026, 1, 12), "1231", 7)[1] != "FY"


# ---- concept choice (decision 2) ----
def f(concept, dims="{}", start=FY26[0], end=FY26[1]):
    return {"concept": concept, "dims": dims, "start": start, "end": end}


def test_revenues_wins_when_both_revenue_concepts_are_reported():
    """PAYO tags Revenues (1,052,774,000) and RevenueFromContract... (813,413,000); the total comes first."""
    chosen = choose_concepts([f("us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax"),
                              f("us-gaap:Revenues")], VOCAB)
    assert chosen["revenue"]["concepts"] == ["us-gaap:Revenues"]


def test_contract_revenue_is_used_when_it_is_the_only_one():
    chosen = choose_concepts([f("us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax")], VOCAB)
    assert chosen["revenue"]["concepts"] == ["us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax"]


def test_share_classes_map_to_shares_outstanding_but_segment_facts_do_not_map():
    cls = '{"us-gaap:StatementClassOfStockAxis": "us-gaap:CommonClassAMember"}'
    seg = '{"srt:ProductOrServiceAxis": "x:SubscriptionMember"}'
    chosen = choose_concepts([f("dei:EntityCommonStockSharesOutstanding", cls), f("us-gaap:Revenues"),
                              f("us-gaap:Revenues", seg)], VOCAB)
    idx = metric_index(chosen)
    assert metric_for(f("dei:EntityCommonStockSharesOutstanding", cls), idx) == "shares_outstanding"
    assert metric_for(f("us-gaap:Revenues"), idx) == "revenue"
    assert metric_for(f("us-gaap:Revenues", seg), idx) is None


def test_every_debt_component_present_is_kept():
    chosen = choose_concepts([f("us-gaap:ConvertibleDebtCurrent"), f("us-gaap:ConvertibleDebtNoncurrent"),
                              f("us-gaap:LongTermDebt")], VOCAB)
    assert chosen["debt_component"]["concepts"] == ["us-gaap:ConvertibleDebtCurrent", "us-gaap:ConvertibleDebtNoncurrent"]
    assert chosen["total_debt"]["concepts"] == ["us-gaap:LongTermDebt"]


def test_a_period_missing_the_chosen_concept_is_logged_not_filled():
    older = (date(2023, 7, 1), date(2024, 6, 30))
    facts = [f("us-gaap:Revenues"), f("us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax", start=older[0], end=older[1])]
    chosen = choose_concepts(facts, VOCAB)
    log = missing_periods(facts, chosen, VOCAB, "0000000000-26-000001")
    assert [(r["metric"], r["chosen"], r["end"]) for r in log] == [("revenue", "us-gaap:Revenues", "2024-06-30")]
    assert metric_for(facts[1], metric_index(chosen)) is None


# ---- ids and the companyfacts cross-check ----
def test_fact_id_is_stable():
    assert fact_id("0001628280-26-058238", "f-142") == fact_id("0001628280-26-058238", "f-142")
    assert fact_id("0001628280-26-058238", "f-142").startswith("F-") and len(fact_id("a", "b")) == 14


def test_crosscheck_counts_matches_mismatches_and_absent_facts():
    cf = {"facts": {"us-gaap": {"Revenues": {"units": {"USD": [
        {"accn": "A", "start": "2025-01-01", "end": "2025-12-31", "val": 100.0}]}},
        "Assets": {"units": {"USD": [{"accn": "A", "end": "2025-12-31", "val": 5.0}]}}}}}
    row = {"fact_id": "F-1", "concept": "us-gaap:Revenues", "unit": "USD", "doc_id": "A", "start": "2025-01-01",
           "end": "2025-12-31", "value": 100.0, "dims": "{}"}
    rows = [row, {**row, "fact_id": "F-2", "concept": "us-gaap:Assets", "start": None, "value": 6.0},
            {**row, "fact_id": "F-3", "concept": "bill:Custom"},
            {**row, "fact_id": "F-4", "dims": json.dumps({"a": "b"})}]
    out = crosscheck(rows, cf)
    assert (out["compared"], out["matched"], out["mismatched"], out["not_in_companyfacts"]) == (2, 1, 1, 1)
    assert out["mismatches"][0]["fact_id"] == "F-2"
