# Retrieval evaluation (Part 3)

Brief Part 3, item 2: "Report recall@{5,10,20}, MRR and nDCG@10 by strategy and category, plus latency (p50/p95) and
index size. Choose a strategy on this evidence and pin it in params.yaml." Stage `retrieval_eval`, code
`src/retrieval_eval.py`, qrels `data/eval/qrels.jsonl` (committed in 713e63b, before any retrieval run).

Numbers come from `reports/retrieval_eval.json` (scores, chunk counts, vector bytes; the stage's metrics file),
`data/bench/cs2/retrieval_eval.csv` (latency and on-disk size, run `2026-11-07T00:49:38+00:00`), and
`data/eval/qrels.jsonl` (query counts). Reproduce with `LANTERN_LLM_MODE=replay dvc repro retrieval_eval`: it runs
offline, because the embed stage also puts the 60 query vectors (both models) in `data/embed_cache`. Two forced
reruns gave a byte-identical `reports/retrieval_eval.json`.

## What is measured

- **Collections**: the 6 chunk sets (`chunk.grid`: block, section and recursive at 200 and 400 tokens) x 2 embedding
  models (hosted `text-embedding-3-small`, open `bge-small-en-v1.5`) = 12 collections.
- **Methods**: hybrid (the retrieval API's ranking: dense + BM25 fused with RRF, `retrieval.hybrid.rrf_k` 60), and,
  for the diagnosis only, dense alone and BM25 alone. BM25 does not use the embedding model, so a chunk set's BM25
  scores are the same for hosted and open.
- **Query**: each query runs in its qrels scope (`kestrel` or `public`) with the filter applied in the store. Each
  retriever returns 20 chunks; the fused list keeps 20.
- **Unit**: judgments are per block (`doc_id:block_id`), retrieval returns chunks. The chunk ranking becomes a
  block ranking: walking the chunks in rank order, each block takes the rank of the first chunk it appears in.
  Cut-offs count blocks, so a 400-token chunk holding five blocks fills five places.
- **Metrics** (ranx 0.3.21): recall@5, recall@10, recall@20, MRR@20, nDCG@10 with linear gain (grade 2 counts twice
  grade 1). Grade 1 and 2 are relevant. A query with no result scores 0. Means are over the 60 queries and per
  category.
- **Latency**: wall time of the full hybrid call per query (query vector from the cache, dense query, BM25, fusion),
  after one untimed warm-up query, p50 and p95 over the 60 queries, one machine (`machine` column of the CSV).
- **Index size**: vector bytes = chunks x dimensions x 4 (float32). The Chroma directory on disk holds all 12
  collections together, with documents and metadata: 392,175,384 bytes (`chroma_dir_bytes`); Chroma does not split
  it per collection.

## Judging conventions

Copied unchanged from the conventions written with the qrels.

> ## Judging conventions (data/eval/qrels.jsonl)
>
> 60 queries: 30 on the Kestrel data room (scope `kestrel`) and 30 on the comparables' 10-K Items 1, 1A and 7
> (scope `public`). Categories: numeric lookup, section-seeking, table-seeking, definitional, cross-document
> comparison, data-room claims.
>
> 1. Judged per block (`<doc_id>:<block_id>`), against indexed blocks only. The hidden sheet `s007`, Figure blocks,
>    page footers, signature lines, OCR noise and the page-image strips Docling turned into one-row tables are
>    never relevant.
> 2. Grade 2: the block by itself answers the query. Grade 1: useful context, such as a footnote that qualifies the
>    answer, a table's caption or heading, or another source stating the same number.
> 3. The query type decides which source counts as the answer. Numeric lookup (what the number is): the seller
>    financials are 2 and a management document stating the number is 1. Data-room claims (what management says):
>    the claim is 2 and the seller financials are 1.
> 4. Cross-document queries: the block answering each side is 2.
> 5. Unlabelled grid fragments (a bare "59.3" or "Apr 2025") are not relevant: they cannot answer anything alone.
> 6. Blocks not listed for a query are treated as not relevant (grade 0).
> 7. Judgments were made from the plain records and plain-text search of the indexed blocks, before any output of
>    the retrieval system was seen. Because candidates came from keyword search, a relevant block that shares no
>    words with the query may have been missed; this biases the qrels slightly toward lexical (BM25) matches.
> 8. Queries and judgments were drafted with an AI assistant and then reviewed and accepted by the author.

## Results: all collections, hybrid, all 60 queries

Sorted by the choice rule below. Dense and BM25 columns are the same chunk set retrieved by one method alone.

| Collection | nDCG@10 | Recall@5 | Recall@10 | Recall@20 | MRR@20 | Dense nDCG@10 | BM25 nDCG@10 | Vector bytes | p50 ms | p95 ms |
|---|---|---|---|---|---|---|---|---|---|---|
| block-200-0-open | 0.3909 | 0.4177 | 0.5343 | 0.62 | 0.4508 | 0.3556 | 0.3184 | 5,434,368 | 195.22 | 237.56 |
| block-200-0-hosted | 0.3871 | 0.3774 | 0.4927 | 0.57 | 0.4734 | 0.3648 | 0.3184 | 21,737,472 | 192.8 | 209.55 |
| section-200-0-hosted | 0.3308 | 0.3385 | 0.4496 | 0.5478 | 0.3954 | 0.3105 | 0.3076 | 20,262,912 | 193.82 | 231.24 |
| block-400-0-open | 0.3169 | 0.3468 | 0.4288 | 0.5075 | 0.3777 | 0.3047 | 0.2788 | 2,646,528 | 146.89 | 151.51 |
| recursive-200-20-hosted | 0.303 | 0.3135 | 0.3593 | 0.5052 | 0.4163 | 0.3058 | 0.2565 | 21,749,760 | 203.86 | 291.26 |
| recursive-200-20-open | 0.3027 | 0.3208 | 0.4042 | 0.519 | 0.3818 | 0.2796 | 0.2565 | 5,437,440 | 197.26 | 303.48 |
| section-200-0-open | 0.3019 | 0.3347 | 0.4218 | 0.5482 | 0.3677 | 0.2758 | 0.3076 | 5,065,728 | 194.23 | 271.77 |
| block-400-0-hosted | 0.2936 | 0.3288 | 0.3829 | 0.4881 | 0.3671 | 0.2907 | 0.2788 | 10,586,112 | 147.55 | 158.45 |
| section-400-0-open | 0.2877 | 0.3083 | 0.3857 | 0.4718 | 0.3363 | 0.2882 | 0.2523 | 2,471,424 | 144.87 | 185.29 |
| section-400-0-hosted | 0.2842 | 0.2913 | 0.3954 | 0.4964 | 0.3246 | 0.3089 | 0.2523 | 9,885,696 | 145.82 | 149.99 |
| recursive-400-40-open | 0.2824 | 0.2871 | 0.344 | 0.4593 | 0.3409 | 0.283 | 0.2094 | 2,674,176 | 148.72 | 183.31 |
| recursive-400-40-hosted | 0.2585 | 0.2482 | 0.3163 | 0.3885 | 0.3243 | 0.2882 | 0.2094 | 10,696,704 | 150.26 | 154.83 |

The full per-category scores for every collection and method are in `reports/retrieval_eval.json`
(`collections.<name>.<method>.by_category`).

## Choice

**Rule** (fixed before the results were read): the collection with the highest overall hybrid nDCG@10; on a tie,
the higher recall@10; then the open model.

**Chosen: block strategy, 200 tokens, no overlap, open model (`block-200-0-open`)**, nDCG@10 0.3909. Pinned in
`params.yaml`: `retrieval.strategy: block`, `retrieval.chunk_tokens: 200`, `retrieval.overlap_tokens: 0`,
`retrieval.embed: open` (chunk_tokens was 400 before this evaluation).

The runner-up is the same chunk set with the hosted model (0.3871, a gap of 0.0038). The open model also has the
higher recall at every cut-off (recall@10 0.5343 against 0.4927); the hosted model has the higher MRR@20 (0.4734
against 0.4508) and dense-only nDCG@10 (0.3648 against 0.3556). The open model's vectors take a quarter of the
bytes (5,434,368 against 21,737,472) and its queries cost nothing.

## Choice per category

The chosen collection's hybrid scores per category, next to the collection that scores best on that category alone
(same rule).

| Category | Queries | Best collection (hybrid nDCG@10) | Chosen hybrid nDCG@10 | Recall@10 | MRR@20 | Chosen dense nDCG@10 | Chosen BM25 nDCG@10 |
|---|---|---|---|---|---|---|---|
| cross_document | 11 | block-200-0-open (0.3406) | 0.3406 | 0.447 | 0.4874 | 0.3092 | 0.2759 |
| dataroom_claims | 5 | block-200-0-open (0.3154) | 0.3154 | 0.5 | 0.3833 | 0.153 | 0.3311 |
| definitional | 9 | block-200-0-open (0.4431) | 0.4431 | 0.6825 | 0.4648 | 0.3701 | 0.3575 |
| numeric_lookup | 16 | block-200-0-hosted (0.5296) | 0.4499 | 0.6354 | 0.458 | 0.433 | 0.2741 |
| section_seeking | 10 | block-200-0-open (0.2695) | 0.2695 | 0.4333 | 0.3278 | 0.3216 | 0.2182 |
| table_seeking | 9 | section-200-0-hosted (0.5021) | 0.4719 | 0.4444 | 0.5535 | 0.4102 | 0.5145 |

- **cross_document, dataroom_claims, definitional, section_seeking**: the chosen collection is the best of the 12.
- **numeric_lookup** (16 queries, the largest category): the hosted twin `block-200-0-hosted` is better, 0.5296
  against 0.4499. This is the one category where the choice gives up a clear margin. It is kept because the same
  hosted collection is worse on four other categories (cross_document 0.3048, dataroom_claims 0.1898, definitional
  0.4327, section_seeking 0.2315, from the same file) and lower overall.
- **table_seeking**: `section-200-0-hosted` is better, 0.5021 against 0.4719 (0.0302). The chosen collection is
  close to it, and section-200-0-hosted is 0.0601 lower overall.
- One index serves every query, so one collection is pinned; no per-category routing (not asked for by the brief).

## Diagnosis: why the numbers are low

The best overall nDCG@10 is 0.3909 and recall@20 is 0.62: about 4 in 10 relevant blocks are not among the first 20
blocks returned. What the files show:

1. **Smaller chunks win everywhere.** For every strategy and model, 200 tokens beats 400 on hybrid nDCG@10 (block
   0.3909/0.3871 against 0.3169/0.2936; section 0.3019/0.3308 against 0.2877/0.2842; recursive 0.3027/0.303 against
   0.2824/0.2585, open/hosted). Part of this is how it is measured: cut-offs count blocks, and a 400-token chunk
   brings its neighbouring blocks into the top 10 with it, so fewer different passages fit. Part of it is real: a
   large chunk's vector averages several topics.
2. **Hybrid helps overall but not in every category.** For the chosen set, hybrid 0.3909 beats dense alone 0.3556
   and BM25 alone 0.3184. But BM25 alone beats hybrid on dataroom_claims (0.3311 against 0.3154) and table_seeking
   (0.5145 against 0.4719), and dense alone beats hybrid on section_seeking (0.3216 against 0.2695). RRF gives both
   lists equal weight, so when one retriever is much weaker on a query type (dense on dataroom_claims: 0.153) it
   pulls good results down.
3. **Dense retrieval is weakest on data-room claims and tables.** Dense nDCG@10 is 0.153 on dataroom_claims and
   0.4102 on table_seeking, against BM25's 0.3311 and 0.5145. Claims and table rows are short, specific strings
   (names, figures, row labels) that keyword matching handles better than a sentence embedding.
4. **Section-seeking is the lowest category** (0.2695). These queries ask for what a section says rather than for
   one fact.
5. **The qrels lean toward BM25** (convention 7): candidates were found by keyword search, so a relevant block that
   shares no words with its query may be unjudged and counted as a miss. That penalises whichever method finds such
   blocks, which is more likely to be dense retrieval than BM25.
6. **Small categories are noisy**: dataroom_claims has 5 queries, so one query moves its mean by 0.2 of the
   metric's range; differences of a few hundredths between collections on 5 to 11 queries should not be read as
   firm.

Not built (the brief lists a reranker as Part 2 stretch): a cross-encoder reranker over the top 20, or RRF weights per
retriever. Either would be a separate decision.

## Latency and size

p50 is 144.87 to 150.26 ms for the 400-token sets and 192.8 to 203.86 ms for the 200-token sets; p95 is 149.99 to
303.48 ms (`data/bench/cs2/retrieval_eval.csv`, run `2026-11-07T00:49:38+00:00`). The chosen collection: p50 195.22
ms, p95 237.56 ms. The 200-token sets have about twice as many chunks (3,538 for block-200-0 against 1,723 for
block-400-0, `chunks` in `reports/retrieval_eval.json`). `src/retrieve.py` rebuilds the BM25 index over the scope's
chunks on every call, so BM25 work grows with the chunk count; how the time splits between BM25 and the dense query
was not measured. Latency is not part of the choice rule.

## Isolation

Added with Part 3 item 3 (`tests/test_retrieval.py`).
