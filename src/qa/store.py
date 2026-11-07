"""Part 4: read-only lookups in data/facts/facts.duckdb for the answering service. A fact_id (F-...) is looked up in
the facts table; a claim_id (C-...) in the claims table, since claims are referenced by claim_id and the brief has no
/claims endpoint (decision Q9). JSON columns are returned parsed, dates as ISO strings.
"""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

JSON_COLS = {"citation", "lineage", "dims"}


def _row(cur, row) -> dict:
    out = {}
    for (name, *_), v in zip(cur.description, row):
        if name in JSON_COLS and isinstance(v, str):
            v = json.loads(v)
        elif isinstance(v, (dt.date, dt.datetime)):
            v = v.isoformat()
        out[name] = v
    return out


def connect(db: Path):
    import duckdb
    return duckdb.connect(str(db), read_only=True)


def get(db: Path, ref_id: str) -> dict | None:
    table, key = ("claims", "claim_id") if ref_id.startswith("C-") else ("facts", "fact_id")
    con = connect(db)
    try:
        cur = con.execute(f"select * from {table} where {key} = ?", [ref_id])
        row = cur.fetchone()
        return None if row is None else {"kind": "claim" if table == "claims" else "fact", **_row(cur, row)}
    finally:
        con.close()


def counts(db: Path) -> dict:
    con = connect(db)
    try:
        return {t: con.execute(f"select count(*) from {t}").fetchone()[0] for t in ("facts", "claims")}
    finally:
        con.close()
