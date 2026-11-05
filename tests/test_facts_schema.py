"""
Part 1 item 2 tests: the fact store's constraints (src/facts/schema.py). No DVC data: each test builds a store in a
temporary folder.
"""
import json
import sys
from pathlib import Path

import duckdb
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.facts.schema import FACT_COLUMNS, create_db  # noqa: E402


def fact(**over):
    row = {"fact_id": "F-000000000001", "deal_id": "public", "entity_id": "0001786352",
           "concept": "us-gaap:Revenues", "metric": "revenue", "value": 1.0, "unit": "USD", "scale": 3,
           "period_type": "duration", "start": "2025-07-01", "end": "2026-06-30", "fiscal_year": 2026,
           "fiscal_period": "FY", "dims": "{}", "decimals": -3, "tier": 1, "source_type": "edgar_xbrl",
           "doc_id": "0001628280-26-058238", "filed": "2026-08-20",
           "citation": json.dumps({"kind": "ixbrl_element"}), "extractor": "test", "lineage": "{}"}
    row.update(over)
    return row


def insert(con, table, row):
    cols = list(row)
    con.execute(f"INSERT INTO {table} ({', '.join(chr(34) + c + chr(34) for c in cols)}) "
                f"VALUES ({', '.join('?' for _ in cols)})", [row[c] for c in cols])


def claim(**over):
    row = {"claim_id": "C-000000000001", "deal_id": "kestrel", "entity_id": "kestrel", "claim_text": "x",
           "method": "text", "tier": 4, "source_type": "dataroom", "doc_id": "DR-kestrel-001",
           "citation": "{}", "extractor": "test", "lineage": "{}"}
    row.update(over)
    return row


@pytest.fixture
def con(tmp_path):
    c = create_db(tmp_path / "facts.duckdb")
    yield c
    c.close()


def test_columns_match_the_ddl(con):
    assert [r[0] for r in con.execute("DESCRIBE facts").fetchall()] == FACT_COLUMNS


def test_a_valid_fact_inserts(con):
    insert(con, "facts", fact())
    assert con.execute("SELECT count(*) FROM facts").fetchone()[0] == 1


@pytest.mark.parametrize("over", [{"tier": 4}, {"tier": 5}, {"citation": None}, {"period_type": "FY"},
                                  {"source_type": "synthetic_web"}])
def test_facts_reject_claim_tiers_missing_citations_and_bad_values(con, over):
    with pytest.raises(duckdb.ConstraintException):
        insert(con, "facts", fact(**over))


def test_fact_id_is_unique(con):
    insert(con, "facts", fact())
    with pytest.raises(duckdb.ConstraintException):
        insert(con, "facts", fact())


def test_a_valid_claim_inserts_and_chart_read_is_allowed(con):
    insert(con, "claims", claim())
    insert(con, "claims", claim(claim_id="C-000000000002", method="chart_read", tier=5))
    assert con.execute("SELECT count(*) FROM claims").fetchone()[0] == 2


@pytest.mark.parametrize("over", [{"tier": 3}, {"method": "ocr"}, {"citation": None}, {"period_basis": "weekly"}])
def test_claims_reject_fact_tiers_unknown_methods_and_missing_citations(con, over):
    with pytest.raises(duckdb.ConstraintException):
        insert(con, "claims", claim(**over))


def test_create_db_replaces_an_existing_store(tmp_path):
    c = create_db(tmp_path / "f.duckdb")
    insert(c, "facts", fact())
    c.close()
    c = create_db(tmp_path / "f.duckdb")
    assert c.execute("SELECT count(*) FROM facts").fetchone()[0] == 0
    c.close()
