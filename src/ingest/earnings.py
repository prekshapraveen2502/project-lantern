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
        docs = edgar.sgml_documents(raw)
        main_doc = [d for d in docs if d["type"].upper() == "8-K"]
        ex991 = [d for d in docs if d["type"].upper() == "EX-99.1"]
        if not main_doc or not ex991:
            raise RuntimeError(f"{ticker} {f['accn']}: missing the 8-K document or the EX-99.1 exhibit")

        fdir = out / ticker / f["accn"]
        fdir.mkdir(parents=True, exist_ok=True)
        for d in (main_doc[0], ex991[0]):
            (fdir / d["filename"]).write_text(d["text"], encoding="utf-8", newline="\n")
        meta = {**f, "ticker": ticker, "cik": cik, "ex99_1": ex991[0]["filename"]}
        (fdir / "filing.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
        print(f"[INFO] {ticker} 8-K {f['date']} items {f['items']} EX-99.1 {ex991[0]['filename']}")
        filing_dirs.append(fdir)
    return filing_dirs


def main() -> None:
    ap = argparse.ArgumentParser(description="Part 0 ingest_earnings")
    ap.add_argument("--output", default="data/earnings")
    ap.add_argument("--params", default="params.yaml")
    args = ap.parse_args()

    params = edgar.load_params(args.params)
    out = Path(args.output)
    for ticker, cik in edgar.cik_for(params["comps"]["tickers"], params).items():
        print(f"[INFO] {ticker} CIK {cik}")
        ingest_ticker(ticker, cik, params, out)


if __name__ == "__main__":
    main()
