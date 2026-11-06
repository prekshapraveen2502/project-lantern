"""
Part 2 items 5-6 tests: hybrid retrieval and scope enforcement (src/retrieve.py) on a toy Chroma collection with
hand-made vectors. No real corpus, no real queries (brief Part 3: qrels come before retrieval output).
"""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.retrieve import ScopeError, retrieve, rrf, scope_filter  # noqa: E402

PARAMS = {"retrieval": {"k": 3, "hybrid": {"enabled": True, "rrf_k": 60}}}
DOCS = [("a1", "deal_a", "alpha revenue grew strongly", [1.0, 0.0, 0.0]),
        ("a2", "deal_a", "alpha customer concentration", [0.9, 0.1, 0.0]),
        ("b1", "deal_b", "MARLIN canary secret revenue", [1.0, 0.0, 0.0]),
        ("k1", "kestrel", "kestrel take rate", [0.0, 1.0, 0.0]),
        ("p1", "public", "public filing revenue item 7", [0.8, 0.0, 0.2])]


@pytest.fixture
def index_dir(tmp_path):
    import chromadb
    from chromadb.config import Settings
    db = chromadb.PersistentClient(path=str(tmp_path / "chroma"), settings=Settings(anonymized_telemetry=False))
    col = db.create_collection("block-200-0-open", embedding_function=None, configuration={"hnsw": {"space": "cosine"}})
    col.add(ids=[d[0] for d in DOCS], embeddings=[d[3] for d in DOCS], documents=[d[2] for d in DOCS],
            metadatas=[{"chunk_id": d[0], "deal_id": d[1], "doc_id": f"DOC-{d[0]}", "section": "s", "tier": 4,
                        "page": -1, "source_type": "dataroom", "kind": "text", "block_ids": json.dumps([d[0]])}
                       for d in DOCS])
    return tmp_path


def run(index_dir, query, scope, vec=(1.0, 0.0, 0.0)):
    return retrieve(query, scope, strategy="block", size=200, overlap=0, which="open", params=PARAMS,
                    index_dir=index_dir, embed_query=lambda q: list(vec))


@pytest.mark.parametrize("scope", [None, "", "deal_c", "ALL"])
def test_a_query_without_a_valid_scope_raises_before_the_store(tmp_path, scope):
    with pytest.raises(ScopeError):
        run(tmp_path / "does-not-exist", "revenue", scope)


def test_scope_filters():
    assert scope_filter("deal_a") == {"deal_id": {"$in": ["deal_a", "public"]}}
    assert scope_filter("public") == {"deal_id": {"$in": ["public"]}}


def test_a_deal_scope_never_returns_another_deals_chunks(index_dir):
    """Dense and BM25 both rank b1 first for this query; neither may return it in deal_a scope."""
    got = run(index_dir, "MARLIN canary secret revenue", "deal_a")
    assert got and {g["deal_id"] for g in got} <= {"deal_a", "public"}
    assert "b1" not in [g["chunk_id"] for g in got]


def test_public_scope_returns_only_public(index_dir):
    assert {g["deal_id"] for g in run(index_dir, "revenue", "public")} == {"public"}


def test_rrf_fuses_dense_and_bm25_rankings():
    assert rrf([["x", "y"], ["y", "z"]], 60)[0][0] == "y"
    assert [cid for cid, _ in rrf([["x", "y"], ["y", "z"]], 60)] == ["y", "x", "z"]


def test_results_carry_doc_and_block_ids(index_dir):
    got = run(index_dir, "alpha revenue", "deal_a")
    assert got[0]["chunk_id"] == "a1" and got[0]["block_ids"] == ["a1"] and got[0]["page"] is None
