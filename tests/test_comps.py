"""Part 6 item 2: fact choice for the comps table and the price-file check (synthetic facts, no store)."""
from datetime import date

import pytest

from src.comps import prices as P
from src.comps import table as T


def f(fid, metric, value, fp, fy, start, end, concept="us-gaap:X", filed=date(2026, 8, 1), dims=None, unit="USD",
      ptype="duration"):
    return {"fact_id": fid, "metric": metric, "value": value, "fiscal_period": fp, "fiscal_year": fy,
            "start": date.fromisoformat(start) if start else None, "end": date.fromisoformat(end), "concept": concept,
            "filed": filed, "dims": dims or {}, "unit": unit, "tier": 1, "period_type": ptype, "doc_id": "0000-26-1",
            "citation": "{}"}


def flows(metric, fy24, fy25, h125, h126, tag):
    return [f(f"F-{tag}24", metric, fy24, "FY", 2024, "2024-01-01", "2024-12-31"),
            f(f"F-{tag}25", metric, fy25, "FY", 2025, "2025-01-01", "2025-12-31"),
            f(f"F-{tag}h125", metric, h125, "H1", 2025, "2025-01-01", "2025-06-30"),
            f(f"F-{tag}h126", metric, h126, "H1", 2026, "2026-01-01", "2026-06-30")]


def inst(fid, metric, value, end="2026-06-30", concept="us-gaap:X", filed=date(2026, 8, 1), unit="USD", dims=None):
    return f(fid, metric, value, "instant", None, None, end, concept, filed, dims, unit, "instant")


FACTS = (flows("revenue", 800, 1000, 450, 550, "rev") + flows("operating_income", 80, 100, 40, 60, "oi")
         + flows("d_and_a", 20, 30, 14, 16, "da") + flows("cost_of_revenue", 300, 400, 180, 220, "cor")
         + [inst("F-cash", "cash", 120), inst("F-cash-old", "cash", 999, end="2025-12-31"),
            inst("F-dc1", "debt_component", 200, concept="us-gaap:LongTermDebtNoncurrent"),
            inst("F-dc2", "debt_component", 10, concept="us-gaap:LongTermDebtCurrent"),
            inst("F-dc2-old-filing", "debt_component", 10, concept="us-gaap:LongTermDebtCurrent",
                 filed=date(2026, 7, 1)),
            inst("F-nci", "noncontrolling_interest", -5),
            inst("F-sa", "shares_outstanding", 90, end="2026-07-31", unit="shares",
                 dims={"us-gaap:StatementClassOfStockAxis": "A"}),
            inst("F-sb", "shares_outstanding", 10, end="2026-07-31", unit="shares",
                 dims={"us-gaap:StatementClassOfStockAxis": "B"}),
            inst("F-s-old", "shares_outstanding", 500, end="2026-02-01", unit="shares")])
PRICE = {"price_id": "PRICE-ABC-20260930", "value": 20.0, "unit": "USD/share", "tier": 1}


def test_row_uses_ltm_components_latest_balance_sheet_and_all_share_classes():
    comp = T.Comp("ABC", FACTS)
    r = T.build_row("ABC", comp, PRICE, date(2026, 9, 30))
    assert r["ltm_revenue"] == 1100 and r["ltm_basis"] == "FY2025 + H1 FY2026 - H1 FY2025"
    assert r["revenue_growth"] == pytest.approx(0.25)
    assert r["gross_margin"] == pytest.approx((1100 - 440) / 1100)
    assert r["ebitda_margin"] == pytest.approx((120 + 32) / 1100)
    assert r["debt_basis"] == "debt_components" and r["net_debt"] == 210 - 120
    assert r["enterprise_value"] == 20 * 100 + 210 - 5 - 120 and r["shares_date"] == "2026-07-31"
    assert r["ev_ebitda"] == pytest.approx(r["enterprise_value"] / 152)
    assert all(r[f"{n}_calc_id"].startswith("CALC-") for n, _, _ in T.METRICS)
    assert "preferred: not tagged" in r["notes"]


def test_a_missing_ytd_flow_leaves_the_cell_empty_with_a_reason():
    facts = [x for x in FACTS if x["fact_id"] not in ("F-dah126",)]
    r = T.build_row("ABC", T.Comp("ABC", facts), PRICE, date(2026, 9, 30))
    assert r["ebitda_margin"] == "" and r["ebitda_margin_calc_id"] == "" and r["ev_ebitda"] == ""
    assert "no H1 d_and_a" in r["notes"]


def test_stats_are_median_and_quartiles_over_present_values():
    rows = [{n: "" for n, _, _ in T.METRICS} | {"ltm_revenue": v} for v in (1.0, 2.0, 3.0, 4.0)]
    rows.append({n: "" for n, _, _ in T.METRICS})
    s = T.stats(rows)["ltm_revenue"]
    assert (s["n"], s["median"], s["q1"], s["q3"]) == (4, 2.5, 1.75, 3.25)


def write(tmp_path, body):
    p = tmp_path / "prices.csv"
    p.write_text("ticker,date,close,source_url\n" + body)
    return p


def test_price_file_check_passes_and_rejects_zero_wrong_date_and_missing_ticker(tmp_path):
    good = write(tmp_path, "AB,2026-09-30,12.5,https://finance.yahoo.com/quote/AB/history/\n"
                           "CD,2026-09-30,3,https://finance.yahoo.com/quote/CD/history/\n")
    rows = P.validate(good, ["AB", "CD"], "2026-09-30")
    p = P.price_inputs(rows, good)["AB"]
    assert (p["price_id"], p["value"], p["tier"]) == ("PRICE-AB-20260930", 12.5, 1)
    bad = write(tmp_path, "AB,2026-09-29,0.00,https://finance.yahoo.com/quote/AB/history/\n")
    with pytest.raises(P.PriceError) as e:
        P.validate(bad, ["AB", "CD"], "2026-09-30")
    assert "not positive" in str(e.value) and "want 2026-09-30" in str(e.value) and "exactly once" in str(e.value)
