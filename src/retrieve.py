"""Part 2 items 5-6: hybrid retrieval with scope enforcement.

retrieve(query, scope, ...) refuses a missing or unknown scope before touching the store (ScopeError). The scope
filter is applied in the store for both retrievers:
  dense  Chroma query(where=...) on the collection {strategy}-{size}-{overlap}-{hosted|open}
  BM25   rank-bm25 over only the documents collection.get(where=...) returns (lower-case word tokens)
and the two rankings are fused with reciprocal rank fusion, score = sum 1 / (retrieval.hybrid.rrf_k + rank).
Scopes: deal_a, deal_b, kestrel return that deal's chunks plus public ones; public returns only public chunks.
The query embedding goes through the same cache as the index (src/index.py embed_texts); the open model gets the
bge query prefix from its model card. No reranker.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

SCOPES = ("deal_a", "deal_b", "kestrel", "public")
BGE_QUERY_PREFIX = "Represent this sentence for searching relevant passages: "


class ScopeError(ValueError):
    """A query without a valid scope."""


def scope_filter(scope: str | None) -> dict:
    if scope not in SCOPES:
        raise ScopeError(f"a query needs a scope, one of {SCOPES}; got {scope!r}")
    allowed = ["public"] if scope == "public" else [scope, "public"]
    return {"deal_id": {"$in": allowed}}


def tokenize(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", (text or "").lower())


def rrf(rankings: list[list[str]], k: int) -> list[tuple[str, float]]:
    scores: dict[str, float] = {}
    for ranking in rankings:
        for rank, cid in enumerate(ranking, 1):
            scores[cid] = scores.get(cid, 0.0) + 1.0 / (k + rank)
    return sorted(scores.items(), key=lambda x: (-x[1], x[0]))


def query_vector(query: str, which: str, params: dict):
    from src.index import embed_texts, models
    from src.llm import client
    text = (BGE_QUERY_PREFIX + query) if which == "open" else query
    return embed_texts(client, which, models(params)[which], [text])[0]


def retrieve(query: str, scope: str | None = None, *, strategy: str, size: int, overlap: int, which: str,
             params: dict, index_dir: Path = Path("data/index"), k: int | None = None, embed_query=None) -> list[dict]:
    where = scope_filter(scope)                                  # refuse before touching the store
    from rank_bm25 import BM25Okapi
    import chromadb
    from chromadb.config import Settings
    from src.index import collection_name
    r = params["retrieval"]
    k = k or int(r["k"])
    db = chromadb.PersistentClient(path=str(Path(index_dir) / "chroma"), settings=Settings(anonymized_telemetry=False))
    col = db.get_collection(collection_name(strategy, size, overlap, which))
    qv = (embed_query or (lambda q: query_vector(q, which, params)))(query)
    dense = col.query(query_embeddings=[list(map(float, qv))], n_results=k, where=where, include=["metadatas"])
    dense_ids = dense["ids"][0]
    pool = col.get(where=where, include=["documents", "metadatas"])
    meta = dict(zip(pool["ids"], pool["metadatas"]))
    bm25_ids = []
    if pool["ids"]:
        scores = BM25Okapi([tokenize(d) for d in pool["documents"]]).get_scores(tokenize(query))
        bm25_ids = [cid for _, cid in sorted(zip(scores, pool["ids"]), key=lambda x: (-x[0], x[1]))[:k]]
    fused = rrf([dense_ids, bm25_ids], int(r["hybrid"]["rrf_k"]))[:k] if r["hybrid"]["enabled"] else \
        [(cid, 0.0) for cid in dense_ids]
    out = []
    for cid, score in fused:
        m = meta[cid]
        out.append({"chunk_id": cid, "score": score, "deal_id": m["deal_id"], "doc_id": m["doc_id"],
                    "section": m["section"], "page": None if m["page"] == -1 else m["page"],
                    "block_ids": json.loads(m["block_ids"]), "kind": m["kind"]})
    return out
