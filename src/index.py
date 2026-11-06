"""Part 2 items 3-4: the `embed` stage. Every chunk set in data/chunks x {hosted, open} -> a Chroma collection.

Embeddings go through src/llm/client.py's embed() (cache: data/embed_cache, one .npy per text). In record mode the
vectors a set is missing are computed first in batches, so the client's per-text compute() call only reads them:
  hosted  client.PROVIDER_EMBED (llm.embed.hosted), budget_check before and a ledger row after each batch
  open    sentence-transformers llm.embed.open at llm.embed.open_revision, imported only here (record mode); a ledger
          row per batch at price 0
Ledger token counts use tiktoken cl100k_base (record mode only). Replay never computes: a cache miss raises in the
client, and sentence-transformers and tiktoken are never imported.
Chroma: a persistent client in data/index/chroma, telemetry off, no embedding function, cosine space; one collection
per {strategy}-{size}-{overlap}-{hosted|open}, metadata deal_id, doc_id, section, tier, page (-1 when none),
source_type, kind, block_ids (JSON), chunk_id. data/index/index_log.json holds counts, dimensions and a vector hash
per collection; build times go to data/bench/cs2/embed.csv (runstats).
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
import yaml

BATCH = 256


def models(params: dict) -> dict[str, dict]:
    e = params["llm"]["embed"]
    return {"hosted": {"name": e["hosted"], "cache_model": e["hosted"]},
            "open": {"name": e["open"], "cache_model": f"{e['open']}@{e['open_revision']}",
                     "revision": e["open_revision"]}}


def collection_name(strategy: str, size: int, overlap: int, which: str) -> str:
    return f"{strategy}-{size}-{overlap}-{which}"


def cache_path(client, cache_model: str, text: str) -> Path:
    return client.EMB / f"{hashlib.sha256(f'{cache_model}|{text}'.encode()).hexdigest()}.npy"


def precompute(client, which: str, m: dict, texts: list[str]) -> dict[str, np.ndarray]:
    """Record mode only: vectors for texts missing from the cache, in batches, with ledger rows."""
    import tiktoken
    enc = tiktoken.get_encoding("cl100k_base")
    out = {}
    st = None
    for i in range(0, len(texts), BATCH):
        batch = texts[i:i + BATCH]
        tokens = sum(len(enc.encode(t)) for t in batch)
        client.budget_check(m["name"], tokens, 0)
        if which == "hosted":
            vecs = client.PROVIDER_EMBED(batch, model=m["name"])
        else:
            if st is None:
                from sentence_transformers import SentenceTransformer
                st = SentenceTransformer(m["name"], revision=m["revision"], device="cpu")
            vecs = st.encode(batch, batch_size=64, normalize_embeddings=True, show_progress_bar=False)
        client.ledger(m["name"], tokens, 0, "embed")
        out.update({t: np.asarray(v, dtype=np.float32) for t, v in zip(batch, vecs)})
    return out


def embed_texts(client, which: str, m: dict, texts: list[str]) -> np.ndarray:
    pre = {}
    if client.MODE != "replay":
        missing = sorted({t for t in texts if not cache_path(client, m["cache_model"], t).exists()})
        if missing:
            pre = precompute(client, which, m, missing)
    return client.embed(texts, m["cache_model"], lambda ts: [pre[t] for t in ts])


def metadata(r: dict) -> dict:
    return {"chunk_id": r["chunk_id"], "deal_id": r["deal_id"], "doc_id": r["doc_id"], "section": r["section"] or "",
            "tier": int(r["tier"]), "page": -1 if r["page"] is None else int(r["page"]),
            "source_type": r["source_type"], "kind": r["kind"], "block_ids": json.dumps(r["block_ids"])}


def vector_hash(col) -> str:
    got = col.get(include=["embeddings"])
    order = sorted(range(len(got["ids"])), key=lambda i: got["ids"][i])
    h = hashlib.sha256()
    for i in order:
        h.update(got["ids"][i].encode())
        h.update(np.asarray(got["embeddings"][i], dtype=np.float32).tobytes())
    return h.hexdigest()


def build(chunks_dir: Path, index_dir: Path, params: dict, client, stats=None) -> dict:
    import chromadb
    from chromadb.config import Settings
    db = chromadb.PersistentClient(path=str(index_dir / "chroma"), settings=Settings(anonymized_telemetry=False))
    maxb = db.get_max_batch_size()
    log = {}
    for strategy, grid in params["chunk"]["grid"].items():
        for size, overlap in grid:
            rows = [json.loads(l) for l in open(chunks_dir / strategy / f"{size}_{overlap}.jsonl", encoding="utf-8")]
            texts = [r["text"] for r in rows]
            for which, m in models(params).items():
                name = collection_name(strategy, size, overlap, which)
                ctx = stats.item(name, "chunks") if stats else _null()
                with ctx as s:
                    vecs = embed_texts(client, which, m, texts)
                    if name in [c.name for c in db.list_collections()]:
                        db.delete_collection(name)
                    col = db.create_collection(name, embedding_function=None,
                                               configuration={"hnsw": {"space": "cosine"}})
                    for i in range(0, len(rows), maxb):
                        col.add(ids=[r["chunk_id"] for r in rows[i:i + maxb]],
                                embeddings=vecs[i:i + maxb].tolist(), documents=texts[i:i + maxb],
                                metadatas=[metadata(r) for r in rows[i:i + maxb]])
                    s["units"] = col.count()
                if stats and s["status"] != "ok":
                    raise RuntimeError(f"{name}: {s['error']}")
                log[name] = {"count": col.count(), "dims": int(vecs.shape[1]), "model": m["cache_model"],
                             "vector_hash": vector_hash(col)}
                print(f"[INFO] embed {name}: {log[name]['count']} chunks, {log[name]['dims']} dims")
    return log


class _null:
    def __enter__(self):
        return {"status": "ok", "units": 0}

    def __exit__(self, *a):
        return False


def main() -> None:
    ap = argparse.ArgumentParser(description="Part 2 embed stage")
    ap.add_argument("--chunks", default="data/chunks")
    ap.add_argument("--index", default="data/index")
    ap.add_argument("--params", default="params.yaml")
    ap.add_argument("--stats", default=None, help="run-stats CSV (default data/bench/cs2/embed.csv)")
    args = ap.parse_args()
    from src.ingest.runstats import RunStats
    from src.llm import client
    params = yaml.safe_load(Path(args.params).read_text(encoding="utf-8"))
    index = Path(args.index)
    index.mkdir(parents=True, exist_ok=True)
    stats = RunStats("embed", args.stats)
    log = build(Path(args.chunks), index, params, client, stats)
    stats.finish()
    (index / "index_log.json").write_text(json.dumps(log, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print(f"[INFO] embed: {len(log)} collections -> {index / 'chroma'} (mode {client.MODE})")


if __name__ == "__main__":
    main()
