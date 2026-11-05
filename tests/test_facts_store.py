"""
Part 0 and Part 1 acceptance checks on the built fact store (data/facts/facts.duckdb). The store is DVC-tracked, so
CI (no DVC access) skips these; run them locally after `dvc repro facts`.
"""
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "data" / "facts" / "facts.duckdb"
pytestmark = pytest.mark.skipif(not DB.exists(), reason="data/facts/facts.duckdb not built (DVC data)")
sys.path.insert(0, str(ROOT))


@pytest.fixture(scope="module")
def con():
    import duckdb
    c = duckdb.connect(str(DB), read_only=True)
    yield c
    c.close()


def test_part1_every_fact_has_a_tier_1_to_3_and_a_citation(con):
    assert con.execute("SELECT count(*) FROM facts WHERE tier NOT BETWEEN 1 AND 3 OR tier IS NULL").fetchone()[0] == 0
    assert con.execute("SELECT count(*) FROM facts WHERE citation IS NULL").fetchone()[0] == 0


def test_part1_no_claim_tier_value_in_facts(con):
    assert con.execute("SELECT count(*) FROM facts WHERE tier IN (4, 5)").fetchone()[0] == 0


def test_part1_xbrl_citations_open_and_give_back_the_value(con):
    """Every tier-1 citation names a local file that exists and an EDGAR URL; the build log's citation-text
    check (sign x text x 10^scale = value) found no mismatch."""
    import json
    rows = con.execute("SELECT citation FROM facts WHERE source_type = 'edgar_xbrl'").fetchall()
    cits = [json.loads(r[0]) for r in rows]
    assert {c["kind"] for c in cits} <= {"ixbrl_element", "ixbrl_context"}
    assert all((ROOT / c["source_path"]).exists() for c in {c["source_path"]: c for c in cits}.values())
    assert all(c["url"].startswith("https://www.sec.gov/Archives/edgar/data/") for c in cits)
    totals = json.loads((ROOT / "data" / "facts" / "build_log.json").read_text())["totals"]
    assert totals["citation_text_mismatched"] == 0


def test_part0_every_comparable_has_at_least_8_statement_metrics(con):
    """Brief Part 0: "Every comparable has facts for at least 8 of the vocabulary's financial-statement metrics"."""
    from src.facts.vocab import load_vocab
    stmt = [n for n, m in load_vocab(ROOT / "config" / "metrics_vocab.yaml")["metrics"].items()
            if m["kind"] == "statement"]
    counts = dict(con.execute(
        f"SELECT entity_id, count(DISTINCT metric) FROM facts WHERE deal_id = 'public' AND metric IN "
        f"({', '.join(repr(m) for m in stmt)}) GROUP BY entity_id").fetchall())
    tickers = yaml.safe_load((ROOT / "params.yaml").read_text())["comps"]["tickers"]
    assert len(counts) == len(tickers)
    assert {k: v for k, v in counts.items() if v < 8} == {}
