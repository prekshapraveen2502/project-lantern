"""
Part 2 items 3-4 tests: the embed stage (src/index.py) on toy chunk sets with a fake LLM client; no model weights,
no network. A separate test checks the real client's replay behaviour for embeddings.
"""
import hashlib
import json
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import src.index as index  # noqa: E402

PARAMS = {"chunk": {"grid": {"block": [[200, 0]]}},
          "llm": {"embed": {"hosted": "h-model", "open": "o-model", "open_revision": "abc123"}}}


class FakeClient:
    """Same cache contract as src/llm/client.py embed(): one .npy per sha256(model|text); replay raises on a miss."""
    def __init__(self, mode, emb):
        self.MODE, self.EMB, self.ledger_rows, self.budget_calls = mode, emb, [], 0

    def budget_check(self, model, tin, tout=1024):
        self.budget_calls += 1

    def ledger(self, model, tin, tout, stage):
        self.ledger_rows.append((model, tin, tout, stage))

    def PROVIDER_EMBED(self, texts, model=None):
        return [[float(len(t)), 1.0, 0.0] for t in texts]

    def embed(self, texts, model, compute):
        vecs = []
        for t in texts:
            f = self.EMB / f"{hashlib.sha256(f'{model}|{t}'.encode()).hexdigest()}.npy"
            if f.exists():
                vecs.append(np.load(f)); continue
            if self.MODE == "replay":
                raise RuntimeError(f"embedding cache miss {f.name}")
            v = np.asarray(compute([t])[0], dtype=np.float32)
            np.save(f, v)
            vecs.append(v)
        return np.vstack(vecs)


def chunks(tmp_path):
    rows = [{"chunk_id": f"c{i}", "deal_id": d, "doc_id": f"D{i}", "section": "Item 1", "tier": 2, "page": None,
             "source_type": "edgar_filing", "kind": "text", "block_ids": [f"1_b{i:04d}"], "text": f"text {i} " * (i + 1)}
            for i, d in enumerate(["public", "kestrel", "public"])]
    (tmp_path / "chunks" / "block").mkdir(parents=True, exist_ok=True)
    (tmp_path / "chunks" / "block" / "200_0.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    return tmp_path / "chunks"


@pytest.fixture
def fake_open(monkeypatch):
    """The open model is never loaded in tests: precompute for 'open' returns fixed vectors."""
    real = index.precompute
    def pre(client, which, m, texts):
        if which == "open":
            client.ledger(m["name"], len(texts), 0, "embed")
            return {t: np.array([0.0, 1.0, float(len(t))], dtype=np.float32) for t in texts}
        return real(client, which, m, texts)
    monkeypatch.setattr(index, "precompute", pre)


def test_record_builds_one_collection_per_set_and_model_with_metadata(tmp_path, fake_open):
    (tmp_path / "emb").mkdir()
    client = FakeClient("record", tmp_path / "emb")
    log = index.build(chunks(tmp_path), tmp_path / "index", PARAMS, client)
    assert sorted(log) == ["block-200-0-hosted", "block-200-0-open"]
    assert all(v["count"] == 3 and v["dims"] == 3 for v in log.values())
    assert [r[0] for r in client.ledger_rows] == ["h-model", "o-model"] and client.budget_calls == 1
    import chromadb
    from chromadb.config import Settings
    col = chromadb.PersistentClient(path=str(tmp_path / "index" / "chroma"),
                                    settings=Settings(anonymized_telemetry=False)).get_collection("block-200-0-hosted")
    got = col.get(ids=["c1"], include=["metadatas"])["metadatas"][0]
    assert got == {"chunk_id": "c1", "deal_id": "kestrel", "doc_id": "D1", "section": "Item 1", "tier": 2, "page": -1,
                   "source_type": "edgar_filing", "kind": "text", "block_ids": '["1_b0001"]'}


def test_replay_rebuilds_the_same_index_from_the_cache_without_computing(tmp_path, fake_open, monkeypatch):
    (tmp_path / "emb").mkdir()
    rec = index.build(chunks(tmp_path), tmp_path / "index", PARAMS, FakeClient("record", tmp_path / "emb"))
    monkeypatch.setattr(index, "precompute", lambda *a: pytest.fail("replay must not compute"))
    rep = index.build(chunks(tmp_path), tmp_path / "index2", PARAMS, FakeClient("replay", tmp_path / "emb"))
    assert {k: (v["count"], v["vector_hash"]) for k, v in rep.items()} == \
           {k: (v["count"], v["vector_hash"]) for k, v in rec.items()}


def test_replay_with_an_empty_cache_raises(tmp_path, monkeypatch):
    (tmp_path / "emb").mkdir()
    monkeypatch.setattr(index, "precompute", lambda *a: pytest.fail("replay must not compute"))
    with pytest.raises(RuntimeError, match="cache miss"):
        index.build(chunks(tmp_path), tmp_path / "index", PARAMS, FakeClient("replay", tmp_path / "emb"))


def test_real_client_embed_replay_miss_raises_before_compute(monkeypatch):
    monkeypatch.setenv("LANTERN_LLM_MODE", "replay")
    monkeypatch.chdir(ROOT)
    import importlib
    import src.llm.client as client
    client = importlib.reload(client)
    with pytest.raises(RuntimeError, match="embedding cache miss"):
        client.embed(["uncached toy text 91c2"], "BAAI/bge-small-en-v1.5@abc", lambda ts: pytest.fail("compute ran"))
