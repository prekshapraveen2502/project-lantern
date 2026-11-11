"""Part 6 item 2: the `comps_table` stage -> outputs/comps/{deal}.csv, {deal}.md, {deal}_calcs.jsonl (every calc
with its trace). With --spotcheck (once, outside the stage) it also writes {deal}_spotcheck.md, the 10-cell checklist
filled in by hand; it is never overwritten.

The comparables are params comps.tickers, chosen for the variant's sector archetype (reports/comps_selection.md);
the table uses no data-room file, so outputs/comps/deal_a.* is built before Deal A exists (DECISIONS 2026-11-10).
Inputs: tier-1 XBRL facts in data/facts/facts.duckdb (deal_id public) and the price snapshot
(params reference.price_snapshot, checked by src/comps/prices.py). Every value is a src/calc/core.py output.

Fact choice, per comparable (entity_id = the CIK in data/edgar_comps/{T}/submissions.json):
  - undimensioned facts only, except shares outstanding (one fact per share class);
  - duplicates of one (metric, period) from several filings: the latest filed, then the lowest fact_id;
  - flows: LTM of the latest fiscal year (calc.ltm). The comparable's basis comes from revenue: if a YTD follows
    its latest FY, every flow must have that YTD and its prior-year twin, else the cell is empty with a reason;
  - growth: latest FY revenue over the prior FY (calc.growth); no prior-year LTM exists in the ingested filings;
  - gross margin: gross_profit if tagged, else revenue - cost_of_revenue, else empty (not reported);
  - balance sheet: every item at the latest cash date on or before the price date. Debt is the filer's own total
    (total_debt) where tagged, else the sum of its debt_component facts at that date; cash is cash and cash
    equivalents, plus short-term investments where tagged; preferred and NCI where tagged. An item not tagged at
    that date is left out and named in notes;
  - shares: every class of dei:EntityCommonStockSharesOutstanding at its latest date on or before the price date.
Summary statistics (median, Q1, Q3: numpy percentile, linear) over the comparables with a value are in the Markdown
only; the CSV has one row per comparable (brief Section 8).
"""
from __future__ import annotations

import argparse
import csv
import json
from datetime import date
from pathlib import Path

import duckdb
import numpy as np
import yaml

from src.calc import core as calc
from src.comps import prices as P

METRICS = [("ltm_revenue", "LTM revenue", "usd"), ("revenue_growth", "Revenue growth (FY)", "pct"),
           ("gross_margin", "Gross margin (LTM)", "pct"), ("ebitda_margin", "EBITDA margin (LTM)", "pct"),
           ("net_debt", "Net debt", "usd"), ("enterprise_value", "Enterprise value", "usd"),
           ("ev_revenue", "EV / LTM revenue", "x"), ("ev_ebitda", "EV / LTM EBITDA", "x")]
YTD_PERIODS = ("Q1", "H1", "9M")
# the 10 spot-check cells: every metric and every comparable at least once, only cells with a value (BILL's
# EV/EBITDA is n.m.: its LTM EBITDA is negative)
SPOT = [("BILL", "ev_revenue"), ("BILL", "enterprise_value"), ("FLYW", "ltm_revenue"), ("PAYO", "ebitda_margin"),
        ("FOUR", "ev_ebitda"), ("TOST", "gross_margin"), ("GPN", "revenue_growth"), ("PYPL", "enterprise_value"),
        ("XYZ", "net_debt"), ("TOST", "ev_revenue")]
FACT_COLS = ("fact_id", "entity_id", "concept", "metric", "value", "unit", "period_type", "start", "end",
             "fiscal_year", "fiscal_period", "dims", "tier", "doc_id", "filed", "citation")


def ciks(tickers: list[str], root: Path = Path("data/edgar_comps")) -> dict[str, dict]:
    out = {}
    for t in tickers:
        s = json.load(open(root / t / "submissions.json"))
        out[t] = {"cik": str(s["cik"]).zfill(10), "name": s.get("name")}
    return out


def load_facts(db: Path, cik: str) -> list[dict]:
    con = duckdb.connect(str(db), read_only=True)
    cols = ", ".join('"' + c + '"' for c in FACT_COLS)
    cur = con.execute(f"SELECT {cols} FROM facts WHERE deal_id = 'public' AND entity_id = ?", [cik])
    rows = [dict(zip(FACT_COLS, r)) for r in cur.fetchall()]
    con.close()
    for r in rows:
        r["dims"] = json.loads(r["dims"]) if isinstance(r["dims"], str) and r["dims"] else (r["dims"] or {})
    return rows


def latest_filed(fs: list[dict]) -> dict:
    return sorted(fs, key=lambda f: (-(f["filed"].toordinal() if f["filed"] else 0), f["fact_id"]))[0]


class Comp:
    """One comparable's facts and the calcs built from them."""

    def __init__(self, ticker: str, facts: list[dict]):
        self.ticker, self.calcs, self.notes = ticker, [], []
        self.facts = [f for f in facts if not f["dims"]]
        self.shares_facts = [f for f in facts if f["metric"] == "shares_outstanding"]

    def keep(self, c: dict) -> dict:
        self.calcs.append(c)
        return c

    def flow(self, metric: str, fp: str, fy: int) -> dict | None:
        fs = [f for f in self.facts if f["metric"] == metric and f["fiscal_period"] == fp and f["fiscal_year"] == fy]
        return latest_filed(fs) if fs else None

    def latest_fy(self, metric: str) -> int | None:
        years = [f["fiscal_year"] for f in self.facts if f["metric"] == metric and f["fiscal_period"] == "FY"]
        return max(years) if years else None

    def basis(self) -> tuple[int, str | None, int | None]:
        """(latest FY, YTD period label or None, YTD fiscal year) from revenue."""
        fy = self.latest_fy("revenue")
        fy_end = self.flow("revenue", "FY", fy)["end"]
        ytd = [f for f in self.facts if f["metric"] == "revenue" and f["fiscal_period"] in YTD_PERIODS
               and f["start"] and f["start"] > fy_end]
        if not ytd:
            return fy, None, None
        cur = max(ytd, key=lambda f: f["end"])
        return fy, cur["fiscal_period"], cur["fiscal_year"]

    def ltm(self, metric: str) -> dict:
        fy, fp, yfy = self.basis()
        f = self.flow(metric, "FY", fy)
        if f is None:
            raise calc.CalcError(f"no FY{fy} {metric}")
        if fp is None:
            return self.keep(calc.ltm(f))
        cur, prior = self.flow(metric, fp, yfy), self.flow(metric, fp, yfy - 1)
        if cur is None or prior is None:
            raise calc.CalcError(f"no {fp} {metric} for FY{yfy} and FY{yfy - 1} (needed for LTM)")
        return self.keep(calc.ltm(f, cur, prior))

    def at(self, metric: str, end: date) -> list[dict]:
        """One fact per concept for an instant metric at a date (latest filed)."""
        by_concept: dict[str, list[dict]] = {}
        for f in self.facts:
            if f["metric"] == metric and f["end"] == end and f["period_type"] == "instant":
                by_concept.setdefault(f["concept"], []).append(f)
        return [latest_filed(v) for _, v in sorted(by_concept.items())]

    def bs_date(self, asof: date) -> date:
        ends = [f["end"] for f in self.facts if f["metric"] == "cash" and f["end"] <= asof]
        if not ends:
            raise calc.CalcError("no cash fact on or before the price date")
        return max(ends)

    def shares(self, asof: date) -> list[dict]:
        fs = [f for f in self.shares_facts if f["end"] <= asof]
        if not fs:
            raise calc.CalcError("no shares outstanding on or before the price date")
        last = max(f["end"] for f in fs)
        by_class: dict[str, list[dict]] = {}
        for f in fs:
            if f["end"] == last:
                by_class.setdefault(json.dumps(f["dims"], sort_keys=True), []).append(f)
        return [latest_filed(v) for _, v in sorted(by_class.items())]


def build_row(t: str, comp: Comp, price: dict, asof: date) -> dict:
    row: dict = {"ticker": t}
    cells: dict[str, dict | None] = {}

    def attempt(name, fn):
        try:
            cells[name] = fn()
        except calc.CalcError as e:
            cells[name] = None
            comp.notes.append(f"{name}: {e}")

    attempt("ltm_revenue", lambda: comp.ltm("revenue"))
    fy = comp.latest_fy("revenue")
    attempt("revenue_growth", lambda: comp.keep(calc.growth(comp.flow("revenue", "FY", fy),
                                                            comp.flow("revenue", "FY", fy - 1))))

    def gross():
        rev = cells["ltm_revenue"] or comp.ltm("revenue")
        if any(f["metric"] == "gross_profit" for f in comp.facts):
            gp = comp.ltm("gross_profit")
        elif any(f["metric"] == "cost_of_revenue" for f in comp.facts):
            gp = comp.keep(calc.gross_profit(rev, comp.ltm("cost_of_revenue")))
        else:
            raise calc.CalcError("neither gross_profit nor cost_of_revenue is tagged")
        return comp.keep(calc.margin(gp, rev))

    attempt("gross_margin", gross)
    ebitda: dict = {}

    def ebitda_margin():
        ebitda["v"] = comp.keep(calc.ebitda(comp.ltm("operating_income"), comp.ltm("d_and_a")))
        return comp.keep(calc.margin(ebitda["v"], cells["ltm_revenue"] or comp.ltm("revenue")))

    attempt("ebitda_margin", ebitda_margin)
    d = comp.bs_date(asof)
    total = comp.at("total_debt", d)
    debt = total or comp.at("debt_component", d)
    cash = comp.at("cash", d) + comp.at("short_term_investments", d)
    pref, nci = comp.at("preferred_stock", d), comp.at("noncontrolling_interest", d)
    for name, items in (("debt", debt), ("short_term_investments", comp.at("short_term_investments", d)),
                        ("preferred", pref), ("nci", nci)):
        if not items:
            comp.notes.append(f"{name}: not tagged at {d}; left out")
    row["balance_sheet_date"] = str(d)
    row["debt_basis"] = "total_debt" if total else ("debt_components" if debt else "none")
    attempt("net_debt", lambda: comp.keep(calc.net_debt(debt, cash)))
    shares = comp.shares(asof)
    row["shares_date"] = str(shares[0]["end"])
    attempt("enterprise_value", lambda: comp.keep(calc.enterprise_value(price, shares, debt, pref, nci, cash)))
    def mult(name, denom, what):
        if not cells["enterprise_value"] or not denom:
            raise calc.CalcError(f"EV or {what} missing")
        return comp.keep(calc.multiple(cells["enterprise_value"], denom, name))

    attempt("ev_revenue", lambda: mult("ev_revenue", cells["ltm_revenue"], "LTM revenue"))
    attempt("ev_ebitda", lambda: mult("ev_ebitda", ebitda.get("v"), "LTM EBITDA"))
    fy_, fp, yfy = comp.basis()
    row["ltm_basis"] = f"FY{fy_}" + (f" + {fp} FY{yfy} - {fp} FY{yfy - 1}" if fp else "")
    for name, _, _ in METRICS:
        c = cells[name]
        row[name] = c["value"] if c else ""
        row[f"{name}_calc_id"] = c["calc_id"] if c else ""
    row["notes"] = "; ".join(comp.notes)
    return row


def stats(rows: list[dict]) -> dict[str, dict]:
    out = {}
    for name, _, _ in METRICS:
        v = np.array([r[name] for r in rows if r[name] != ""], dtype=float)
        out[name] = {"n": len(v)} | ({"median": float(np.median(v)), "q1": float(np.percentile(v, 25)),
                                      "q3": float(np.percentile(v, 75))} if len(v) else {})
    return out


def fmt(v, kind: str) -> str:
    if v == "" or v is None:
        return "n/a"
    return {"usd": f"${v / 1e6:,.1f}m", "pct": f"{v * 100:.1f}%", "x": f"{v:.2f}x"}[kind]


def markdown(deal: str, rows: list[dict], st: dict, names: dict, price_file: str, asof: str) -> str:
    head = "| Ticker | Company | " + " | ".join(label for _, label, _ in METRICS) + " |"
    lines = [f"# Comparable companies: {deal}", "",
             f"Built for the variant's sector archetype from params comps.tickers; uses no data-room file. Prices: "
             f"`{price_file}` (as of {asof}). Every value is a calculator output; its calc_id is in "
             f"`outputs/comps/{deal}.csv` and its trace in `outputs/comps/{deal}_calcs.jsonl`.", "", head,
             "|" + "---|" * (len(METRICS) + 2)]
    for r in rows:
        lines.append(f"| {r['ticker']} | {names[r['ticker']]} | "
                     + " | ".join(fmt(r[n], k) for n, _, k in METRICS) + " |")
    for s in ("median", "q1", "q3"):
        label = {"median": "Median", "q1": "Q1 (25th pct)", "q3": "Q3 (75th pct)"}[s]
        lines.append(f"| **{label}** | | " + " | ".join(fmt(st[n].get(s, ""), k) for n, _, k in METRICS) + " |")
    lines.append("| n | | " + " | ".join(str(st[n]["n"]) for n, _, _ in METRICS) + " |")
    lines += ["", "LTM basis, balance-sheet date and notes per comparable:", "",
              "| Ticker | LTM basis | Balance sheet | Shares date | Debt | Notes |", "|---|---|---|---|---|---|"]
    for r in rows:
        lines.append(f"| {r['ticker']} | {r['ltm_basis']} | {r['balance_sheet_date']} | {r['shares_date']} | "
                     f"{r['debt_basis']} | {r['notes'] or '—'} |")
    return "\n".join(lines) + "\n"


def leaf_rows(c: dict, registry: dict, facts: dict, prices: dict) -> list[dict]:
    out = []
    for i in calc._leaves(c):          # Part 4 ops (growth) carry their fact_ids in the trace only
        if i in facts:
            f = facts[i]
            out.append({"id": i, "value": f["value"], "filing": f["doc_id"], "concept": f["concept"],
                        "period": f"{f['start'] or ''}..{f['end']}" if f["start"] else str(f["end"]),
                        "dims": json.dumps(f["dims"], sort_keys=True) if f["dims"] else "",
                        "url": json.loads(f["citation"]).get("url", "")})
        else:
            p = next(p for p in prices.values() if p["price_id"] == i)
            out.append({"id": i, "value": p["value"], "filing": p["file"], "concept": "close (snapshot)",
                        "period": p["date"], "dims": "", "url": p["source_url"]})
    return out


def spotcheck(deal: str, rows: list[dict], registry: dict, facts: dict, prices: dict) -> str:
    by_t = {r["ticker"]: r for r in rows}
    lines = [f"# Spot check: {deal} comps, 10 cells (to fill in by hand)", "",
             "For each cell: open every input's URL, check the value, filing and concept, recompute the cell from "
             "the formula, and fill in the last columns. Prices: check the close against a second public source.", ""]
    for n, (t, m) in enumerate(SPOT, 1):
        r = by_t[t]
        cid = r[f"{m}_calc_id"]
        lines += [f"## {n}. {t} {dict((a, b) for a, b, _ in METRICS)[m]}", ""]
        if not cid:
            lines += [f"Empty cell: {r['notes']}", ""]
            continue
        c = registry[cid]
        lines += [f"- value: `{c['value']}` ({fmt(c['value'], dict((a, k) for a, _, k in METRICS)[m])}), "
                  f"calc_id `{cid}`, formula `{c['formula']}`",
                  f"- recomputed by hand: ______  match (y/n): ___  notes: ______", "",
                  "| Input | Value | Filing | Concept | Period | Dims | URL | Checked (y/n) |",
                  "|---|---|---|---|---|---|---|---|"]
        for x in leaf_rows(c, registry, facts, prices):
            lines.append(f"| {x['id']} | {x['value']} | {x['filing']} | {x['concept']} | {x['period']} | "
                         f"{x['dims']} | {x['url']} | |")
        lines.append("")
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--deal", default="deal_a")
    ap.add_argument("--out", default="outputs/comps")
    ap.add_argument("--db", default="data/facts/facts.duckdb")
    ap.add_argument("--spotcheck", action="store_true", help="also write {deal}_spotcheck.md once (not a stage "
                    "output: it is filled in by hand, so it is never overwritten)")
    a = ap.parse_args()
    params = yaml.safe_load(open("params.yaml"))
    tickers = params["comps"]["tickers"]
    asof_s = json.load(open("data/dataroom/variant.json"))["price_snapshot_date"]
    asof = date.fromisoformat(asof_s)
    price_file = Path(params["reference"]["price_snapshot"])
    prices = P.price_inputs(P.validate(price_file, tickers, asof_s), price_file)
    meta = ciks(tickers)
    rows, registry, facts = [], {}, {}
    for t in tickers:
        fs = load_facts(Path(a.db), meta[t]["cik"])
        facts.update({f["fact_id"]: f for f in fs})
        comp = Comp(t, fs)
        rows.append(build_row(t, comp, prices[t], asof))
        for c in comp.calcs:
            registry[c["calc_id"]] = c
        print(f"{t}: " + ", ".join(f"{n}={fmt(rows[-1][n], k)}" for n, _, k in METRICS)
              + (f" | notes: {rows[-1]['notes']}" if rows[-1]["notes"] else ""))
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    cols = ["ticker"] + [c for n, _, _ in METRICS for c in (n, f"{n}_calc_id")] + \
           ["ltm_basis", "balance_sheet_date", "shares_date", "debt_basis", "notes"]
    with open(out / f"{a.deal}.csv", "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols, lineterminator="\n")
        w.writeheader()
        w.writerows(rows)
    st = stats(rows)
    names = {t: meta[t]["name"] for t in tickers}
    (out / f"{a.deal}.md").write_text(markdown(a.deal, rows, st, names, str(price_file), asof_s), encoding="utf-8")
    with open(out / f"{a.deal}_calcs.jsonl", "w", encoding="utf-8", newline="\n") as f:
        for cid in sorted(registry):
            f.write(json.dumps(registry[cid], sort_keys=True, default=str) + "\n")
    print(f"{len(rows)} comparables, {len(registry)} calcs -> {out}/{a.deal}.csv, .md, _calcs.jsonl")
    if a.spotcheck:
        sc = out / f"{a.deal}_spotcheck.md"
        if sc.exists():
            raise SystemExit(f"{sc} exists (it is filled in by hand); not overwritten")
        sc.write_text(spotcheck(a.deal, rows, registry, facts, prices), encoding="utf-8")
        print(f"checklist -> {sc}")
    print("stats: " + ", ".join(f"{n} median {fmt(st[n].get('median', ''), k)} (n={st[n]['n']})"
                                for n, _, k in METRICS))


if __name__ == "__main__":
    main()
