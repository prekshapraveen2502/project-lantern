# Retrieval design (Part 2)

Brief Part 2: chunking strategies, table chunks, hosted and open embeddings through the cache, a vector store with
metadata filters, hybrid retrieval with reciprocal rank fusion, and scope enforcement. Stages `chunk` and `embed`;
code in `src/chunk.py`, `src/index.py`, `src/retrieve.py`. Retrieval quality is measured in Part 3 against qrels
written before any retrieval output is looked at; this report shows no retrieval results.

Numbers below come from `data/chunks/chunk_log.json`, `data/index/index_log.json`, `data/bench/cs2/embed.csv`,
`logs/cost_ledger.jsonl` and `params.yaml`.

## Corpus

| Source | deal_id | Units |
|---|---|---|
| Kestrel data room (`data/dataroom/kestrel/export/records.jsonl`) | kestrel | Part 0 records; Figure blocks and the hidden sheet `s007` ("Draft_v3 (do not use)") left out |
| Comparables' Docling Items 1, 1A, 7 (`data/edgar_comps/{T}/10-K/{accn}/items/`) | public | each blank-line-separated paragraph or Markdown table is a block, block_id `{item}_b{nnnn}` |

6,120 blocks in total. Earnings-release HTML and CS1's AAPL records are not indexed; deal_a, deal_b and the synthetic
website do not exist yet.

## Chunking strategies

Token budgets are counted with the open model's tokenizer (bge-small-en-v1.5 WordPiece, files only, DVC-tracked in
`data/tokenizer/`), so no chunk exceeds the model's 512-token input. Sizes and overlaps are `chunk.grid` in
`params.yaml`.

- **block**: merges consecutive text blocks within a section up to the budget; a block longer than the budget is
  split on its own; never merges a table with text.
- **section**: packs a section's text across paragraph boundaries (sentence, then space separators) and prefixes
  every text chunk with the section heading, counted in the budget: filings "Item N | <the Item's first line>"
  (Docling flattened the sub-headings, so the Item is the section); CIM, deck, contracts, QoE the CS1 section
  title with `(cid:N)` tokens removed; the transcript its first line.
- **recursive**: the baseline, LangChain's RecursiveCharacterTextSplitter over each document's text.

| Set | Chunks | Table chunks | Max tokens | Tokens |
|---|---|---|---|---|
| block/200_0 | 3,538 | 314 | 200 | 465,427 |
| block/400_0 | 1,723 | 286 | 400 | 464,490 |
| section/200_0 | 3,298 | 314 | 200 | 497,545 |
| section/400_0 | 1,609 | 286 | 400 | 478,559 |
| recursive/200_20 | 3,540 | 314 | 200 | 469,961 |
| recursive/400_40 | 1,741 | 286 | 400 | 476,592 |

## Table-chunk policy

Every strategy indexes each table as its own chunk(s), linked to the table's `block_id`:

1. **Caption**: PDF tables the nearest preceding Title/Text block on the same page; xlsx the sheet's title cell;
   Markdown tables the preceding paragraph if it is at most 200 characters, else none.
2. **Scale**: the first `scale_phrases` word (thousands, millions, billions) found in the caption, the header row or
   the rows above the header.
3. **Header row**: xlsx the row holding `FYyyyy` labels; PDF and Markdown tables the first row.
4. **Rows**, one per line, cells separated by `|`.
5. **Splitting**: a table longer than the budget is split into row groups; every part repeats the caption, scale and
   header, and all parts carry the same `block_id` (`table_part` / `table_parts`). A single row longer than the
   budget stops the stage.
6. **Compaction (chunk text only)**: empty cells are dropped and a cell equal to the previous cell is merged into it,
   because Docling writes a merged cell's text once per spanned column. Records, blocks and block text are unchanged,
   so Part 4 citations still resolve against the block text. Without compaction, 123 rows in 21 of 277 tables were
   over the 200-token budget (1 row over 400); with it, none.
   **Known side effect**: genuinely equal adjacent cells (for example two periods with the same value) are merged in
   the chunk text, and dropping an empty first header cell shifts the header one column left of the row labels.

**Example 1, xlsx sheet (Kestrel P&L, `DR-kestrel-005` `s001`, block/400, 153 tokens):**

```
Kestrel Pay, Inc. – P&L
Scale: millions
FY2023 | FY2024 | FY2025
Gross revenue | 61.8 | 78.9 | 96.4
Pass-through costs (interchange, network fees) | -23.5 | -30.2 | -37.1
Net revenue | 38.3 | 48.7 | 59.3
  of which: subscription | 9.6 | 13.1 | 17.4
  of which: transaction | 28.7 | 35.6 | 41.9
Operating expenses (excl. D&A) | -35.2 | -43.3 | -51.4
Reported EBITDA | 3.1 | 5.4 | 7.9
```

**Example 2, compaction (BILL 10-K Item 7, block `7_b0074`).** Block text row (as written by Docling, truncated):

```
| Income (loss) before provision for income taxes | Income (loss) before provision for income taxes | Income (loss) before provision for income taxes | (10,131)            | (10,131)            |                     | ...
```

The same row in the chunk text:

```
Income (loss) before provision for income taxes | (10,131) | 30,410 | (26,319) | (40,541) | (133) | % | 56,729 | (216) | %
```

The table is split into 3 parts at block/400 (374, 373 and 243 tokens), each repeating caption and header.

**Example 3, a split PDF table (CIM p7 monthly operating metrics, `DR-kestrel-001` `p0007_b003`).** Camelot accepted
no table on this page, so the rows come from the record's text lines, one value per line; at block/400 it splits into
2 parts (400 and 34 tokens), part 2 starting:

```
Monthly operating metrics (USD millions)
Scale: millions
Month
421.5
```

This is a known limitation: without a Camelot table the row structure is lost (a header of "Month" and one value per
line).

## Embeddings, cost and build time

| Model | Pin | Dimensions | Price, USD per 1M input tokens (`llm.prices`) | Tokens embedded (ledger) | Cost (ledger) |
|---|---|---|---|---|---|
| text-embedding-3-small (hosted) | model id | 1,536 | 0.02 (checked 2026-11-06) | 2,116,525 | 0.042332 |
| BAAI/bge-small-en-v1.5 (open, local CPU) | HF revision 5c38ec7c405ec4b44b94cc5a9bb96e735b38267a | 384 | 0 | 2,116,525 | 0.0 |

Tokens are counted with tiktoken cl100k_base in record mode (44 ledger rows per model). Each unique chunk text is
embedded once per model; the cache (`data/embed_cache/`, one `.npy` per text) is shared across chunk sets, so
table chunks identical across strategies are computed once.

Index build time (record run, `data/bench/cs2/embed.csv`, Darwin arm64, 10 threads, 16.0 GiB, Python 3.11.17):

| Collection | Seconds | Chunks | Peak MiB |
|---|---|---|---|
| block-200-0-hosted | 20.241 | 3,538 | 507.6 |
| block-200-0-open | 57.313 | 3,538 | 1,186.3 |
| block-400-0-hosted | 6.995 | 1,723 | 763.5 |
| block-400-0-open | 44.162 | 1,723 | 1,343.2 |
| section-200-0-hosted | 14.148 | 3,298 | 933.7 |
| section-200-0-open | 39.19 | 3,298 | 1,123.9 |
| section-400-0-hosted | 7.803 | 1,609 | 1,152.5 |
| section-400-0-open | 41.407 | 1,609 | 1,552.6 |
| recursive-200-20-hosted | 7.373 | 3,540 | 1,062.4 |
| recursive-200-20-open | 10.331 | 3,540 | 1,364.3 |
| recursive-400-40-hosted | 4.69 | 1,741 | 1,306.5 |
| recursive-400-40-open | 22.321 | 1,741 | 1,954.0 |

Total 275.974 s. Collections built later reuse cached vectors of texts shared with earlier sets, so per-collection
times depend on build order. The replay run rebuilt all 12 collections from the cache in 18.025 s with no network and
no key; collection counts and vector hashes matched the record run.

## Vector store and hybrid retrieval

Chroma (`chromadb==1.5.9`), persistent in `data/index/chroma`, telemetry off, no embedding function (vectors are
always passed in), cosine space. One collection per `{strategy}-{size}-{overlap}-{hosted|open}` (12). Chunk metadata:
`deal_id`, `doc_id`, `section`, `tier`, `page` (-1 when the source has no pages), `source_type`, `kind`
(text/table), `block_ids`, `chunk_id`.

`retrieve(query, scope, ...)` refuses a missing or unknown scope before opening the store. The scope filter
(`deal_a`, `deal_b`, `kestrel`: that deal plus `public`; `public`: only `public`) is applied in the store for both
retrievers: dense `collection.query(where=...)`, and BM25 (rank-bm25, lower-case word tokens) over only the
documents `collection.get(where=...)` returns. The two rankings are fused with reciprocal rank fusion,
1 / (`retrieval.hybrid.rrf_k` + rank), `rrf_k` = 60. No reranker. The open model's queries get the bge query prefix
from its model card.
