"""Part 3 item 2: the `retrieval_eval` stage. Every chunk set x {hosted, open} collection is scored against
data/eval/qrels.jsonl, for hybrid (the retrieval API's fused ranking) and, for the diagnosis, dense-only and BM25-only.

Per query: each retriever returns its top EVAL_DEPTH chunks in the query's scope (src/retrieve.py rank(), scope filter
in the store), fused with RRF to EVAL_DEPTH. The chunk ranking becomes a block ranking (judgments are doc_id:block_id):
walking the chunks in rank order, each block takes the position where it first appears (its best chunk), so cut-offs
count blocks. ranx scores it: recall@5/10/20, MRR@20, nDCG@10 (linear gain); grade >= 1 is relevant; a query with
nothing returned scores 0. Means are over all queries and per qrels category.

Index size is deterministic (chunk count x dimensions x 4 bytes, float32 vectors) and goes in the metrics file with
the scores: reports/retrieval_eval.json. Latency varies between runs, so it goes outside the stage outputs:
data/bench/cs2/retrieval_eval.csv (appended per run) holds p50/p95 of the full hybrid call per query (query vector from
the cache + both retrievers + fusion) after one warm-up query, and the on-disk size of data/index/chroma.
Runs in replay: every query vector is already in data/embed_cache (the embed stage embeds the qrels queries).
"""
from __future__ import annotations

import argparse
import csv
import json
import time
from pathlib import Path

import numpy as np
import yaml

EVAL_DEPTH = 20
METRICS = ["recall@5", "recall@10", "recall@20", "mrr@20", "ndcg@10"]
METHODS = ("hybrid", "dense", "bm25")
BENCH_FIELDS = ["run_at", "stage", "collection", "queries", "p50_ms", "p95_ms", "chroma_dir_bytes", "machine"]


def block_ranking(chunks: list[tuple[str, float]], meta: dict) -> dict[str, float]:
    """Chunk ranking -> {doc_id:block_id: score}; a block's score is set by the first (best) chunk it appears in and
    every block gets a unique, strictly decreasing score so ranx keeps this order."""
    out: dict[str, float] = {}
    for cid, _ in chunks:
        m = meta[cid]
        for bid in json.loads(m["block_ids"]):
            key = f"{m['doc_id']}:{bid}"
            if key not in out:
                out[key] = 1.0 / (len(out) + 1)
    return out


def score(qrels: list[dict], runs: dict[str, dict[str, float]]) -> dict:
    """ranx means over all queries and per category, rounded to 4 places."""
    from ranx import Qrels, Run, evaluate

    def mean(rows):
        q = Qrels({r["qid"]: r["relevant"] for r in rows})
        returned = {r["qid"]: runs[r["qid"]] for r in rows if runs.get(r["qid"])}
        if not returned:                                         # ranx cannot build an empty Run; nothing returned = 0
            return {m: 0.0 for m in METRICS}
        got = evaluate(q, Run(returned), METRICS, make_comparable=True)
        return {m: round(float(got[m]), 4) for m in METRICS}

    cats = sorted({r["category"] for r in qrels})
    return {"overall": mean(qrels), "by_category": {c: mean([r for r in qrels if r["category"] == c]) for c in cats}}


def dir_bytes(path: Path) -> int:
    return sum(f.stat().st_size for f in Path(path).rglob("*") if f.is_file())


def evaluate_collection(col, qrels: list[dict], which: str, params: dict, embed_query) -> tuple[dict, list[float]]:
    from src.retrieve import rank
    runs = {m: {} for m in METHODS}
    rank(qrels[0]["query"], qrels[0]["scope"], collection=col, which=which, params=params, depth=EVAL_DEPTH,
         embed_query=embed_query)                                           # warm-up, not timed
    times = []
    for r in qrels:
        t0 = time.perf_counter()
        got = rank(r["query"], r["scope"], collection=col, which=which, params=params, depth=EVAL_DEPTH,
                   embed_query=embed_query)
        times.append((time.perf_counter() - t0) * 1000.0)
        for m in METHODS:
            runs[m][r["qid"]] = block_ranking(got[m], got["meta"])
    return {m: score(qrels, runs[m]) for m in METHODS}, times


def run(qrels_path: Path, index_dir: Path, params: dict, embed_query_for) -> tuple[dict, list[dict]]:
    """Chroma rewrites chroma.sqlite3 whenever a persistent client opens it, even to read, which would change the
    embed stage's tracked output; so the queries run against a temporary copy of the index."""
    import shutil
    import tempfile
    qrels = [json.loads(l) for l in open(qrels_path, encoding="utf-8") if l.strip()]
    log = json.loads((index_dir / "index_log.json").read_text(encoding="utf-8"))
    with tempfile.TemporaryDirectory(prefix="lantern-eval-") as tmp:
        shutil.copytree(index_dir / "chroma", Path(tmp) / "chroma")
        return _run(qrels, log, Path(tmp), params, embed_query_for)


def _run(qrels: list[dict], log: dict, index_dir: Path, params: dict, embed_query_for) -> tuple[dict, list[dict]]:
    from src.index import collection_name
    from src.retrieve import open_collection
    report = {"settings": {"queries": len(qrels), "depth": EVAL_DEPTH, "relevant_grade_min": 1,
                           "ndcg_gain": "linear", "rrf_k": int(params["retrieval"]["hybrid"]["rrf_k"]),
                           "unit": "block (doc_id:block_id), best chunk per block"},
              "collections": {}}
    latency = []
    for strategy, grid in params["chunk"]["grid"].items():
        for size, overlap in grid:
            for which in ("hosted", "open"):
                name = collection_name(strategy, size, overlap, which)
                col = open_collection(index_dir, strategy, size, overlap, which)
                scores, times = evaluate_collection(col, qrels, which, params, embed_query_for(which))
                n, dims = log[name]["count"], log[name]["dims"]
                report["collections"][name] = {"strategy": strategy, "chunk_tokens": size, "overlap_tokens": overlap,
                                               "embed": which, "chunks": n, "dims": dims,
                                               "index_vector_bytes": n * dims * 4, **scores}
                latency.append({"collection": name, "queries": len(times),
                                "p50_ms": round(float(np.percentile(times, 50)), 2),
                                "p95_ms": round(float(np.percentile(times, 95)), 2)})
                h = scores["hybrid"]["overall"]
                print(f"[INFO] retrieval_eval {name}: hybrid nDCG@10 {h['ndcg@10']} recall@10 {h['recall@10']}")
    return report, latency


def main() -> None:
    ap = argparse.ArgumentParser(description="Part 3 retrieval_eval stage")
    ap.add_argument("--qrels", default="data/eval/qrels.jsonl")
    ap.add_argument("--index", default="data/index")
    ap.add_argument("--out", default="reports/retrieval_eval.json")
    ap.add_argument("--bench", default="data/bench/cs2/retrieval_eval.csv")
    ap.add_argument("--params", default="params.yaml")
    args = ap.parse_args()
    from src.ingest.runstats import RunStats
    from src.llm import client
    from src.retrieve import query_vector
    if client.MODE != "replay":
        raise SystemExit("retrieval_eval runs in replay only (LANTERN_LLM_MODE=replay); query vectors come from the "
                         "embed stage's cache")
    params = yaml.safe_load(Path(args.params).read_text(encoding="utf-8"))
    index = Path(args.index)
    report, latency = run(Path(args.qrels), index, params, lambda which: (lambda q: query_vector(q, which, params)))
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(report, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    meta = RunStats("retrieval_eval", args.bench)                  # only for run_at and machine, same as other stages
    bench = Path(args.bench)
    bench.parent.mkdir(parents=True, exist_ok=True)
    new = not bench.exists()
    size = dir_bytes(index / "chroma")
    with open(bench, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=BENCH_FIELDS)
        if new:
            w.writeheader()
        for row in latency:
            w.writerow({"run_at": meta.run_at, "stage": "retrieval_eval", "chroma_dir_bytes": size,
                        "machine": meta.machine, **row})
    print(f"[INFO] retrieval_eval: {len(report['collections'])} collections -> {args.out}; latency -> {bench}")


if __name__ == "__main__":
    main()
