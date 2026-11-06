"""
Part 1 item 5 tests: management claims (src/facts/claims.py). CI tests use pure functions and replay mode (no key,
no network); the Kestrel tests read the built store and are skipped without DVC data.
"""
import json
import os
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.facts.claims import (ClaimsError, claim_id, claim_period, keep_block, locate, page_messages,  # noqa: E402
                              response_model, to_value)
from src.facts.vocab import load_vocab  # noqa: E402

VOCAB = load_vocab(ROOT / "config" / "metrics_vocab.yaml")


@pytest.mark.parametrize("number,scale,pct,expected", [
    (4.9, "billion", False, (4.9e9, 9)), (96.4, "million", False, (96.4e6, 6)), (31, "none", True, (0.31, 0)),
    (1.45, "none", True, (0.0145, 0)), (14, "none", False, (14.0, 0))])
def test_to_value(number, scale, pct, expected):
    v, s = to_value(number, scale, pct)
    assert s == expected[1] and abs(v - expected[0]) <= 1e-9 * abs(expected[0])


def test_grid_fragments_are_left_out_and_narrative_is_kept():
    for frag in ("442.4", "Apr 2025", "(23.5)", "61.8", "Jun 2025", "—"):
        assert not keep_block({"block_type": "Text", "text": frag}), frag
    for text in ("In FY2025 Kestrel processed over $4.9bn in payment volume.", "FY2023",
                 "Best-in-class expansion: NRR of 128%.", "Q4 FY2025, annualized."):
        assert keep_block({"block_type": "Text", "text": text}), text
    assert not keep_block({"block_type": "Table", "text": "Month TPV Net revenue"})


def test_locate_is_whitespace_insensitive_and_returns_offsets():
    blocks = [{"block_id": "b1", "text": "nothing here"},
              {"block_id": "b2", "text": "Kestrel processed over\n$4.9bn in payment volume."}]
    b, s, e = locate("over $4.9bn in payment volume", blocks)
    assert b["block_id"] == "b2" and b["text"][s:e] == "over\n$4.9bn in payment volume"
    assert locate("not in any block", blocks) is None


@pytest.mark.parametrize("label,ptype,expected", [
    ("FY2025", "duration", ("fiscal", "2024-07-01", "2025-06-30", 2025, "FY")),
    ("FY2025", "instant", ("fiscal", None, "2025-06-30", 2025, "instant")),
    ("Q4 FY2025", "duration", ("fiscal", "2025-04-01", "2025-06-30", 2025, "Q4")),
    ("CY2025", "duration", ("calendar", "2025-01-01", "2025-12-31", 2026, "other")),
    (None, "duration", ("unknown", "2024-07-01", "2025-06-30", 2025, "FY"))])
def test_claim_period(label, ptype, expected):
    p = claim_period(label, "0630", 2025, ptype, 7)
    assert (p["period_basis"], p["start"], p["end"], p["fiscal_year"], p["fiscal_period"]) == expected
    assert p["period_assumed"] == (label is None)


def test_an_unrecognised_period_label_stops():
    with pytest.raises(ClaimsError, match="not FYyyyy"):
        claim_period("this year", "0630", 2025, "duration", 7)


def test_claim_id_is_stable_and_span_specific():
    assert claim_id("DR-kestrel-001", "p0002_b003", 10, 40) == claim_id("DR-kestrel-001", "p0002_b003", 10, 40)
    assert claim_id("DR-kestrel-001", "p0002_b003", 10, 40) != claim_id("DR-kestrel-001", "p0002_b003", 10, 41)


def test_metric_is_an_enum_of_vocabulary_names_and_comparators_are_fixed():
    schema = json.dumps(response_model(VOCAB).model_json_schema())
    for name in ("revenue", "top1_customer_share", "headcount"):
        assert f'"{name}"' in schema
    for comp in ("approx", "le", "gt"):
        assert f'"{comp}"' in schema


def test_prompt_holds_only_the_blocks_and_the_vocabulary():
    msgs = page_messages([{"block_id": "p0006_b002", "text": "no partner exceeds 10% of net revenue"}], VOCAB)
    assert "[p0006_b002] no partner exceeds 10% of net revenue" in msgs[1]["content"]
    assert "top1_customer_share" in msgs[1]["content"]


def test_only_reconcile_eval_mentions_the_answer_key():
    """The Kestrel answer key is for evaluation only (never in prompts, extraction or reconcile logic)."""
    hits = [p.relative_to(ROOT).as_posix() for p in (ROOT / "src").rglob("*.py")
            if "answer_key" in p.read_text(encoding="utf-8") and p.name != "reconcile_eval.py"]
    assert hits == []


def test_replay_cache_miss_raises_without_network(monkeypatch):
    """Replay serves only from data/llm_cache; an uncached call raises before any provider is contacted."""
    monkeypatch.setenv("LANTERN_LLM_MODE", "replay")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.chdir(ROOT)
    import importlib
    import src.llm.client as client
    client = importlib.reload(client)
    with pytest.raises(RuntimeError, match="cache miss"):
        client.structured(response_model(VOCAB), [{"role": "user", "content": "uncached test prompt 7f3a"}],
                          model="gpt-4.1-mini", stage="test", temperature=0)


# ---- Kestrel (local only) ----
DB = ROOT / "data" / "facts" / "facts.duckdb"


@pytest.mark.skipif(not DB.exists(), reason="data/facts/facts.duckdb (DVC)")
def test_kestrel_claims_are_tier_4_with_block_and_location():
    import duckdb
    con = duckdb.connect(str(DB), read_only=True)
    rows = con.execute("SELECT tier, deal_id, doc_id, block_id, page, bbox, citation, claim_text FROM claims").fetchall()
    tier45_in_facts = con.execute("SELECT count(*) FROM facts WHERE tier IN (4, 5)").fetchone()[0]
    recs = {(json.loads(l)["doc_id"], json.loads(l)["block_id"]): json.loads(l)
            for l in open(ROOT / "data" / "dataroom" / "kestrel" / "export" / "records.jsonl")}
    con.close()
    assert rows and tier45_in_facts == 0
    for tier, deal, doc, block, page, bbox, cit, text in rows:
        c = json.loads(cit)
        assert tier == 4 and deal == "kestrel" and (doc, block) in recs
        assert text in recs[(doc, block)]["text"]
        if c["kind"] == "text_span":
            assert page is None and bbox is None
        else:
            assert page is not None and bbox is not None and c["kind"] == "pdf_bbox"
