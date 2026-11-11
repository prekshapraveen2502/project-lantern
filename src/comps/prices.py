"""Part 6 item 2: the price snapshot (data/reference/prices_asof_<YYYYMMDD>.csv), read and checked.

The file is downloaded by hand (DECISIONS 2026-11-10: Yahoo Finance historical quotes, unadjusted Close). Columns
exactly ticker,date,close,source_url; one row per params comps.tickers ticker; date = variant.json
price_snapshot_date; close a positive number; source_url an https URL naming the ticker. Nothing here fetches,
types or estimates a price. Each close becomes a calculator input {price_id, value, unit, tier 1}.
"""
from __future__ import annotations

import csv
import hashlib
from pathlib import Path

COLUMNS = ["ticker", "date", "close", "source_url"]


class PriceError(ValueError):
    pass


def validate(path: Path, tickers: list[str], snapshot_date: str) -> list[dict]:
    """Rows in file order, each with close as float; raises PriceError listing every problem."""
    problems, rows = [], []
    with open(path, encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames != COLUMNS:
            raise PriceError(f"{path}: columns {reader.fieldnames}, want {COLUMNS}")
        for i, r in enumerate(reader, start=2):
            t = (r["ticker"] or "").strip()
            try:
                close = float(r["close"])
            except (TypeError, ValueError):
                problems.append(f"line {i} {t}: close {r['close']!r} is not a number")
                continue
            if not close > 0:
                problems.append(f"line {i} {t}: close {r['close']!r} is not positive")
            if r["date"] != snapshot_date:
                problems.append(f"line {i} {t}: date {r['date']!r}, want {snapshot_date}")
            url = r["source_url"] or ""
            if not url.startswith("https://") or t.lower() not in url.lower():
                problems.append(f"line {i} {t}: source_url {url!r} is not an https URL naming the ticker")
            rows.append({**r, "ticker": t, "close": close, "line": i})
    got = [r["ticker"] for r in rows]
    if sorted(got) != sorted(tickers) or len(set(got)) != len(got):
        problems.append(f"tickers {got}, want each of {tickers} exactly once")
    if problems:
        raise PriceError(f"{path}: " + "; ".join(problems))
    return rows


def price_inputs(rows: list[dict], path: Path) -> dict[str, dict]:
    """ticker -> calculator input. price_id PRICE-<TICKER>-<YYYYMMDD>; tier 1 (the brief's fixed reference)."""
    return {r["ticker"]: {"price_id": f"PRICE-{r['ticker']}-{r['date'].replace('-', '')}", "value": r["close"],
                          "unit": "USD/share", "tier": 1, "date": r["date"], "source_url": r["source_url"],
                          "file": str(path), "line": r["line"]} for r in rows}


def sha256_of(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()
