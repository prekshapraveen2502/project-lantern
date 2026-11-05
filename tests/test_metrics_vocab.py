"""
Part 1 item 1 tests: config/metrics_vocab.yaml and src/facts/vocab.py. No DVC data needed: the Kestrel row labels
below are typed in from the public practice deal (data/dataroom/kestrel/raw/financials.xlsx).
"""
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.facts.vocab import load_vocab, match_line, normalise_label  # noqa: E402

VOCAB = load_vocab(ROOT / "config" / "metrics_vocab.yaml")
BRIEF_EXAMPLES = ["revenue", "revenue_growth", "gross_margin", "ebitda", "adjusted_ebitda", "ebitda_margin", "arr",
                  "nrr", "gross_logo_churn", "top1_customer_share", "top10_customer_share", "headcount"]

# Kestrel financials.xlsx row labels as written, and the metric each must map to (decision 3 table).
KESTREL_ROWS = [
    ("Gross revenue", "gross_revenue"),
    ("Pass-through costs (interchange, network fees)", "pass_through_costs"),
    ("Net revenue", "revenue"),
    ("  of which: subscription", "subscription_revenue"),
    ("  of which: transaction", "transaction_revenue"),
    ("Operating expenses (excl. D&A)", None),
    ("Reported EBITDA", "reported_ebitda"),
    ("+ Stock-based compensation", "share_based_comp"),
    ("+ One-off legal settlement (2024 patent claim)", None),
    ("Not accepted: Recurring fraud losses (treated by management as one-off)", None),
    ("QoE-adjusted EBITDA", "adjusted_ebitda"),
    ("Management-adjusted EBITDA", "management_adjusted_ebitda"),
    ("Cash and equivalents", "cash"),
    ("Term loan", "debt_component"),
    ("Customer funds held (settlement)", None),
    ("Total payment volume (TPV), USD thousands", "tpv"),
    ("Subscription ARR (June 30)", "arr"),
    ("Net revenue retention (trailing 12 months)", "nrr"),
    ("Net take rate (net revenue / TPV)", "take_rate"),
    ("Fraud and credit losses (% of TPV)", "fraud_loss_rate"),
    ("Active paying customers", "active_customers"),
    ("Top partner share of net revenue", "top1_customer_share"),
    ("Top 10 customers share of net revenue", "top10_customer_share"),
]


def test_every_metric_has_a_valid_kind_and_unit_type():
    # load_vocab raises on any invalid kind, unit_type, concept prefix or pattern; check the counts too
    kinds = {m["kind"] for m in VOCAB["metrics"].values()}
    assert kinds == {"statement", "revenue_build", "derived", "ebitda_bridge", "kpi"}
    assert len(VOCAB["metrics"]) == 43


def test_brief_examples_are_present():
    assert [m for m in BRIEF_EXAMPLES if m not in VOCAB["metrics"]] == []


def test_xbrl_concepts_only_where_decided():
    for name, m in VOCAB["metrics"].items():
        if m["kind"] in ("revenue_build", "ebitda_bridge", "kpi"):
            assert not m.get("xbrl"), name
    assert VOCAB["metrics"]["revenue"]["xbrl"][0] == "us-gaap:Revenues"          # PAYO: total, not contract-only
    assert VOCAB["metrics"]["debt_component"]["xbrl_select"] == "all"
    assert VOCAB["metrics"]["shares_outstanding"]["xbrl_dims"] == ["us-gaap:StatementClassOfStockAxis"]


@pytest.mark.parametrize("label,metric", KESTREL_ROWS)
def test_kestrel_rows_map_as_decided(label, metric):
    assert match_line(label, VOCAB) == metric


def test_traps_are_not_confused():
    assert match_line("Subscription ARR (June 30)", VOCAB) != "subscription_revenue"
    assert match_line("Management-adjusted EBITDA", VOCAB) != "adjusted_ebitda"
    assert match_line("Net revenue retention (trailing 12 months)", VOCAB) != "revenue"


def test_a_label_matching_two_metrics_stops():
    vocab = load_vocab(ROOT / "config" / "metrics_vocab.yaml")
    vocab["metrics"]["arr"]["_patterns"].append(re.compile("^gross revenue$"))   # make a collision
    with pytest.raises(ValueError, match="matches several metrics"):
        match_line("Gross revenue", vocab)


@pytest.mark.parametrize("raw,expected", [
    ("+ Stock-based compensation:", "stock-based compensation"),
    ("Add:   Stock-based   compensation", "stock-based compensation"),
    ("  of which: subscription", "subscription"),
    ("Management’s adjusted EBITDA", "management's adjusted ebitda"),
    ("Less: Pass-through costs", "pass-through costs"),
])
def test_normalisation(raw, expected):
    assert normalise_label(raw, VOCAB["normalise"]) == expected
