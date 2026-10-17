"""Shared EDGAR helpers for ingest_comps and ingest_earnings.

Adapted from the Lab 12 slide functions: submissions() and the companyfacts call,
plus raise_for_status(), a rate limit, and a ticker -> CIK lookup.
"""
from __future__ import annotations

import time

import requests
import yaml

TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"
COMPANYFACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
FULL_SUBMISSION_URL = "https://www.sec.gov/Archives/edgar/data/{cik_int}/{accn_nodash}/{accn}.txt"
MIN_INTERVAL_S = 0.11   # EDGAR allows at most 10 requests per second
_last_call = 0.0


def load_params(path: str = "params.yaml") -> dict:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def headers(params: dict) -> dict:
    """EDGAR requires a User-Agent with a real contact; reuse the CS1 identity."""
    d = params["download"]
    return {"User-Agent": f'{d["user_agent_name"]} {d["user_agent_email"]}'}


def _get(url: str, params: dict) -> requests.Response:
    """Polite GET: wait to stay under the rate limit, fail loudly on any HTTP error."""
    global _last_call
    wait = MIN_INTERVAL_S - (time.monotonic() - _last_call)
    if wait > 0:
        time.sleep(wait)
    r = requests.get(url, headers=headers(params), timeout=60)
    _last_call = time.monotonic()
    r.raise_for_status()          # a 403/404 becomes a clear error naming the URL
    return r


def get_json(url: str, params: dict) -> dict:
    return _get(url, params).json()


def get_bytes(url: str, params: dict) -> bytes:
    return _get(url, params).content


def cik_for(tickers: list[str], params: dict) -> dict[str, str]:
    """Map each ticker to its 10-digit CIK. Unknown tickers raise; never skip quietly."""
    table = get_json(TICKERS_URL, params)
    by_ticker = {row["ticker"].upper(): f'{row["cik_str"]:010d}' for row in table.values()}
    missing = [t for t in tickers if t.upper() not in by_ticker]
    if missing:
        raise KeyError(f"tickers not found on EDGAR: {missing}")
    return {t.upper(): by_ticker[t.upper()] for t in tickers}


def submissions(cik: str, params: dict) -> dict:
    return get_json(SUBMISSIONS_URL.format(cik=cik), params)


def companyfacts(cik: str, params: dict) -> dict:
    return get_json(COMPANYFACTS_URL.format(cik=cik), params)



def latest_filing(subs: dict, form: str, after: str, before: str) -> dict | None:
    """Newest filing of exactly `form` with after <= filingDate < before.

    filings.recent is column-oriented (parallel arrays), so zip the columns back into rows.
    Exact match on form: '10-K' does not match '10-K/A'.
    """
    rec = subs["filings"]["recent"]
    rows = [
        {"form": f, "date": d, "accn": a, "doc": p}
        for f, d, a, p in zip(rec["form"], rec["filingDate"],
                              rec["accessionNumber"], rec["primaryDocument"])
        if f == form and after <= d < before
    ]
    return max(rows, key=lambda r: r["date"]) if rows else None


def full_submission(cik: str, accn: str, params: dict) -> bytes:
    """The whole filing as one SGML text file: the same file CS1's unpacker reads."""
    url = FULL_SUBMISSION_URL.format(cik_int=int(cik), accn_nodash=accn.replace("-", ""), accn=accn)
    return get_bytes(url, params)
