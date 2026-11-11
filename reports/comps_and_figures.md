# Comps, figures and chart reads (Part 6)

Brief Part 6 items 1–4. Code: `src/calc/core.py`, `src/comps/`, `src/figures/`; tests: `tests/test_calc.py`,
`tests/test_comps.py`, `tests/test_figures.py`. Stages: `comps_table`, `figures`. Every number below is copied
from the file named next to it.

## 1. Calculator (`src/calc/core.py`)

Pure functions; each output has a `calc_id` (`CALC-` + 12 hex digits of sha256 over the op and its `role:id`
inputs) and a trace. Part 6 outputs also carry `fact_ids`: every fact and price the value rests on, through nested
calcs. The Part 4 ops (`growth`, `ratio`, `difference`) are unchanged, so Part 4 and 5 outputs replay byte-identical.

| Calculator | Definition used |
|---|---|
| `ltm` | latest FY + current YTD − prior-year YTD. Both YTDs must have the same period label (Q1, H1, 9M); the prior YTD must end inside the FY and the current one 358–372 days later. When no YTD follows the latest FY (the latest filing is the 10-K: BILL, FY ending 2026-06-30), LTM is that FY |
| `growth` | current / prior − 1 (Part 4). In the comps table: latest FY revenue over the prior FY, because the ingested filings hold no prior-year LTM |
| `gross_profit`, `margin` | gross margin = gross profit / revenue; gross profit is the filer's tag where present, else revenue − cost of revenue |
| `ebitda` | **EBITDA = operating income + depreciation and amortization** (the `d_and_a` metric: cash-flow D&A), on the same LTM basis as revenue. Not "adjusted": no add-backs |
| `net_debt` | debt − cash − short-term investments |
| `enterprise_value` | snapshot price × shares outstanding (every class of `dei:EntityCommonStockSharesOutstanding` at its latest date on or before the price date, all at the one listed price) + debt + preferred + NCI − cash − short-term investments |
| `multiple` | EV / LTM revenue and EV / LTM EBITDA; a denominator ≤ 0 is not meaningful (`n.m.`, empty cell) |

**Short-term-investments policy.** Short-term investments are treated as cash-like and subtracted where the filer
tags them (`short_term_investments`: `ShortTermInvestments`, else `AvailableForSaleSecuritiesDebtSecuritiesCurrent`,
else `MarketableSecuritiesCurrent`). Cash is cash and cash equivalents only. Restricted cash, customer funds and
funds receivable (large at PayPal and Block) are not subtracted, and neither are long-term investments.

**Sign convention.** The store keeps costs positive (`config/metrics_vocab.yaml`, `sign: cost`). The calculators
refuse a negative cost of revenue, D&A, debt, cash or preferred (`CalcError`); they never flip one silently.
Operating income, EBITDA, net debt and NCI keep their sign: XYZ's NCI is −$35.8m and is added as negative; BILL's
LTM EBITDA is negative, so its EV/EBITDA is `n.m.`. Tests: `test_sign_convention_*` in `tests/test_calc.py`.

**Debt.** This uses the filer's own total (`total_debt`: GPN, FOUR, BILL) where it is tagged, else the sum of its
`debt_component` facts at the balance-sheet date (PYPL, XYZ). Each component is one concept, so there is no
double counting.

**Price.** `data/reference/prices_asof_20260930.csv`: Yahoo Finance historical quotes, unadjusted Close as of
2026-09-30, downloaded by Preksha (DECISIONS 2026-11-10; Stooq's CSV endpoint serves a JavaScript browser check).
`tools/fetch_prices.py` validates it (8 tickers, date, positive closes, an https source per row) and prints its
sha256 `c1c5932597a9d7017e0b20dca5d01e657519ecbbc442ddb079a2b2953e5e4f54`; it is tracked with DVC. In traces a
price is `PRICE-<TICKER>-20260930`, tier 1 (the brief's fixed reference input).

## 2. Comps table (`outputs/comps/deal_a.csv`, `deal_a.md`)

The table is built for the variant's sector archetype (`b2b_payments_saas`, `reports/comps_selection.md`) from
`params.yaml` → `comps.tickers`. It uses no Deal A data; Deal A has not been generated yet. The CSV has one row per
comparable, and every metric has a companion `<metric>_calc_id` column (brief Section 8). Each calc's trace is in
`outputs/comps/deal_a_calcs.jsonl` (84 calcs). The values below are from `outputs/comps/deal_a.md`.

| Ticker | LTM revenue | Revenue growth (FY) | Gross margin (LTM) | EBITDA margin (LTM) | Net debt | Enterprise value | EV / LTM revenue | EV / LTM EBITDA |
|---|---|---|---|---|---|---|---|---|
| BILL | $1,653.2m | 13.0% | 80.9% | -1.1% | $-101.4m | $3,526.3m | 2.13x | n/a |
| FLYW | $713.5m | 26.6% | n/a | 9.6% | $-294.2m | $1,835.0m | 2.57x | 26.83x |
| PAYO | $1,081.4m | 7.7% | n/a | 17.4% | $-346.3m | $2,073.1m | 1.92x | 11.02x |
| FOUR | $4,782.0m | 25.5% | n/a | 19.3% | $4,155.0m | $7,128.0m | 1.49x | 7.74x |
| TOST | $6,804.0m | 24.1% | 26.7% | 7.1% | $-1,015.0m | $15,741.2m | 2.31x | 32.66x |
| GPN | $10,206.7m | -0.4% | 63.9% | n/a | $17,106.5m | $39,366.1m | 3.86x | n/a |
| PYPL | $34,128.0m | 4.3% | n/a | 20.2% | $-361.0m | $44,576.4m | 1.31x | 6.45x |
| XYZ | $25,042.0m | 0.3% | 46.4% | 6.2% | $455.2m | $44,595.9m | 1.78x | 28.79x |
| **Median** | $5,793.0m | 10.4% | 55.1% | 9.6% | $-197.8m | $11,434.6m | 2.03x | 18.93x |
| **Q1** | $1,510.2m | 3.3% | 41.4% | 6.6% | $-350.0m | $3,163.0m | 1.71x | 8.56x |
| **Q3** | $13,915.6m | 24.4% | 68.2% | 18.3% | $1,380.1m | $40,668.7m | 2.38x | 28.30x |
| n | 8 | 8 | 4 | 7 | 8 | 8 | 8 | 6 |

Medians and quartiles: numpy percentile (linear) over the comparables with a value. The LTM basis, balance-sheet
date, shares date, debt basis and a reason for every empty cell are in `outputs/comps/deal_a.md` and the CSV's
`notes` column.

**Empty cells and their reasons** (`outputs/comps/deal_a.csv` → `notes`):
- **Gross margin (4 of 8):** FLYW, PAYO, FOUR and PYPL tag neither gross profit nor cost of revenue.
- **EBITDA margin, GPN:** GPN's 10-Q has no H1 D&A fact, so its LTM D&A can't be formed.
- **EV/EBITDA, GPN:** missing for the same reason.
- **EV/EBITDA, BILL:** `n.m.`, because its LTM EBITDA is negative.

**Known limitations of the inputs** (`notes`, and the fact store):
- **Debt not tagged:** FLYW, PAYO and TOST have no debt fact at 2026-06-30 under the vocabulary's concepts, so their debt is left out.
- **PYPL debt:** taken from `LongTermDebtNoncurrent` only, because no current-portion concept is tagged under the vocabulary. Its net debt is probably understated.
- **Concept coverage:** debt is read only from the concepts `config/metrics_vocab.yaml` maps, so a filer's other debt lines are missed.

### Spot check (10 cells)

**Status: recorded.** The checklist is `outputs/comps/deal_a_spotcheck.md`, generated once by
`python -m src.comps.table --spotcheck` and filled in by Preksha. Her edits changed only the fill-in columns.
- **The 10 cells:** BILL EV/LTM revenue, BILL EV, FLYW LTM revenue, PAYO EBITDA margin, FOUR EV/LTM EBITDA, TOST gross margin, GPN revenue growth, PYPL EV, XYZ net debt and TOST EV/LTM revenue. Together they cover every metric and every comparable.
- **What each cell lists:** its value, calc_id and formula, plus every input with its fact_id, value, filing (accession), concept, period, dimensions and its EDGAR URL anchored to the iXBRL element (the price lists its source URL).

**Results** (counts from `outputs/comps/deal_a_spotcheck.md`):

| Check | Method | Result |
|---|---|---|
| Cell value | arithmetic recomputed independently by hand from the listed inputs | 10 of 10 match (`match (y/n): y` ×10, `n` ×0) |
| Inputs | each input checked by Preksha against its source (EDGAR iXBRL element; for prices, the snapshot's source) | 63 of 63 input rows `y`, 0 `n` (54 distinct inputs, of which 4 prices: BILL, FOUR, PYPL, TOST) |

No cell or input is marked `n`.

**Notes Preksha wrote:**
- **PYPL EV (cell 8):** "debt is noncurrent only (current portion not tagged: known gap, EV likely understated)". This is the PYPL current-debt limitation above. The cell matches its inputs, but EV leaves out the untagged current portion of debt.
- **TOST EV/LTM revenue (cell 10):** "no debt tagged for TOST; Class B at the Class A price".
- **BILL EV (cell 2):** short-term investments (AFS debt securities, current) are subtracted as cash-like.
- **FOUR EV/LTM EBITDA (cell 5):** NCI is added.
- **PAYO EBITDA margin (cell 4):** EBITDA = operating income + D&A, LTM.

BILL is comparable #1, but its EV/EBITDA is `n.m.`, so the checklist uses BILL EV/LTM revenue instead.

## 3. Figure–caption linkage (`data/figures_cs2/links.jsonl`)

**Path.** The brief names `data/figures/links.jsonl`, but `data/figures/` is the output directory of the CS1
`layout` stage. Writing into it makes `layout` show as changed, and CS1 stages stay unchanged. So the `figures`
stage writes to `data/figures_cs2/` (DECISIONS 2026-11-10).

**Rule** (`src/figures/links.py`, no model). For each Figure block, candidates are Title, Text or List blocks on
the same page with text, overlapping the figure horizontally, that are not running headers or footers. A running
line is one whose first 20 characters start a block on at least half of the document's pages.
1. **Above:** the nearest candidate whose top is above the figure's top and whose bottom is within 72 pt of it (a title overlapping the top edge has gap 0). Ties go to a Title, then the lower block_id.
2. **Else below:** the same rule, within 72 pt below the figure.
3. **Else none:** logged, never guessed.

**Nested boxes.** A Figure covering at least 80% of another on the same page is its container. Every Figure is
linked, but only a Figure inside no other is read in item 4. The detector boxed each deck chart twice, and the
inner box cuts off the chart's own title.

**Kestrel** (`data/figures_cs2/links.jsonl`, 5 lines): all 5 Figure blocks are captioned by the "above" rule, and 3 are chart-read targets.

| Document | Page | Figure | Caption | Gap (pt) | Read |
|---|---|---|---|---|---|
| DR-kestrel-001 (CIM) | 4 | p0004_b003 | "Net revenue by fiscal year (USD m)" (p0004_b002) | 0.0 | yes |
| DR-kestrel-006 (deck) | 3 | p0003_b002 | "Growth story" (p0003_b001) | 21.75 | yes (contains p0003_b003) |
| DR-kestrel-006 (deck) | 3 | p0003_b003 | "Growth story" (p0003_b001) | 64.83 | no (inside p0003_b002) |
| DR-kestrel-006 (deck) | 4 | p0004_b002 | "Net revenue" (p0004_b001) | 0.0 | yes (contains p0004_b003) |
| DR-kestrel-006 (deck) | 4 | p0004_b003 | "Net revenue" (p0004_b001) | 44.73 | no (inside p0004_b002) |

## 4. Chart reads and chart-vs-table reconciliation (`data/figures_cs2/chart_reads.jsonl`)

**How the charts are read.**
- **The call:** one structured call per target chart through `src/llm/client.py`, with the pinned vision model (`params.yaml` → `llm.models.vision`, `gpt-4.1-mini`) at temperature 0. The message holds the figure PNG (written by `ingest_dataroom`) and its caption.
- **Cache:** `data/figures_cache/llm`, tracked with DVC.
- **What the model returns:** the printed title, the vocabulary metric, the scale, and each category with its number as printed.
- **What code does with it:** converts each point to a claim the Part 1 way, with `method: chart_read`, the document's tier (4) and the Figure block's `pdf_bbox` citation. Code then reconciles each claim against Kestrel's tier-3 facts with Part 1's `reconcile_claim` (`reconcile.tolerance_rel` 0.01, `high_gap_rel` 0.05).

**Kestrel: 3 charts, 9 chart_read claims; 8 match the tables, 1 is a discrepancy.** Every value was read from a
printed data label.

| Chart (title as read) | Period | Chart | Table fact | Status | Gap | Severity |
|---|---|---|---|---|---|---|
| CIM p4 "Net revenue by fiscal year (USD m)" | FY2023 / FY2024 / FY2025 | 38.3 / 48.7 / 59.3 | F-e58099dd5e0f / F-19f22b5e3608 / F-e0f9d2abc4a3 | match ×3 | 0.0 | — |
| Deck slide 3 "Total payment volume by fiscal year (USD m)" | FY2023 / FY2024 / FY2025 | 3120.0 / 3980.0 / 4870.0 | F-d3848c6906b7 / F-851f6980e99b / F-d8aeaaabe08e | match ×3 | 0.0 | — |
| Deck slide 4 "Net revenue (USD m)" | FY2023 / FY2024 | 38.3 / 48.7 | F-e58099dd5e0f / F-19f22b5e3608 | match ×2 | 0.0 | — |
| **Deck slide 4 "Net revenue (USD m)"** | **FY2025** | **62.0** | **F-e0f9d2abc4a3 (59.3)** | **discrepancy** | **+0.0455** | **medium** |

**The chart-vs-table discrepancy.** The management deck's net revenue chart shows FY2025 at 62.0, while the
seller's P&L shows 59.3 (`C-0d34003d46a3` against `F-e0f9d2abc4a3`). The gap of 4.55% is under `high_gap_rel`, so
the severity is medium. The CIM's chart of the same metric agrees with the table. This matches the data room's T-CHART
trap (`data/dataroom/kestrel/answer_key.yaml` → `chart_traps` `k_chart_rev`: management_deck.pdf page 4, revenue
FY2025, chart 62.0 vs table 59.3, gap 0.045531); the key is the public practice key, read only for this check.

**Cost** (`logs/cost_ledger.jsonl`, stage `figures:chart_read`): 4 calls, $0.005005, 11,096 input and 354 output
tokens.
- **Superseded call:** one of the 4 ($0.001035) read the inner box of deck slide 3 during debugging. Its cache file (`f7b006f3…`) stays in `data/figures_cache/llm` unused.
- **Missing from the ledger:** one more call hit the 1,024-token output limit and is not in the ledger, because `client.py` logs only successful calls. The same request succeeded on retry.

Replay: with no API key and the network blocked, `dvc repro figures` rewrote both outputs byte-identical and
added no ledger rows.
