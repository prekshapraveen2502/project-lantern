"""Part 5: link profile numbers to Part 1 claims or tier-3 facts, and strategy (c), staged + Part 1 reconciliation
(decision 2026-11-07 Q3 a).

Link (all strategies): a number is linked to the Part 1 claim located in the same document block with the same
vocabulary metric (several: the closest value; a field with no metric also needs the value within
reconcile.tolerance_rel). Otherwise, a number with a metric and a fiscal-year period is linked to the tier-3 fact
for that metric and year when the values agree within reconcile.tolerance_rel (derived metrics: the calc over the
input facts, calc_id from src/calc/core.py). An unlinked number keeps ref null; the memo payload leaves it out
(decision Q7).

Reconcile (strategy c): a number linked to a claim whose reconcile status is discrepancy or definition_mismatch takes
the tier-3 value (fact, or calc for revenue_growth / subscription_share) that reconcile compared it with; the claimed
value, comparator and status are kept in Num.reconcile. period_mismatch, match and no_fact keep the claimed value.
"""
from __future__ import annotations

import re

from src.calc import core as calc
from src.extract.profile import DealProfile, Num, Reconciled, iter_nums
from src.facts.reconcile import DERIVED_INPUTS, fact_for

REPLACE = ("discrepancy", "definition_mismatch")
FY = re.compile(r"^FY(\d{4})$")


def _close(a: float, b: float, tol: float) -> bool:
    return a == b if b == 0 else abs(a - b) / abs(b) <= tol


def tier3_ref(metric: str, fy: int, idx: dict, facts_by_id: dict) -> tuple[float, str] | None:
    """(value, fact_id or calc_id) of the tier-3 value for metric at FY fy, as Part 1 reconcile computes it."""
    hit = fact_for(metric, fy, "FY", idx)
    if hit is None:
        return None
    value, fact_id, inputs = hit
    if metric in DERIVED_INPUTS:
        a, b = (facts_by_id[i] for i in inputs)
        c = calc.growth(a, b) if metric == "revenue_growth" else calc.ratio(a, b)
        return c["value"], c["calc_id"]
    return value, fact_id


def link(p: DealProfile, claims: list[dict], idx: dict, facts_by_id: dict, tol: float, log: list[dict]) -> None:
    for fid, metric, n in iter_nums(p):
        cands = [c for c in claims if c["doc_id"] == n.source.doc_id and c["block_id"] == n.source.block_id
                 and c["metric"] == metric and (metric is not None or _close(n.value, c["value"], tol))]
        if cands:
            n.ref = min(cands, key=lambda c: (abs(c["value"] - n.value), c["claim_id"]))["claim_id"]
            continue
        m = FY.match(n.period or "")
        if metric and m:
            hit = tier3_ref(metric, int(m.group(1)), idx, facts_by_id)
            if hit and _close(n.value, hit[0], tol):
                n.ref = hit[1]
                continue
        log.append({"field": fid, "event": "unlinked", "value": n.value})


def reconcile(p: DealProfile, claims: dict, discrepancies: dict, idx: dict, facts_by_id: dict,
              log: list[dict]) -> None:
    """In place: strategy (c) on a linked profile. claims and discrepancies are keyed by claim_id."""
    for fid, metric, n in iter_nums(p):
        if not (n.ref or "").startswith("C-"):
            continue
        d, c = discrepancies.get(n.ref), claims[n.ref]
        if d is None or d["status"] not in REPLACE:
            continue
        hit = fact_for(c["metric"], c["fiscal_year"], c["fiscal_period"], idx)
        if hit is None:
            log.append({"field": fid, "event": "reconcile_no_fact", "claim_id": n.ref})
            continue
        value, ref = tier3_ref(c["metric"], c["fiscal_year"], idx, facts_by_id) \
            if c["fiscal_period"] == "FY" else (hit[0], hit[1])
        n.reconcile = Reconciled(claim_id=n.ref, status=d["status"], fact_id=d["fact_id"], gap=d["gap"],
                                 claimed_value=n.value, claimed_comparator=n.comparator)
        n.value, n.ref, n.comparator = value, ref, "eq"
        log.append({"field": fid, "event": "reconciled", "status": d["status"], "claimed": n.reconcile.claimed_value,
                    "value": value, "ref": ref})
