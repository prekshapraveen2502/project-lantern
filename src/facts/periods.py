"""Part 1 item 2: fiscal labels from a period's dates and the company's fiscal year end (FYE).

fiscal_year   the calendar year in which the fiscal year ends (BILL FY2026 = 2025-07-01 .. 2026-06-30)
fiscal_period FY | Q1-Q4 | H1 | 9M | instant | other
A fiscal year end or quarter end matches within +/- tol_days (params facts.fye_tolerance_days), so 52/53-week
years ("last Saturday of September") still map. Quarter ends are the FYE moved back 3, 6 and 9 months (month-end
kept when the FYE is a month-end).
"""
from __future__ import annotations

import calendar
from datetime import date, timedelta


def _month_end(y: int, m: int) -> int:
    return calendar.monthrange(y, m)[1]


def fye_date(year: int, mmdd: str) -> date:
    m, d = int(mmdd[:2]), int(mmdd[2:])
    return date(year, m, min(d, _month_end(year, m)))


def _shift_months(d: date, months: int, month_end: bool) -> date:
    y, m = divmod(d.year * 12 + (d.month - 1) + months, 12)
    m += 1
    last = _month_end(y, m)
    return date(y, m, last if month_end else min(d.day, last))


def quarter_ends(fiscal_year: int, mmdd: str) -> list[date]:
    """[Q1 end, Q2 end, Q3 end, FY end] of a fiscal year."""
    fy_end = fye_date(fiscal_year, mmdd)
    is_month_end = fy_end.day == _month_end(fy_end.year, fy_end.month)
    return [_shift_months(fy_end, -3 * k, is_month_end) for k in (3, 2, 1)] + [fy_end]


def fiscal_year_of(d: date, mmdd: str, tol_days: int) -> int:
    tol = timedelta(days=tol_days)
    for fy in (d.year - 1, d.year, d.year + 1):
        if fye_date(fy - 1, mmdd) + tol < d <= fye_date(fy, mmdd) + tol:
            return fy
    raise ValueError(f"no fiscal year for {d} with FYE {mmdd}")


def fiscal_labels(start: date | None, end: date, mmdd: str, tol_days: int) -> tuple[int, str]:
    """(fiscal_year, fiscal_period) for an instant (start None) or a duration [start, end] (both inclusive)."""
    fy = fiscal_year_of(end, mmdd, tol_days)
    if start is None:
        return fy, "instant"
    q = next((i for i, qe in enumerate(quarter_ends(fy, mmdd), 1) if abs((end - qe).days) <= tol_days), None)
    days = (end - start).days + 1
    if q is None:
        return fy, "other"
    if q == 4 and 350 <= days <= 380:
        return fy, "FY"
    if 80 <= days <= 100:
        return fy, f"Q{q}"
    if q == 2 and 170 <= days <= 195:
        return fy, "H1"
    if q == 3 and 260 <= days <= 290:
        return fy, "9M"
    return fy, "other"
