## Routing policy and why

Not every document is worth the same processing, so each source goes down the cheapest path that still
gives us what we need from it. The policy is recorded in `params.yaml` under `ingest.routing`.

| Source | Route (`params.yaml`) | What we run |
|---|---|---|
| Comparables (public filers) | `xbrl+docling_items` | companyfacts + Arelle on the iXBRL for numbers; Docling on the filing's HTML for 10-K Items 1, 1A, 7 |
| Data room (private target) | `cs1_full+docling+ocr` | the full CS1 path (text layer, OCR, layout, Camelot) plus Docling |
| Synthetic web | `html_text` | visible text per block, allowlist + robots.txt |

**Comparables.** Public filers already publish their numbers as tagged XBRL, which is the filed figure
itself (tier 1). Parsing the PDF would only approximate those numbers, and CS1 measured how expensive
that approximation is: Docling on the PDF path costs 4.517 s per page at the median and 19.215 s at p95,
with a 3,597 MiB peak (`reports/benchmarks.md`, §3), about 276 s for a 61-page filing at the median.
So comparables skip rendering, pdfplumber, layout, Camelot and OCR. The only text we need, Items 1, 1A
and 7 of the 10-K, comes from Docling on the original HTML, with no render step. In our runs a whole
comparable (both filings downloaded, XBRL facts extracted, Items split) took 3.7–6.1 s with a peak of
732–875 MiB (`data/bench/cs2/ingest_comps.csv`) [replace with the variant run's figures].

**Data room.** A private target has no XBRL, so every number has to come from the documents themselves.
They also come in mixed formats (a narrative CIM, a spreadsheet, a call transcript, a scanned contract),
so no single cheap path covers them. And the CIM's claims are what Part 1 reconciles against the seller's
financials, so every number needs a page-and-box citation, which only the full CS1 path provides. That is
why the data room gets the expensive route: there is no cheaper source of truth.

**OCR on scanned pages.** A scanned contract has no text layer at all, so without OCR it yields nothing:
its fees, term and change-of-control clause would be lost. CS1 measured Docling at 100% WER on a scanned
page without OCR (`reports/eval.md`, §3), so the CS1 Tesseract path is kept. We also found that CS1's layout
OCRs each detected box separately: on the Lab 2 sample contract this clipped first letters ("MASTER" became
"ASTER") and missed a whole clause. Scanned pages therefore use the full-page OCR words, grouped into lines
by vertical overlap, which kept all 48 words on that sample. Every scanned contract in our data rooms
triggered OCR (`data/dataroom/*/export/work/parsed/ocr_log.csv`) [confirm on the variant run].

**Synthetic web.** Web pages are tier 5: they can only ever be labelled claims, never facts. So plain
visible text per block is enough, and layout or table parsing would add nothing. What the route does need
is safety: only the synthetic host and EDGAR are allowlisted, `robots.txt` is respected, and every URL is
logged as kept or refused (`data/web/crawl_log.csv`).

| Route | Main cost | What a cheaper path would lose |
|---|---|---|
| Comparables | Docling on HTML for three Items | nothing on numbers (XBRL is the filed figure); the Items text, without Docling |
| Data room | full CS1 path + OCR + Docling | without OCR, every scanned contract; without layout, page-and-box citations for the claims Part 1 reconciles |
| Web | almost none | nothing: tier-5 text never becomes a fact |
