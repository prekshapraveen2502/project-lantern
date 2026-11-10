"""Part 5 item 2: sections, linking, strategy (c) reconciliation, gold scoring and the bootstrap."""
from pathlib import Path

import numpy as np
import pytest

from src.extract import evaluate as E
from src.extract import link as L
from src.extract import profile as P
from src.extract.run import sections

ROOT = Path(__file__).resolve().parents[1]


def test_only_evaluate_reads_the_gold_profile():
    """The gold profile is for evaluation only (never in prompts or extraction)."""
    hits = [p.relative_to(ROOT).as_posix() for p in (ROOT / "src").rglob("*.py")
            if ('["gold"]' in p.read_text(encoding="utf-8") or "profile_gold" in p.read_text(encoding="utf-8"))
            and p.name != "evaluate.py"]
    assert hits == []


def test_sections_merge_heading_only_section_into_next():
    b = lambda s, t, i: {"section": s, "block_type": t, "block_id": i}
    blocks = [b("Cover", "Title", 1), b("Cover", "Text", 2), b("Growth", "Title", 3),
              b("Net revenue", "Title", 4), b("Net revenue", "Text", 5), b("Process", "Text", 6)]
    got = [(n, [x["block_id"] for x in bs]) for n, bs in sections(blocks)]
    assert got == [("Cover", [1, 2]), ("Net revenue", [3, 4, 5]), ("Process", [6])]


def src(block="p0003_b002"):
    return P.Source(doc_id="DR-k-001", page=3, block_id=block, quote="q")


def n(value, block="p0003_b002", period="FY2025", comparator="eq"):
    return P.Num(value=value, unit="USD", comparator=comparator, as_written=str(value), period=period, source=src(block))


FACTS = [
    {"fact_id": "F-rev25", "metric": "revenue", "fiscal_year": 2025, "fiscal_period": "FY", "value": 59.3e6,
     "tier": 3, "unit": "USD"},
    {"fact_id": "F-rev24", "metric": "revenue", "fiscal_year": 2024, "fiscal_period": "FY", "value": 48.7e6,
     "tier": 3, "unit": "USD"},
    {"fact_id": "F-gross25", "metric": "gross_revenue", "fiscal_year": 2025, "fiscal_period": "FY", "value": 96.4e6,
     "tier": 3, "unit": "USD"},
    {"fact_id": "F-top1", "metric": "top1_customer_share", "fiscal_year": 2025, "fiscal_period": "FY",
     "value": 0.168, "tier": 3, "unit": "pure"},
]
CLAIMS = [
    {"claim_id": "C-rev", "doc_id": "DR-k-001", "block_id": "p0003_b002", "metric": "revenue", "value": 96.4e6,
     "fiscal_year": 2025, "fiscal_period": "FY"},
    {"claim_id": "C-grow", "doc_id": "DR-k-001", "block_id": "p0003_b002", "metric": "revenue_growth",
     "value": 0.31, "fiscal_year": 2025, "fiscal_period": "FY"},
    {"claim_id": "C-top1", "doc_id": "DR-k-001", "block_id": "p0006_b002", "metric": "top1_customer_share",
     "value": 0.10, "fiscal_year": 2025, "fiscal_period": "FY"},
]
DISC = {"C-rev": {"status": "definition_mismatch", "fact_id": "F-rev25", "gap": 0.6256},
        "C-grow": {"status": "discrepancy", "fact_id": "F-rev25", "gap": 0.4242},
        "C-top1": {"status": "discrepancy", "fact_id": "F-top1", "gap": -0.4048}}


def profile():
    p = P.DealProfile(deal_id="kestrel", strategy="staged")
    p.history.lines = [P.Line(label="Revenue", metric="gross_revenue", value=n(96.4e6)),
                       P.Line(label="Revenue", metric="revenue", value=n(96.4e6)),
                       P.Line(label="Net revenue", metric="revenue", value=n(48.7e6, block="t", period="FY2024"))]
    p.history.growth = [P.Line(label="growth", metric="revenue_growth", value=n(0.31))]
    p.customers.top1_customer_share = n(0.10, block="p0006_b002", comparator="le")
    return p


def test_link_then_reconcile():
    from src.facts.reconcile import index_facts
    idx, by_id, log = index_facts(FACTS), {f["fact_id"]: f for f in FACTS}, []
    p = profile()
    L.link(p, CLAIMS, idx, by_id, 0.01, log)
    gross, rev, rev24 = p.history.lines
    assert gross.value.ref == "F-gross25"           # labelled gross_revenue: links to the gross fact, not the claim
    assert rev.value.ref == "C-rev" and rev24.value.ref == "F-rev24"
    assert p.history.growth[0].value.ref == "C-grow" and p.customers.top1_customer_share.ref == "C-top1"
    L.reconcile(p, {c["claim_id"]: c for c in CLAIMS}, DISC, idx, by_id, log)
    assert gross.value.value == 96.4e6 and gross.value.reconcile is None
    assert rev.value.value == 59.3e6 and rev.value.ref == "F-rev25"
    assert rev.value.reconcile.claimed_value == 96.4e6 and rev.value.reconcile.status == "definition_mismatch"
    g = p.history.growth[0].value
    assert g.value == pytest.approx(59.3 / 48.7 - 1) and g.ref.startswith("CALC-")
    t = p.customers.top1_customer_share
    assert t.value == 0.168 and t.comparator == "eq" and t.reconcile.claimed_comparator == "le"


def test_score_field_types():
    p = profile()
    p.overview.company_name = "Kestrel Pay Inc"
    p.management = [P.Manager(name="Dana Whitfield", title="CEO", joined_year=2016)]
    p.addbacks.items = [P.AddBack(name="Stock-based compensation", amount=n(4.6e6))]
    p.disclosure_gaps = ["Customer churn by cohort"]
    p.process.bid_deadline = __import__("datetime").date(2026, 10, 30)
    flat = P.flatten(p)
    s = lambda g: E.score_field(g, flat, 0.01)
    assert s({"id": "overview.company_name", "type": "text", "truth": "Kestrel Pay, Inc."})["truth"]
    assert s({"id": "management.dana whitfield.title", "type": "text", "truth": "Chief Executive Officer",
              "aliases": ["CEO"]})["truth"]
    assert s({"id": "management.dana whitfield.joined_year", "type": "integer", "truth": 2016})["truth"]
    assert s({"id": "process.bid_deadline", "type": "date", "truth": "2026-10-30"})["truth"]
    assert s({"id": "gaps.churn", "type": "set_item", "keywords": ["churn", "cohort"]})["truth"]
    assert not s({"id": "gaps.chargeback", "type": "set_item", "keywords": ["chargeback"]})["truth"]
    assert s({"id": "addbacks.items.sbc.amount", "type": "number", "truth": 4.6e6, "keywords": ["stock"]})["truth"]
    assert s({"id": "customers.customer_count", "type": "absent"})["truth"]
    top1 = {"id": "customers.top1_customer_share", "type": "number", "truth": 0.168, "as_stated": 0.10,
            "comparator": "le"}
    r = s(top1)
    assert not r["truth"] and r["as_stated"]
    assert not s({**top1, "comparator": "lt"})["as_stated"]


def test_bootstrap_paired_and_selection():
    c = {"single": np.array([1, 0, 0, 1.0]), "staged": np.array([1, 1, 0, 1.0]),
         "staged_reconciled": np.array([1, 1, 1, 1.0])}
    r = E.bootstrap(c, 2000, 7)
    assert r["single"]["accuracy"] == 0.5 and r["staged_reconciled"]["accuracy"] == 1.0
    lo, hi = r["single"]["ci95"]
    assert 0 <= lo <= 0.5 <= hi <= 1
    assert r["staged-single"]["difference"] == 0.25 and r["staged-single"]["ci95"][0] >= 0
    assert r == E.bootstrap(c, 2000, 7)            # seeded
    acc = {"single": {"accuracy": 0.5}, "staged": {"accuracy": 0.9}, "staged_reconciled": {"accuracy": 0.9}}
    assert E.select(acc, {"staged": {"usd": 0.02}, "staged_reconciled": {"usd": 0.04}}) == "staged"
    assert E.select(acc, {}) == "staged"
