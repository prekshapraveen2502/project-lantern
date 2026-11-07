"""
Part 3 item 2 tests: the retrieval_eval stage (src/retrieval_eval.py) on toy rankings and a toy Chroma collection with
hand-made vectors. No real corpus, no real qrels.
"""
import hashlib
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import src.retrieval_eval as ev  # noqa: E402

META = {"c1": {"doc_id": "D1", "block_ids": '["b1", "b2"]'},
        "c2": {"doc_id": "D1", "block_ids": '["b2", "b3"]'},
        "c3": {"doc_id": "D2", "block_ids": '["b1"]'}}


def test_chunk_ranking_becomes_block_ranking_best_chunk_first_and_cutoffs_count_blocks():
    got = ev.block_ranking([("c1", 0.9), ("c2", 0.8), ("c3", 0.7)], META)
    assert list(got) == ["D1:b1", "D1:b2", "D1:b3", "D2:b1"]          # D1:b2 keeps its place from c1
    scores = list(got.values())
    assert scores == sorted(scores, reverse=True) and len(set(scores)) == len(scores)


def test_scores_use_grade_one_as_relevant_linear_gain_and_zero_for_an_empty_run():
    qrels = [{"qid": "q1", "category": "a", "relevant": {"D1:b1": 2, "D1:b3": 1}},
             {"qid": "q2", "category": "b", "relevant": {"D2:b1": 1}}]
    runs = {"q1": ev.block_ranking([("c1", 1.0), ("c2", 0.5)], META), "q2": {}}
    got = ev.score(qrels, runs)
    a = got["by_category"]["a"]
    assert a["recall@5"] == 1.0 and a["mrr@20"] == 1.0
    assert a["ndcg@10"] == round((2 + 1 / 2) / (2 + 1 / 1.5849625007211562), 4)   # gains 2 at rank 1, 1 at rank 3
    assert got["by_category"]["b"] == {m: 0.0 for m in ev.METRICS}
    assert got["overall"]["recall@5"] == 0.5


DOCS = [("a1", "deal_a", "alpha revenue grew", [1.0, 0.0, 0.0], ["b1"]),
        ("a2", "deal_a", "alpha churn rate", [0.0, 1.0, 0.0], ["b2"]),
        ("p1", "public", "public filing revenue", [0.7, 0.0, 0.7], ["b9"])]
PARAMS = {"chunk": {"grid": {"block": [[200, 0]]}}, "retrieval": {"hybrid": {"enabled": True, "rrf_k": 60}}}


@pytest.fixture
def index_dir(tmp_path):
    import chromadb
    from chromadb.config import Settings
    db = chromadb.PersistentClient(path=str(tmp_path / "chroma"), settings=Settings(anonymized_telemetry=False))
    log = {}
    for which in ("hosted", "open"):
        col = db.create_collection(f"block-200-0-{which}", embedding_function=None,
                                   configuration={"hnsw": {"space": "cosine"}})
        col.add(ids=[d[0] for d in DOCS], embeddings=[d[3] for d in DOCS], documents=[d[2] for d in DOCS],
                metadatas=[{"chunk_id": d[0], "deal_id": d[1], "doc_id": f"DOC-{d[0][0]}", "section": "s",
                            "tier": 3, "page": -1, "source_type": "dataroom", "kind": "text",
                            "block_ids": json.dumps(d[4])} for d in DOCS])
        log[f"block-200-0-{which}"] = {"count": 3, "dims": 3}
    (tmp_path / "index_log.json").write_text(json.dumps(log))
    del db
    return tmp_path


def digest(path: Path) -> str:
    h = hashlib.sha256()
    for f in sorted(p for p in path.rglob("*") if p.is_file()):
        h.update(str(f.relative_to(path)).encode()); h.update(f.read_bytes())
    return h.hexdigest()


def test_run_scores_every_collection_and_method_and_leaves_the_index_untouched(index_dir, tmp_path):
    qrels = tmp_path / "qrels.jsonl"
    qrels.write_text(json.dumps({"qid": "q1", "query": "alpha revenue", "category": "numeric_lookup",
                                 "scope": "deal_a", "relevant": {"DOC-a:b1": 2}}) + "\n")
    before = digest(index_dir / "chroma")
    vec = lambda which: (lambda q: [1.0, 0.0, 0.0])
    report, latency = ev.run(qrels, index_dir, PARAMS, vec)
    assert digest(index_dir / "chroma") == before                    # Chroma never opened the tracked copy
    assert sorted(report["collections"]) == ["block-200-0-hosted", "block-200-0-open"]
    c = report["collections"]["block-200-0-open"]
    assert c["index_vector_bytes"] == 3 * 3 * 4 and set(c) >= set(ev.METHODS)
    assert c["hybrid"]["overall"]["mrr@20"] == 1.0 and c["dense"]["overall"]["recall@5"] == 1.0
    assert [r["collection"] for r in latency] == ["block-200-0-hosted", "block-200-0-open"]
    assert all(r["queries"] == 1 and r["p95_ms"] >= r["p50_ms"] > 0 for r in latency)
