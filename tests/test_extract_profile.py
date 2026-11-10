"""Part 5 item 1: DealProfile conversion, merge and field ids."""
from pathlib import Path

import pytest
import yaml

from src.extract import profile as P

VOCAB = {"metrics": {"revenue": {}, "gross_revenue": {}, "pass_through_costs": {"sign": "cost"}, "arr": {},
                     "revenue_growth": {}, "headcount": {}}}
BLOCKS = [
    {"doc_id": "DR-x-001", "page": 2, "block_id": "p0002_b002", "text": "Kestrel has a team of 410 employees."},
    {"doc_id": "DR-x-001", "page": 3, "block_id": "p0003_b002", "text": "Kestrel reported FY2025 revenue of $96.4m, "
                                                                        "and net revenue grew 31% in FY2025."},
    {"doc_id": "DR-x-001", "page": 9, "block_id": "p0009_b002", "text": "Pass-through costs (23.5) (30.2) (37.1)"},
    {"doc_id": "DR-x-001", "page": 6, "block_id": "p0006_b002", "text": "the top ten customers represent less "
                                                                        "than a third of net revenue."},
]
Ex = P.extraction_model(list(VOCAB["metrics"]))


def ex(**kw):
    return Ex.model_validate(kw)


def num(quote, number, unit="USD", scale="none", period=None, comparator="eq", as_written=None):
    return {"quote": quote, "as_written": as_written or str(number), "number": number, "scale_word": scale,
            "unit": unit, "comparator": comparator, "period_label": period, "qualifiers": []}


def build(e, strategy="single"):
    b = P.Builder("kestrel", BLOCKS, VOCAB, latest_fy=2025)
    return b.build(e, strategy), b.log


def test_units_quotes_periods_and_sign():
    e = ex(overview={"employees": num("a team of 410 employees", 410, unit="count")},
           history={"lines": [
               {"label": "Revenue", "metric": "gross_revenue", "value": num("FY2025 revenue of $96.4m", 96.4,
                                                                             scale="million", period="FY2025")},
               {"label": "Pass-through costs", "metric": "pass_through_costs",
                "value": num("(37.1)", -37.1, scale="million", period="FY2025")}],
               "growth": [{"label": "net revenue growth", "metric": "revenue_growth",
                           "value": num("net revenue grew 31% in FY2025", 31, unit="percent", period="FY2025")}]},
           customers={"top10_customer_share": num("less than a third of net revenue", 0.3333, unit="fraction",
                                                  comparator="lt")})
    p, log = build(e)
    assert p.deal_id == "kestrel" and log == []
    assert p.overview.employees.value == 410 and p.overview.employees.unit == "count"
    assert p.overview.employees.period == "FY2025" and p.overview.employees.period_assumed
    gross, cost = p.history.lines
    assert gross.value.value == pytest.approx(96.4e6) and gross.value.source.block_id == "p0003_b002"
    assert gross.value.source.page == 3
    assert cost.value.value == pytest.approx(37.1e6) and cost.value.sign_flipped
    assert p.history.growth[0].value.value == pytest.approx(0.31) and p.history.growth[0].value.unit == "pure"
    t10 = p.customers.top10_customer_share
    assert t10.value == pytest.approx(0.3333) and t10.comparator == "lt"


def test_unlocated_quote_and_bad_period_are_logged():
    e = ex(kpis=[{"metric": "arr", "value": num("ARR of $21.0m", 21.0, scale="million")},
                 {"metric": "headcount", "value": num("410 employees", 410, unit="count", period="June 2025")}])
    p, log = build(e)
    assert [k.metric for k in p.kpis] == ["headcount"]
    assert p.kpis[0].value.period == "FY2025" and p.kpis[0].value.period_assumed
    assert {x["event"] for x in log} == {"quote_not_found", "period_unrecognised"}


def test_locate_exact_then_in_order_and_raw_mode():
    table = {"doc_id": "DR-x-001", "page": 9, "block_id": "p0009_b003",
             "text": "FY2023\nFY2024\nFY2025\nGross revenue\n61.8\n78.9\n96.4\nNet revenue\n38.3\n48.7\n59.3"}
    assert P.locate_in_order("Gross revenue 61.8", [table])[1] == "exact"
    assert P.locate_in_order("Gross revenue 78.9", [table])[1] == "in_order"
    assert P.locate_in_order("Gross revenue 8.9", [table]) is None          # tokens must be whole
    assert P.locate_in_order("Gross revenue 48.7", [table])[1] == "in_order"   # known limit: across rows
    e = ex(kpis=[{"metric": "arr", "value": num("ARR of $21.0m", 21.0, scale="million")}])
    b = P.Builder("kestrel", BLOCKS, VOCAB, latest_fy=2025, raw=True)
    p = b.build(e, "single")
    assert p.kpis[0].value.source.match == "none" and p.kpis[0].value.value == 21e6
    assert b.log[0]["event"] == "quote_not_found"


def test_merge_first_non_null_wins_and_conflicts_logged():
    a, _ = build(ex(overview={"company_name": "Kestrel Pay, Inc."}, management=[{"name": "Dana Whitfield"}],
                    disclosure_gaps=["customer churn by cohort"]))
    b, _ = build(ex(overview={"company_name": "Kestrel", "hq_city": "Columbus"},
                    management=[{"name": "dana  whitfield", "title": "CEO"}, {"name": "Omar Feld"}],
                    disclosure_gaps=["Customer churn by cohort", "chargeback rates by vertical"]))
    log = []
    m = P.merge([a, b], "staged", log)
    assert m.strategy == "staged"
    assert m.overview.company_name == "Kestrel Pay, Inc." and m.overview.hq_city == "Columbus"
    assert [x.name for x in m.management] == ["Dana Whitfield", "Omar Feld"]
    assert m.disclosure_gaps == ["customer churn by cohort", "chargeback rates by vertical"]
    assert [x["field"] for x in log] == ["overview.company_name"]


def test_flatten_ids():
    e = ex(overview={"company_name": "Kestrel Pay, Inc.", "employees": num("a team of 410 employees", 410,
                                                                           unit="count")},
           history={"fiscal_year_end": "06-30", "lines": [
               {"label": "Revenue", "metric": "gross_revenue", "value": num("FY2025 revenue of $96.4m", 96.4,
                                                                             scale="million", period="FY2025")}]},
           management=[{"name": "Dana Whitfield", "title": "Chief Executive Officer", "joined_year": 2016}],
           process={"bid_deadline": "2026-10-30"},
           addbacks={"items": [{"name": "Stock-based compensation",
                                "amount": num("FY2025 revenue of $96.4m", 4.6, scale="million")}]},
           disclosure_gaps=["customer churn by cohort"])
    p, _ = build(e)
    f = P.flatten(p)
    assert f["overview.company_name"] == "Kestrel Pay, Inc."
    assert f["overview.employees"].value == 410
    assert f["history.fiscal_year_end"] == "06-30"
    assert f["history.gross_revenue.FY2025"].value == pytest.approx(96.4e6)
    assert f["management.dana whitfield.title"] == "Chief Executive Officer"
    assert f["management.dana whitfield.joined_year"] == 2016
    assert f["process.bid_deadline"] == "2026-10-30"
    assert f["addbacks.items"][0][0] == "Stock-based compensation"
    assert f["gaps"] == ["customer churn by cohort"]


GOLD = Path("data/reference/kestrel_profile_gold.yaml")


@pytest.mark.skipif(not GOLD.exists(), reason="gold profile is DVC-tracked (dvc pull)")
def test_every_gold_id_is_one_flatten_can_produce():
    """Fixed ids must be produced by flatten(); row and list ids by their prefix (Phase A section 3)."""
    p = P.DealProfile(deal_id="kestrel", strategy="single")
    fixed = set(P.flatten(p)) | {f"{a}.{n}" for a in ("overview", "business_model", "customers", "process")
                                 for n in type(getattr(p, a)).model_fields} | set(P.FIELD_METRIC) \
        | {"history.fiscal_year_end"}
    lists = ("business_model.revenue_streams.", "business_model.sales_channels.", "gaps.", "addbacks.items.")
    bad = []
    for g in yaml.safe_load(GOLD.read_text())["fields"]:
        i = g["id"]
        ok = (i in fixed or i.startswith(lists) or
              (i.split(".")[0] in ("history", "kpis") and len(i.split(".")) == 3) or
              (i.startswith("management.") and i.split(".")[-1] in ("title", "joined_year")))
        if i.startswith(lists[:3]) and g["type"] != "set_item":
            ok = False
        if i.startswith("addbacks.items.") and not g.get("keywords"):
            ok = False
        if not ok:
            bad.append(i)
    assert bad == []
