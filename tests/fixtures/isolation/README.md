# Isolation fixture (Part 3 item 3)

Two toy deals and a few public chunks, committed to Git so CI (no DVC access) can run the isolation test in
`tests/test_retrieval.py`. All text is made up; nothing comes from a real or synthetic data room.

- `chunks.jsonl`: 8 `deal_a` chunks, 8 `deal_b` chunks, 3 `public` chunks (`chunk_id`, `deal_id`, `doc_id`, `text`).
  The deal_b chunks use the same themes as the qrels queries (revenue, payment volume, cash, term loan, margins,
  definitions, risk factors, management claims, segment tables), so without the scope filter they rank near the top
  for most queries.
- `canaries.json`: planted canary strings in the brief's `canaries_visible.json` format (`{"deal_a": [...],
  "deal_b": [...]}`). Each deal_b chunk carries one deal_b canary; one deal_a chunk carries the deal_a canary.

The test embeds the chunks with a hashed bag-of-words vector (no model, no network) into a temporary Chroma
collection and calls `src/retrieve.py` `retrieve()` with the pinned `retrieval` params.
