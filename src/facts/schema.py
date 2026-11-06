"""Part 1 item 2: the fact store's tables (decision of 2026-11-05, "Fact store layout").

facts   tier 1-3 values (XBRL, seller financials); every row has a citation
claims  tier 4-5 claims (CIM, deck, transcript, synthetic web); created here, filled in item 5
"""
from __future__ import annotations

from pathlib import Path

import duckdb

FACTS_DDL = """
CREATE TABLE facts (
    fact_id       VARCHAR PRIMARY KEY,
    deal_id       VARCHAR NOT NULL,
    entity_id     VARCHAR NOT NULL,
    concept       VARCHAR,                      -- XBRL qname; NULL for data-room facts
    metric        VARCHAR,                      -- metrics_vocab.yaml name; NULL if unmapped
    value         DOUBLE  NOT NULL,             -- base units, sign convention applied
    unit          VARCHAR NOT NULL,             -- USD, shares, pure, USD/shares, count, ...
    scale         INTEGER,                      -- power-of-ten exponent from the number as shown
    period_type   VARCHAR NOT NULL CHECK (period_type IN ('instant', 'duration')),
    start         DATE,
    "end"         DATE    NOT NULL,
    fiscal_year   INTEGER,
    fiscal_period VARCHAR,
    dims          JSON    NOT NULL,
    decimals      INTEGER,                      -- NULL = exact (INF) or unknown
    tier          INTEGER NOT NULL CHECK (tier BETWEEN 1 AND 3),
    source_type   VARCHAR NOT NULL CHECK (source_type IN ('edgar_xbrl', 'dataroom')),
    doc_id        VARCHAR NOT NULL,
    filed         DATE,
    citation      JSON    NOT NULL,
    extractor     VARCHAR NOT NULL,
    lineage       JSON    NOT NULL
)"""

CLAIMS_DDL = """
CREATE TABLE claims (
    claim_id      VARCHAR PRIMARY KEY,
    deal_id       VARCHAR NOT NULL,
    entity_id     VARCHAR NOT NULL,
    metric        VARCHAR,
    value         DOUBLE,
    unit          VARCHAR,
    scale         INTEGER,
    period_type   VARCHAR CHECK (period_type IN ('instant', 'duration')),
    start         DATE,
    "end"         DATE,
    fiscal_year   INTEGER,
    fiscal_period VARCHAR,
    period_label  VARCHAR,                      -- as written, e.g. CY2025, FY2025
    period_basis  VARCHAR CHECK (period_basis IN ('fiscal', 'calendar', 'unknown')),
    qualifier     VARCHAR[],                    -- adjusted, run-rate, pro forma, LTM, FY
    claim_text    VARCHAR NOT NULL,
    method        VARCHAR NOT NULL CHECK (method IN ('text', 'chart_read')),
    tier          INTEGER NOT NULL CHECK (tier IN (4, 5)),
    source_type   VARCHAR NOT NULL,
    doc_id        VARCHAR NOT NULL,
    block_id      VARCHAR,
    page          INTEGER,
    bbox          DOUBLE[],
    citation      JSON    NOT NULL,
    extractor     VARCHAR NOT NULL,
    lineage       JSON    NOT NULL,
    comparator    VARCHAR NOT NULL CHECK (comparator IN ('eq', 'approx', 'lt', 'le', 'gt', 'ge'))
)"""

CLAIM_COLUMNS = ["claim_id", "deal_id", "entity_id", "metric", "value", "unit", "scale", "period_type", "start", "end",
                 "fiscal_year", "fiscal_period", "period_label", "period_basis", "qualifier", "claim_text", "method",
                 "tier", "source_type", "doc_id", "block_id", "page", "bbox", "citation", "extractor", "lineage",
                 "comparator"]

FACT_COLUMNS = ["fact_id", "deal_id", "entity_id", "concept", "metric", "value", "unit", "scale", "period_type",
                "start", "end", "fiscal_year", "fiscal_period", "dims", "decimals", "tier", "source_type", "doc_id",
                "filed", "citation", "extractor", "lineage"]


def create_db(path: Path | str) -> duckdb.DuckDBPyConnection:
    """A new, empty fact store at `path` (an existing file is replaced: the store is rebuilt on every repro)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    for p in (path, path.with_name(path.name + ".wal")):
        p.unlink(missing_ok=True)
    con = duckdb.connect(str(path))
    con.execute(FACTS_DDL)
    con.execute(CLAIMS_DDL)
    return con
