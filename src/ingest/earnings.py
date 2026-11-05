"""ingest_earnings (Part 0): for every ticker in comps.tickers, the newest
comps.earnings_8k_quarters Form 8-K filings reporting Item 2.02 (filed before comps.before),
each saved with its 8-K document and its EX-99.1 exhibit (the earnings release).

Usage:  python -m src.ingest.earnings --output data/earnings
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from src.ingest import edgar
from src.ingest.runstats import RunStats


def pick_release(docs: list[dict], label: str) -> tuple[dict, dict, str]:
    """(8-K document, earnings release, its SGML type). The release is the EX-99.1 exhibit; if the filer
    tagged it plain EX-99 instead (Shift4 0001794669-26-000042, file ex991q2.htm), the filing's only
    EX-99 document is used, with a warning. No 8-K, or no EX-99.1 and not exactly one EX-99, fails."""
    main_doc = [d for d in docs if d["type"].upper() == "8-K"]
    if not main_doc:
        raise RuntimeError(f"{label}: missing the 8-K document")
    ex991 = [d for d in docs if d["type"].upper() == "EX-99.1"]
    if ex991:
        return main_doc[0], ex991[0], "EX-99.1"
    ex99 = [d for d in docs if d["type"].upper() == "EX-99"]
    if len(ex99) != 1:
        raise RuntimeError(f"{label}: no EX-99.1 exhibit and {len(ex99)} EX-99 documents (need exactly one)")
    print(f"[WARN] {label}: no EX-99.1 exhibit; using the only EX-99 document {ex99[0]['filename']}")
    return main_doc[0], ex99[0], "EX-99"


def ingest_ticker(ticker: str, cik: str, params: dict, out: Path) -> list[Path]:
    c = params["comps"]
    n = c["earnings_8k_quarters"]
    hits, skipped = edgar.earnings_8ks(edgar.submissions(cik, params), n, c["before"],
                                       c["earnings_8k_min_gap_days"])
    for s in skipped:
        print(f"[INFO] {ticker} skipped 8-K {s['date']} items {s['items']}: "
              f"within {c['earnings_8k_min_gap_days']} days of a newer Item 2.02 filing")
    if len(hits) < n:
        raise RuntimeError(f"{ticker}: only {len(hits)} Item 2.02 8-Ks before {c['before']}, need {n}")

    filing_dirs = []
    for f in hits:
        raw = edgar.full_submission(cik, f["accn"], params).decode("utf-8", errors="replace")
        main_doc, release, ex99_type = pick_release(edgar.sgml_documents(raw), f"{ticker} {f['accn']}")

        fdir = out / ticker / f["accn"]
        fdir.mkdir(parents=True, exist_ok=True)
        for d in (main_doc, release):
            (fdir / d["filename"]).write_text(d["text"], encoding="utf-8", newline="\n")
        meta = {**f, "ticker": ticker, "cik": cik, "ex99_1": release["filename"], "ex99_type": ex99_type}
        (fdir / "filing.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
        print(f"[INFO] {ticker} 8-K {f['date']} items {f['items']} {ex99_type} {release['filename']}")
        filing_dirs.append(fdir)
    return filing_dirs


def main() -> None:
    ap = argparse.ArgumentParser(description="Part 0 ingest_earnings")
    ap.add_argument("--output", default="data/earnings")
    ap.add_argument("--params", default="params.yaml")
    ap.add_argument("--stats", default=None, help="run-stats CSV (default data/bench/cs2/ingest_earnings.csv)")
    args = ap.parse_args()

    params = edgar.load_params(args.params)
    out = Path(args.output)
    stats = RunStats("ingest_earnings", args.stats)
    for ticker, cik in edgar.cik_for(params["comps"]["tickers"], params).items():
        print(f"[INFO] {ticker} CIK {cik}")
        with stats.item(ticker, "8-Ks") as r:
            r["units"] = len(ingest_ticker(ticker, cik, params, out))
    stats.finish()


if __name__ == "__main__":
    main()
