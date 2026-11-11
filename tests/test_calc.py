"""Part 6 item 1: the calculators (pure; no store, no files). Values are made up for the arithmetic only."""
import pytest

from src.calc import core as calc


def fact(fid, value, period="FY", start="2025-01-01", end="2025-12-31", unit="USD", tier=1):
    return {"fact_id": fid, "value": value, "fiscal_period": period, "start": start, "end": end, "unit": unit,
            "tier": tier}


FY = fact("F-fy", 1000.0)
H1 = fact("F-h1", 600.0, "H1", "2026-01-01", "2026-06-30")
H1P = fact("F-h1p", 500.0, "H1", "2025-01-01", "2025-06-30")
PRICE = {"price_id": "PRICE-ABC-20260930", "value": 20.0, "unit": "USD/share", "tier": 1}


def inst(fid, value, unit="USD", tier=1):
    return {"fact_id": fid, "value": value, "unit": unit, "tier": tier}


def test_ltm_is_fy_plus_ytd_minus_prior_ytd_with_trace():
    out = calc.ltm(FY, H1, H1P)
    assert out["value"] == 1100.0 and out["op"] == "ltm" and out["unit"] == "USD"
    assert out["fact_ids"] == ["F-fy", "F-h1", "F-h1p"]
    assert [t["role"] for t in out["trace"]] == ["fy", "ytd", "prior_ytd"]
    assert out["calc_id"].startswith("CALC-") and out["calc_id"] == calc.ltm(dict(FY), dict(H1), dict(H1P))["calc_id"]


def test_ltm_with_a_nine_month_ytd_and_a_june_year_end():
    fy = fact("F-fy", 1200.0, "FY", "2024-07-01", "2025-06-30")
    cur = fact("F-9m", 1000.0, "9M", "2025-07-01", "2026-03-31")
    pri = fact("F-9mp", 850.0, "9M", "2024-07-01", "2025-03-31")
    assert calc.ltm(fy, cur, pri)["value"] == 1350.0


@pytest.mark.parametrize("ytd,prior,why", [
    (H1, fact("F-q", 250.0, "Q2", "2025-04-01", "2025-06-30"), "YTD periods differ"),
    (H1, fact("F-old", 450.0, "H1", "2024-01-01", "2024-06-30"), "does not end inside the FY"),
    (fact("F-h1x", 600.0, "H1", "2026-07-01", "2026-12-31"), H1P, "days after"),
])
def test_ltm_refuses_mismatched_periods(ytd, prior, why):
    with pytest.raises(calc.CalcError, match=why):
        calc.ltm(FY, ytd, prior)


def test_ltm_refuses_mixed_units():
    with pytest.raises(calc.CalcError, match="different units"):
        calc.ltm(FY, {**H1, "unit": "EUR"}, H1P)


def test_ev_is_price_times_all_share_classes_plus_claims_minus_cash():
    out = calc.enterprise_value(PRICE, [inst("F-sa", 90.0, "shares"), inst("F-sb", 10.0, "shares")],
                                debt=[inst("F-debt", 300.0)], preferred=[inst("F-pref", 50.0)],
                                nci=[inst("F-nci", 25.0)], cash=[inst("F-cash", 200.0), inst("F-sti", 75.0)])
    assert out["value"] == 20.0 * 100 + 300 + 50 + 25 - 200 - 75 == 2100.0
    assert out["fact_ids"] == ["PRICE-ABC-20260930", "F-sa", "F-sb", "F-debt", "F-pref", "F-nci", "F-cash", "F-sti"]
    assert out["unit"] == "USD" and out["tier"] == 1


def test_ev_calc_id_depends_on_roles():
    a = calc.enterprise_value(PRICE, [inst("F-s", 1.0, "shares")], [inst("F-x", 5.0)], [], [], [])
    b = calc.enterprise_value(PRICE, [inst("F-s", 1.0, "shares")], [], [], [], [inst("F-x", 5.0)])
    assert a["calc_id"] != b["calc_id"] and a["value"] == 25.0 and b["value"] == 15.0


def test_ev_without_shares_or_price_is_refused():
    with pytest.raises(calc.CalcError, match="shares"):
        calc.enterprise_value(PRICE, [], [], [], [], [])
    with pytest.raises(calc.CalcError, match="price"):
        calc.enterprise_value({**PRICE, "value": 0}, [inst("F-s", 1.0, "shares")], [], [], [], [])


def test_sign_convention_operating_loss_and_net_cash_are_kept_negative():
    """Costs are stored positive; a loss and a net-cash position stay negative; a negative EBITDA has no multiple."""
    e = calc.ebitda(inst("F-oi", -120.0), inst("F-da", 40.0))
    assert e["value"] == -80.0
    nd = calc.net_debt([inst("F-debt", 100.0)], [inst("F-cash", 250.0)])
    assert nd["value"] == -150.0
    gp = calc.gross_profit(inst("F-rev", 1000.0), inst("F-cor", 400.0))
    assert gp["value"] == 600.0
    with pytest.raises(calc.CalcError, match="n.m."):
        calc.multiple(inst("F-ev", 5000.0), e, "ev_ebitda")


@pytest.mark.parametrize("fn,args,role", [
    (calc.gross_profit, (inst("F-rev", 1000.0), inst("F-cor", -400.0)), "cost of revenue"),
    (calc.ebitda, (inst("F-oi", 100.0), inst("F-da", -40.0)), "D&A"),
    (calc.net_debt, ([inst("F-debt", -100.0)], []), "debt"),
])
def test_sign_convention_a_negative_cost_is_refused_not_flipped(fn, args, role):
    with pytest.raises(calc.CalcError, match=role):
        fn(*args)


def test_margins_and_multiples_chain_through_calcs():
    rev = calc.ltm(FY, H1, H1P)                                         # 1100
    oi = calc.ltm(fact("F-oifya", 200.0), fact("F-oih1", 130.0, "H1", "2026-01-01", "2026-06-30"),
                  fact("F-oih1p", 110.0, "H1", "2025-01-01", "2025-06-30"))   # 220
    da = calc.ltm(fact("F-dafy", 50.0), fact("F-dah1", 30.0, "H1", "2026-01-01", "2026-06-30"),
                  fact("F-dah1p", 25.0, "H1", "2025-01-01", "2025-06-30"))    # 55
    e = calc.ebitda(oi, da)
    assert e["value"] == 275.0
    m = calc.margin(e, rev)
    assert m["value"] == pytest.approx(0.25) and m["unit"] == "pure"
    ev = calc.enterprise_value(PRICE, [inst("F-s", 100.0, "shares")], [inst("F-d", 200.0)], [], [], [inst("F-c", 50.0)])
    assert calc.multiple(ev, rev, "ev_revenue")["value"] == pytest.approx(2150.0 / 1100.0)
    mult = calc.multiple(ev, e, "ev_ebitda")
    assert mult["value"] == pytest.approx(2150.0 / 275.0) and mult["unit"] == "x"
    assert mult["fact_ids"][:2] == ["PRICE-ABC-20260930", "F-s"] and "F-oih1p" in mult["fact_ids"]


def test_margin_refuses_non_positive_revenue():
    with pytest.raises(calc.CalcError, match="not positive"):
        calc.margin(inst("F-gp", 10.0), inst("F-rev", 0.0))


def test_derived_tier_is_the_highest_input_tier():
    assert calc.net_debt([inst("F-d", 10.0, tier=3)], [inst("F-c", 1.0, tier=1)])["tier"] == 3


def test_part4_ops_are_unchanged():
    g = calc.growth(fact("F-a", 110.0), fact("F-b", 100.0))
    assert set(g) == {"calc_id", "op", "value", "unit", "tier", "formula", "trace"}
    assert g["calc_id"] == calc.calc_id("growth", ["F-a", "F-b"]) and g["value"] == pytest.approx(0.1)
