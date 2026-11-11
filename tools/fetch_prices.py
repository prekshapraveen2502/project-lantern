"""One-time: validate and record the hand-downloaded price snapshot (DECISIONS 2026-11-10).

Stooq's CSV endpoint serves a JavaScript browser check, so prices are not fetched by script: Preksha downloads the
unadjusted closes from Yahoo Finance historical quotes into params reference.price_snapshot. This script only
checks the file (src/comps/prices.py validate: columns, every comps.tickers ticker once, variant.json's
price_snapshot_date, positive closes, an https source URL per row) and prints its sha256. It never writes the
file. Then record it with DVC: dvc add data/reference

Usage: python tools/fetch_prices.py
Exits 1 with every problem listed if the file does not pass.
"""
import json
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.comps.prices import PriceError, sha256_of, validate  # noqa: E402

params = yaml.safe_load(open("params.yaml"))
path = Path(params["reference"]["price_snapshot"])
date = json.load(open("data/dataroom/variant.json"))["price_snapshot_date"]
try:
    rows = validate(path, params["comps"]["tickers"], date)
except (PriceError, FileNotFoundError) as e:
    print(f"FAIL {e}")
    sys.exit(1)
for r in rows:
    print(f"{r['ticker']:5} {r['date']} {r['close']:>12} {r['source_url']}")
print(f"OK {len(rows)} prices as of {date}; sha256 {sha256_of(path)}")
print("next: dvc add data/reference")
