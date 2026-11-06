"""Part 1 item 6: reconcile management claims (tier 4) with seller facts (tier 3) -> data/facts/discrepancies.jsonl.

One record per claim with exactly the brief's section 8 fields: metric, period, claim_id, fact_id, status, gap,
severity. Checks, in order (DECISIONS 2026-11-06, Phase A answers C7, C10-C14, C21):
  no_fact             the claim has no metric, or the deal has no tier-3 fact for it in any period
  period_mismatch     a calendar-period claim while the deal's fiscal year end is not 12-31 (fiscal/calendar mix),
                      or no fact for the claim's fiscal period although the metric has facts in other periods
  definition_mismatch the claim carries the qualifier run-rate, or its value matches (within tolerance) another
                      metric's fact for the same period but not its own
  match / discrepancy gap = (claim - fact) / |fact|; |gap| <= reconcile.tolerance_rel -> match. A bound claim
                      (lt/le/gt/ge) that the fact satisfies is a match. Discrepancy severity: high when
                      |gap| > reconcile.high_gap_rel, otherwise medium.
Derived metrics are computed from tier-3 facts (only these two): revenue_growth = revenue(FY) / revenue(FY-1) - 1,
subscription_share = subscription_revenue / revenue; fact_id is the current-period input fact (revenue,
subscription_revenue).
The fiscal year end is detected from the deal's tier-3 FY facts. The answer key is never read here.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import duckdb
import yaml

FIELDS = ["metric", "period", "claim_id", "fact_id", "status", "gap", "severity"]
DERIVED_INPUTS = {"revenue_growth": ("revenue",), "subscription_share": ("subscription_revenue", "revenue")}


class ReconcileError(ValueError):
    """A case the reconcile rules do not cover: the run stops."""


def fiscal_year_end(facts: list[dict]) -> str:
    ends = {str(f["end"])[5:].replace("-", "") for f in facts if f["fiscal_period"] == "FY"}
    if len(ends) != 1:
        raise ReconcileError(f"no single fiscal year end in the deal's FY facts: {sorted(ends)}")
    return ends.pop()


def index_facts(facts: list[dict]) -> dict:
    """(metric, fiscal_year, fiscal_period) -> one fact. Equal duplicates: the lowest fact_id; different: stop."""
    groups: dict = {}
    for f in facts:
        if f["metric"]:
            groups.setdefault((f["metric"], f["fiscal_year"], f["fiscal_period"]), []).append(f)
    idx = {}
    for key, fs in groups.items():
        if len({f["value"] for f in fs}) > 1:
            raise ReconcileError(f"{key}: tier-3 facts disagree: {[(f['fact_id'], f['value']) for f in fs]}")
        idx[key] = min(fs, key=lambda f: f["fact_id"])
    return idx


def fact_for(metric: str, fy: int, fp: str, idx: dict) -> tuple[float, str, list[str]] | None:
    """(value, fact_id, input fact ids) for a reported or derived metric at a fiscal period, or None."""
    if metric == "revenue_growth":
        cur, prev = idx.get(("revenue", fy, fp)), idx.get(("revenue", fy - 1, fp))
        if cur and prev and fp == "FY":
            return cur["value"] / prev["value"] - 1, cur["fact_id"], [cur["fact_id"], prev["fact_id"]]
        return None
    if metric == "subscription_share":
        sub, rev = idx.get(("subscription_revenue", fy, fp)), idx.get(("revenue", fy, fp))
        if sub and rev:
            return sub["value"] / rev["value"], sub["fact_id"], [sub["fact_id"], rev["fact_id"]]
        return None
    f = idx.get((metric, fy, fp))
    return (f["value"], f["fact_id"], [f["fact_id"]]) if f else None


def has_any_fact(metric: str, idx: dict) -> bool:
    needed = DERIVED_INPUTS.get(metric, (metric,))
    return all(any(k[0] == m for k in idx) for m in needed)


def gap_of(claim: float, fact: float) -> float:
    if fact == 0:
        raise ReconcileError("fact value 0: gap undefined")
    return (claim - fact) / abs(fact)


def satisfied(comparator: str, claim: float, fact: float) -> bool:
    return {"lt": fact < claim, "le": fact <= claim, "gt": fact > claim, "ge": fact >= claim}.get(comparator, False)


def period_text(c: dict) -> str:
    return c["period_label"] or f"FY{c['fiscal_year']}"


def reconcile_claim(c: dict, idx: dict, fye: str, tol: float, high: float) -> dict:
    rec = {"metric": c["metric"], "period": period_text(c), "claim_id": c["claim_id"], "fact_id": None,
           "status": None, "gap": None, "severity": None}
    if not c["metric"] or not has_any_fact(c["metric"], idx):
        rec["status"] = "no_fact"
        return rec
    if c["period_basis"] == "calendar" and fye != "1231":
        rec["status"] = "period_mismatch"
        return rec
    hit = fact_for(c["metric"], c["fiscal_year"], c["fiscal_period"], idx)
    if hit is None:
        rec["status"] = "period_mismatch"
        return rec
    fact, rec["fact_id"], _ = hit
    rec["gap"] = gap_of(c["value"], fact)
    within = abs(rec["gap"]) <= tol
    if "run-rate" in (c["qualifier"] or []):
        rec["status"] = "definition_mismatch"
        return rec
    if not within and c["comparator"] in ("eq", "approx"):
        for (m, fy, fp), other in idx.items():
            if m != c["metric"] and (fy, fp) == (c["fiscal_year"], c["fiscal_period"]) and other["value"] != 0 \
                    and abs(gap_of(c["value"], other["value"])) <= tol:
                rec["status"] = "definition_mismatch"
                return rec
    if within or satisfied(c["comparator"], c["value"], fact):
        rec["status"] = "match"
    else:
        rec["status"] = "discrepancy"
        rec["severity"] = "high" if abs(rec["gap"]) > high else "medium"
    return rec


def reconcile_deal(claims: list[dict], facts: list[dict], tol: float, high: float) -> tuple[list[dict], str]:
    fye = fiscal_year_end(facts)
    idx = index_facts(facts)
    return [reconcile_claim(c, idx, fye, tol, high) for c in sorted(claims, key=lambda c: c["claim_id"])], fye


def load(con, table: str, deal: str, where: str = "") -> list[dict]:
    cur = con.execute(f"SELECT * FROM {table} WHERE deal_id = ? {where}", [deal])
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def main() -> None:
    ap = argparse.ArgumentParser(description="Part 1 reconcile stage")
    ap.add_argument("--deals", nargs="+", required=True)
    ap.add_argument("--db", default="data/facts/facts.duckdb")
    ap.add_argument("--out", default="data/facts/discrepancies.jsonl")
    ap.add_argument("--params", default="params.yaml")
    args = ap.parse_args()
    r = yaml.safe_load(Path(args.params).read_text(encoding="utf-8"))["reconcile"]
    tol, high = float(r["tolerance_rel"]), float(r["high_gap_rel"])
    con = duckdb.connect(args.db, read_only=True)
    out = []
    for deal in args.deals:
        facts = load(con, "facts", deal, "AND tier = 3")
        claims = load(con, "claims", deal)
        recs, fye = reconcile_deal(claims, facts, tol, high)
        out += recs
        counts = {s: sum(1 for x in recs if x["status"] == s) for s in
                  ("match", "discrepancy", "definition_mismatch", "period_mismatch", "no_fact")}
        print(f"[INFO] reconcile {deal}: fiscal year end {fye}; {len(recs)} claims; {counts}")
    con.close()
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    with open(args.out, "w", encoding="utf-8", newline="\n") as fh:
        for rec in out:
            fh.write(json.dumps({k: rec[k] for k in FIELDS}) + "\n")
    print(f"[INFO] reconcile: {len(out)} records -> {args.out}")


if __name__ == "__main__":
    main()
