"""
Part 3 item 3: deal isolation. Every qrels query (data/eval/qrels.jsonl, all 60) runs in deal_a scope through
src/retrieve.py retrieve() with the pinned retrieval params, and must return zero deal_b records and zero deal_b
canaries.

- CI: against the committed toy fixture tests/fixtures/isolation/ (two toy deals with planted canaries), embedded with
  a hashed bag-of-words vector into a temporary Chroma collection; no model, no network, no DVC.
- Teeth: the same check with the scope filter removed must find deal_b records and canaries (always runs).
  `pytest tests/test_retrieval.py --no-scope-filter` removes the filter for the main test too, which then fails.
- Full index (local): the pinned collection in data/index with canaries from data/dataroom/canaries_visible.json;
  skipped until deal_a and deal_b are in the index.
"""
import hashlib
import json
import sys
from pathlib import Path

import numpy as np
import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import src.retrieve as retrieve_mod  # noqa: E402
from src.index import collection_name  # noqa: E402

FIXTURE = ROOT / "tests" / "fixtures" / "isolation"
PARAMS = yaml.safe_load((ROOT / "params.yaml").read_text(encoding="utf-8"))
R = PARAMS["retrieval"]
PINNED = dict(strategy=R["strategy"], size=int(R["chunk_tokens"]), overlap=int(R["overlap_tokens"]), which=R["embed"])
QUERIES = [json.loads(l) for l in open(ROOT / "data" / "eval" / "qrels.jsonl", encoding="utf-8") if l.strip()]
DIM = 64


def hashed_vector(text: str) -> list[float]:
    v = np.zeros(DIM)
    for tok in retrieve_mod.tokenize(text):
        v[int(hashlib.sha256(tok.encode()).hexdigest(), 16) % DIM] += 1.0
    n = np.linalg.norm(v)
    return (v / n if n else v).tolist()


def remove_scope_filter(monkeypatch):
    """The scope stays required (ScopeError for a missing one); only the store filter is dropped."""
    real = retrieve_mod.scope_filter
    monkeypatch.setattr(retrieve_mod, "scope_filter", lambda scope: (real(scope), None)[1])


@pytest.fixture
def fixture_index(tmp_path):
    import chromadb
    from chromadb.config import Settings
    rows = [json.loads(l) for l in open(FIXTURE / "chunks.jsonl", encoding="utf-8")]
    db = chromadb.PersistentClient(path=str(tmp_path / "chroma"), settings=Settings(anonymized_telemetry=False))
    col = db.create_collection(collection_name(**PINNED), embedding_function=None,
                               configuration={"hnsw": {"space": "cosine"}})
    col.add(ids=[r["chunk_id"] for r in rows], embeddings=[hashed_vector(r["text"]) for r in rows],
            documents=[r["text"] for r in rows],
            metadatas=[{"chunk_id": r["chunk_id"], "deal_id": r["deal_id"], "doc_id": r["doc_id"], "section": "",
                        "tier": 3, "page": -1, "source_type": "dataroom", "kind": "text",
                        "block_ids": json.dumps([r["chunk_id"]])} for r in rows])
    return tmp_path, col, json.loads((FIXTURE / "canaries.json").read_text(encoding="utf-8"))


def leaks(index_dir: Path, col, canaries: dict, embed_query) -> tuple[list[str], int]:
    """Every qrels query in deal_a scope; returns the leaks found and how many results came back in total."""
    found, total = [], 0
    for q in QUERIES:
        got = retrieve_mod.retrieve(q["query"], "deal_a", params=PARAMS, index_dir=index_dir,
                                    embed_query=embed_query, **PINNED)
        total += len(got)
        texts = dict(zip(*[col.get(ids=[g["chunk_id"] for g in got], include=["documents"])[k]
                           for k in ("ids", "documents")])) if got else {}
        for g in got:
            if g["deal_id"] == "deal_b":
                found.append(f"{q['qid']}: deal_b record {g['chunk_id']} ({g['doc_id']})")
            for c in canaries.get("deal_b", []):
                if c in texts.get(g["chunk_id"], ""):
                    found.append(f"{q['qid']}: deal_b canary {c} in {g['chunk_id']}")
    return found, total


def test_every_query_in_deal_a_scope_returns_no_deal_b_records_or_canaries(fixture_index, request, monkeypatch):
    if request.config.getoption("--no-scope-filter"):
        remove_scope_filter(monkeypatch)
    index_dir, col, canaries = fixture_index
    found, total = leaks(index_dir, col, canaries, hashed_vector)
    assert len(QUERIES) == 60 and total > 0
    assert not found, f"{len(found)} deal_b leaks in deal_a scope, first: {found[:5]}"


def test_the_isolation_check_finds_leaks_when_the_filter_is_removed(fixture_index, monkeypatch):
    """The check has teeth: without the store filter the same queries return deal_b records and canaries."""
    remove_scope_filter(monkeypatch)
    index_dir, col, canaries = fixture_index
    found, _ = leaks(index_dir, col, canaries, hashed_vector)
    assert any("deal_b record" in f for f in found) and any("deal_b canary" in f for f in found)


def test_full_index_deal_a_scope_returns_no_deal_b(request, monkeypatch, tmp_path):
    """Local run against data/index (DVC); the query vectors come from the embed cache in replay."""
    chroma = ROOT / "data" / "index" / "chroma"
    canaries_file = ROOT / "data" / "dataroom" / "canaries_visible.json"
    if not chroma.exists():
        pytest.skip("data/index is not pulled (DVC); run locally after dvc pull")
    import shutil
    shutil.copytree(chroma, tmp_path / "chroma")                 # Chroma rewrites its sqlite file on open
    col = retrieve_mod.open_collection(tmp_path, **PINNED)
    deals = {d: bool(col.get(where={"deal_id": d}, limit=1)["ids"]) for d in ("deal_a", "deal_b")}
    if not all(deals.values()):
        pytest.skip(f"pending deal_a/deal_b: not in the index yet ({deals})")
    if request.config.getoption("--no-scope-filter"):
        remove_scope_filter(monkeypatch)
    monkeypatch.setenv("LANTERN_LLM_MODE", "replay")
    canaries = json.loads(canaries_file.read_text(encoding="utf-8"))
    found, total = leaks(tmp_path, col, canaries, None)
    assert total > 0 and not found, f"{len(found)} deal_b leaks in deal_a scope, first: {found[:5]}"
