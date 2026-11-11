"""Pure calculators. Part 4 (decision Q1 a): growth, ratio/share and difference. Part 6 item 1 adds LTM, gross
profit, margins, EBITDA, net debt, enterprise value and multiples to the same interface.

Every output carries a calc_id and a trace of its input fact_ids. calc_id = "CALC-" + the first 12 hex digits of
sha256 over (op, input ids in argument order), so the same calculation on the same inputs always has the same id.
A derived value inherits the lowest tier of its inputs (brief Section 5: the highest tier number). Outputs are
kept in a registry, outputs/qa/calcs.jsonl, one JSON object per calc_id; GET /calc/{calc_id} reads it.

Part 6 conventions (stated in reports/comps_and_figures.md):
  inputs     a fact (fact_id), an earlier calc output (calc_id) or a snapshot price (price_id, tier 1: the brief's
             fixed reference input). Part 6 outputs add "fact_ids": every fact_id and price_id the value rests on,
             through nested calcs, in order. The Part 4 ops' outputs are unchanged.
  ids        Part 6 ops hash "role:id" pairs (e.g. "debt:F-..."), so the same fact in a different role is a
             different calculation.
  signs      the store keeps costs positive (metrics_vocab.yaml sign: cost; a negative data-room cost is flipped at
             load). Costs, D&A, debt, cash, preferred and NCI must be >= 0 here; a negative one is refused
             (CalcError), never silently flipped. Operating income, EBITDA and net debt may be negative.
  LTM        latest FY + current YTD - prior-year YTD; both YTDs the same fiscal_period label (e.g. H1), the prior
             YTD ending inside the FY and the current YTD ending about a year later.
  EBITDA     operating income + depreciation and amortization (cash-flow D&A), on the same basis (LTM or FY).
  EV         price x shares outstanding (sum of the classes given) + debt + preferred + NCI - cash - short-term
             investments (short-term investments are treated as cash-like; the caller passes only those tagged).
  multiples  EV / revenue, EV / EBITDA; a denominator <= 0 is not meaningful (CalcError "n.m.").
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

OPS = ("growth", "ratio", "difference",                                   # Part 4
       "ltm", "gross_profit", "ebitda", "margin", "net_debt", "enterprise_value", "ev_revenue", "ev_ebitda")  # Part 6


class CalcError(ValueError):
    """An input the calculator cannot use (missing value, zero denominator)."""


def calc_id(op: str, input_ids: list[str]) -> str:
    return "CALC-" + hashlib.sha256(json.dumps([op, list(input_ids)]).encode()).hexdigest()[:12]


def _value(f: dict) -> float:
    if f.get("value") is None:
        raise CalcError(f"{f.get('fact_id')}: no value")
    return float(f["value"])


def _out(op: str, inputs: list[dict], value: float, formula: str) -> dict:
    ids = [f["fact_id"] for f in inputs]
    return {"calc_id": calc_id(op, ids), "op": op, "value": value, "unit": "pure" if op != "difference" else
            inputs[0].get("unit"), "tier": max(int(f["tier"]) for f in inputs), "formula": formula,
            "trace": [{"fact_id": f["fact_id"], "value": _value(f), "tier": int(f["tier"])} for f in inputs]}


def growth(current: dict, prior: dict) -> dict:
    """current / prior - 1."""
    p = _value(prior)
    if p == 0:
        raise CalcError(f"{prior['fact_id']}: prior value is zero")
    return _out("growth", [current, prior], _value(current) / p - 1, "a / b - 1")


def ratio(numerator: dict, denominator: dict) -> dict:
    """numerator / denominator (a share when the numerator is part of the denominator)."""
    d = _value(denominator)
    if d == 0:
        raise CalcError(f"{denominator['fact_id']}: denominator is zero")
    return _out("ratio", [numerator, denominator], _value(numerator) / d, "a / b")


def difference(a: dict, b: dict) -> dict:
    """a - b, in a's unit; both inputs must share a unit."""
    if a.get("unit") != b.get("unit"):
        raise CalcError(f"{a['fact_id']} and {b['fact_id']} have different units")
    return _out("difference", [a, b], _value(a) - _value(b), "a - b")


# ---- Part 6 item 1 ----------------------------------------------------------------------------------------------
LTM_MIN_DAYS, LTM_MAX_DAYS = 358, 372        # the current YTD ends about one year after the prior-year YTD


def _id(x: dict) -> str:
    for k in ("calc_id", "fact_id", "price_id"):
        if x.get(k):
            return x[k]
    raise CalcError(f"input without an id: {x}")


def _leaves(x: dict) -> list[str]:
    if x.get("calc_id"):
        return list(x.get("fact_ids") or [t["fact_id"] for t in x["trace"]])
    return [_id(x)]


def _nonneg(x: dict, role: str) -> float:
    v = _value(x)
    if v < 0:
        raise CalcError(f"{_id(x)}: {role} is negative ({v}); the store keeps it >= 0")
    return v


def _same_unit(items: list[dict]) -> str | None:
    units = {x.get("unit") for x in items}
    if len(units) > 1:
        raise CalcError(f"inputs have different units: {sorted(map(str, units))}")
    return units.pop() if units else None


def _out6(op: str, roles: list[tuple[str, dict]], value: float, formula: str, unit: str | None) -> dict:
    ids = [f"{role}:{_id(x)}" for role, x in roles]
    leaves: list[str] = []
    for _, x in roles:
        leaves += [i for i in _leaves(x) if i not in leaves]
    return {"calc_id": calc_id(op, ids), "op": op, "value": value, "unit": unit,
            "tier": max(int(x["tier"]) for _, x in roles), "formula": formula,
            "trace": [{"role": role, "id": _id(x), "value": _value(x), "tier": int(x["tier"])} for role, x in roles],
            "fact_ids": leaves}


def _date(x: dict, key: str):
    from datetime import date
    v = x.get(key)
    if v is None:
        raise CalcError(f"{_id(x)}: no {key}")
    return v if isinstance(v, date) else date.fromisoformat(str(v)[:10])


def ltm(fy: dict, ytd: dict, prior_ytd: dict) -> dict:
    """Last twelve months = latest FY + current YTD - prior-year YTD."""
    if fy.get("fiscal_period") != "FY":
        raise CalcError(f"{_id(fy)}: not a fiscal-year value ({fy.get('fiscal_period')})")
    if ytd.get("fiscal_period") != prior_ytd.get("fiscal_period") or ytd.get("fiscal_period") in (None, "FY"):
        raise CalcError(f"{_id(ytd)} and {_id(prior_ytd)}: YTD periods differ "
                        f"({ytd.get('fiscal_period')} vs {prior_ytd.get('fiscal_period')})")
    if not _date(fy, "start") < _date(prior_ytd, "end") <= _date(fy, "end"):
        raise CalcError(f"{_id(prior_ytd)}: the prior-year YTD does not end inside the FY")
    gap = (_date(ytd, "end") - _date(prior_ytd, "end")).days
    if not LTM_MIN_DAYS <= gap <= LTM_MAX_DAYS:
        raise CalcError(f"{_id(ytd)}: current YTD ends {gap} days after the prior-year YTD (want about a year)")
    unit = _same_unit([fy, ytd, prior_ytd])
    v = _value(fy) + _value(ytd) - _value(prior_ytd)
    return _out6("ltm", [("fy", fy), ("ytd", ytd), ("prior_ytd", prior_ytd)], v, "fy + ytd - prior_ytd", unit)


def gross_profit(revenue: dict, cost_of_revenue: dict) -> dict:
    """revenue - cost of revenue (cost stored positive)."""
    unit = _same_unit([revenue, cost_of_revenue])
    v = _value(revenue) - _nonneg(cost_of_revenue, "cost of revenue")
    return _out6("gross_profit", [("revenue", revenue), ("cost_of_revenue", cost_of_revenue)], v,
                 "revenue - cost_of_revenue", unit)


def ebitda(operating_income: dict, d_and_a: dict) -> dict:
    """EBITDA = operating income + D&A (D&A stored positive; operating income may be negative)."""
    unit = _same_unit([operating_income, d_and_a])
    v = _value(operating_income) + _nonneg(d_and_a, "D&A")
    return _out6("ebitda", [("operating_income", operating_income), ("d_and_a", d_and_a)], v,
                 "operating_income + d_and_a", unit)


def margin(numerator: dict, revenue: dict) -> dict:
    """numerator / revenue (gross profit or EBITDA over revenue); revenue must be > 0."""
    _same_unit([numerator, revenue])
    r = _value(revenue)
    if r <= 0:
        raise CalcError(f"{_id(revenue)}: revenue is not positive")
    return _out6("margin", [("numerator", numerator), ("revenue", revenue)], _value(numerator) / r,
                 "numerator / revenue", "pure")


def net_debt(debt: list[dict], cash: list[dict]) -> dict:
    """sum(debt) - sum(cash and cash-like items); may be negative (net cash)."""
    if not debt and not cash:
        raise CalcError("net debt needs at least one input")
    unit = _same_unit(debt + cash)
    v = sum(_nonneg(d, "debt") for d in debt) - sum(_nonneg(c, "cash") for c in cash)
    return _out6("net_debt", [("debt", d) for d in debt] + [("cash", c) for c in cash], v,
                 "sum(debt) - sum(cash)", unit)


def enterprise_value(price: dict, shares: list[dict], debt: list[dict], preferred: list[dict], nci: list[dict],
                     cash: list[dict]) -> dict:
    """price x sum(shares) + debt + preferred + NCI - cash (cash includes short-term investments if passed)."""
    if not shares:
        raise CalcError("EV needs shares outstanding")
    p = _value(price)
    if p <= 0:
        raise CalcError(f"{_id(price)}: price is not positive")
    n = sum(_nonneg(s, "shares") for s in shares)
    unit = _same_unit(debt + preferred + nci + cash) or "USD"
    v = (p * n + sum(_nonneg(d, "debt") for d in debt) + sum(_nonneg(x, "preferred") for x in preferred)
         + sum(_nonneg(x, "NCI") for x in nci) - sum(_nonneg(c, "cash") for c in cash))
    roles = ([("price", price)] + [("shares", s) for s in shares] + [("debt", d) for d in debt]
             + [("preferred", x) for x in preferred] + [("nci", x) for x in nci] + [("cash", c) for c in cash])
    return _out6("enterprise_value", roles, v, "price * sum(shares) + debt + preferred + nci - cash", unit)


def multiple(ev: dict, denominator: dict, name: str) -> dict:
    """EV / revenue or EV / EBITDA; a denominator <= 0 is not meaningful."""
    d = _value(denominator)
    if d <= 0:
        raise CalcError(f"n.m.: {name} denominator {_id(denominator)} is {d} (not positive)")
    _same_unit([ev, denominator])
    return _out6(name, [("ev", ev), ("denominator", denominator)], _value(ev) / d, "ev / denominator", "x")


def load(registry: Path) -> dict[str, dict]:
    if not Path(registry).exists():
        return {}
    return {r["calc_id"]: r for r in (json.loads(l) for l in open(registry, encoding="utf-8") if l.strip())}


def register(calcs: list[dict], registry: Path) -> int:
    """Append calcs not yet in the registry; returns how many were added."""
    have = load(registry)
    new = []
    for c in calcs:
        if c["calc_id"] not in have:
            have[c["calc_id"]] = c
            new.append(c)
    if new:
        Path(registry).parent.mkdir(parents=True, exist_ok=True)
        with open(registry, "a", encoding="utf-8") as f:
            for c in new:
                f.write(json.dumps(c, sort_keys=True) + "\n")
    return len(new)
