# Comparable company selection

Brief §4: choose 6–10 US public comparables, all Inline-XBRL filers in the deal's sector archetype, justified by
SIC, size, business model and geography, with the list and date window pinned in `params.yaml`.

## The set

Pinned in `params.yaml`:

- `comps.tickers: [BILL, FLYW, PAYO, FOUR, TOST, GPN, PYPL, XYZ]` (8 comparables; BILL is comparable #1)
- `comps.forms: [10-K, 10-Q]`, `comps.after: "2024-07-01"`, `comps.before: "2026-09-30"`

The archetype comes from `data/dataroom/variant.json`: `archetype: b2b_payments_saas`.

The window ends at the variant's price snapshot date (`data/dataroom/variant.json`: `"price_snapshot_date":
"2026-09-30"`); `ingest_comps` keeps the newest filing with `after <= filing date < before`
(`src/ingest/edgar.py`, `latest_filing`). It starts early enough that each comparable's latest 10-K falls inside it:
those 10-Ks were filed between 2026-02-03 (PYPL) and 2026-08-20 (BILL, whose fiscal year ends June 30).

**Deviation from the brief.** The brief says the set includes the Case Study 1 company as comparable #1, because the
instructor variant's archetype is chosen from that company's SIC code. This project's data room is synthetic
(variant `pl-fintech-a`), and its archetype is fintech, so the CS1 company (AAPL) is not a comparable here. Tier-1
citations for these comparables therefore use `ixbrl_element` / `ixbrl_context`, which the brief's FAQ allows for a
comparable that was never rendered to PDF. A planned second synthetic variant (`pl-hardware-b`) will include AAPL
as comparable #1, to show `pdf_bbox` citations. BILL is comparable #1 here because its business model is the closest to the
archetype (see below).

## Facts per comparable

Sources: SIC, business address, fiscal year end and filer category from `data/edgar_comps/{T}/submissions.json`;
filing dates and accession numbers from `data/edgar_comps/{T}/{form}/{accn}/filing.json`; XBRL fact counts are the
line counts of `data/edgar_comps/{T}/{form}/{accn}/xbrl_facts.jsonl`; public float is `dei:EntityPublicFloat` (USD)
in `data/edgar_comps/{T}/companyfacts.json`, taking the value tagged with the ingested 10-K's accession number.

| # | Ticker | Company | SIC | SIC description | Business address | FYE (MMDD) | Filer category |
|---|---|---|---|---|---|---|---|
| 1 | BILL | BILL Holdings, Inc. | 7372 | Services-Prepackaged Software | San Jose, CA | 0630 | Large accelerated filer |
| 2 | FLYW | Flywire Corp | 7389 | Services-Business Services, NEC | Boston, MA | 1231 | Large accelerated filer |
| 3 | PAYO | Payoneer Global Inc. | 7389 | Services-Business Services, NEC | New York, NY | 1231 | Large accelerated filer |
| 4 | FOUR | Shift4 Payments, Inc. | 7389 | Services-Business Services, NEC | Center Valley, PA | 1231 | Large accelerated filer |
| 5 | TOST | Toast, Inc. | 7374 | Services-Computer Processing & Data Preparation | Boston, MA | 1231 | Large accelerated filer |
| 6 | GPN | GLOBAL PAYMENTS INC | 7389 | Services-Business Services, NEC | Atlanta, GA | 1231 | Large accelerated filer |
| 7 | PYPL | PayPal Holdings, Inc. | 7389 | Services-Business Services, NEC | San Jose, CA | 1231 | Large accelerated filer |
| 8 | XYZ | Block, Inc. | 7372 | Services-Prepackaged Software | Oakland, CA | 1231 | Large accelerated filer |

| # | Ticker | 10-K filed | 10-K accession | 10-Q filed | 10-Q accession | XBRL facts (10-K / 10-Q) | Public float (USD) | as of |
|---|---|---|---|---|---|---|---|---|
| 1 | BILL | 2026-08-20 | 0001628280-26-058238 | 2026-05-08 | 0001628280-26-032387 | 1275 / 1036 | 4,800,000,000 | 2025-12-31 |
| 2 | FLYW | 2026-02-24 | 0001193125-26-067540 | 2026-08-05 | 0001580560-26-000016 | 1466 / 952 | 1,351,794,027 | 2025-06-30 |
| 3 | PAYO | 2026-02-26 | 0001104659-26-020487 | 2026-08-06 | 0001104659-26-091988 | 1207 / 944 | 2,019,068,359 | 2025-06-30 |
| 4 | FOUR | 2026-02-27 | 0001794669-26-000010 | 2026-08-06 | 0001794669-26-000045 | 1430 / 983 | 6,400,000,000 | 2025-06-30 |
| 5 | TOST | 2026-02-18 | 0001650164-26-000057 | 2026-08-05 | 0001650164-26-000164 | 914 / 623 | 23,000,000,000 | 2025-06-30 |
| 6 | GPN | 2026-02-20 | 0001123360-26-000008 | 2026-08-05 | 0001123360-26-000084 | 1732 / 1226 | 19,310,238,934 | 2025-06-30 |
| 7 | PYPL | 2026-02-03 | 0001633917-26-000024 | 2026-07-28 | 0001633917-26-000082 | 2127 / 1881 | 71,300,000,000 | 2025-06-30 |
| 8 | XYZ | 2026-02-26 | 0001628280-26-012254 | 2026-08-05 | 0001628280-26-053368 | 1632 / 1407 | 39,400,000,000 | 2025-06-30 |

**Inline XBRL.** Every filing above is Inline XBRL: `ingest_comps` reads the facts with Arelle from the filing's
primary document only after checking it contains `ix:header` (`src/ingest/comps.py`, `primary_ixbrl`).

## Justification

**SIC.** The eight comparables carry three different SIC codes: 7372 (BILL, XYZ), 7389 (FLYW, PAYO, FOUR, GPN, PYPL)
and 7374 (TOST). Fintech filers are classified inconsistently, so SIC alone cannot define the set; the selection is
made by business model, and SIC is reported for transparency.

**Business model.** Each comparable earns revenue from payments, software, or both, as the archetype
(`b2b_payments_saas`) does. Word for word from each 10-K's Item 1
(`data/edgar_comps/{T}/10-K/{accn}/items/item_1.md`):

- **BILL:** "one integrated platform that includes accounts payable (AP), accounts receivable (AR), expenses,
  forecasting, procurement, and more". B2B payables and receivables software with payments: the closest model to
  the archetype, hence comparable #1.
- **FLYW:** "a leading global payments enablement and software company", combining "tailored invoicing, flexible
  payment options".
- **PAYO:** "Global SMBs use their Payoneer Account to manage their end-to-end AR and AP payment flows."
- **FOUR:** "a leading independent provider of software and payment processing solutions in the U.S."
- **TOST:** "a comprehensive platform of software-as-a-service, or SaaS, products and financial technology
  solutions, including integrated payment processing".
- **GPN:** "a leading payments technology company delivering innovative software and services to our customers
  globally".
- **PYPL:** "Our products are designed to enable digital payments and simplify commerce experiences for consumers and
  merchants". A large-cap payments benchmark.
- **XYZ:** "Our platform integrates payments, banking, lending, and commerce solutions designed to provide secure,
  reliable, and scalable financial infrastructure." Of its reportable revenue categories: "Commerce Enablement
  empowers sellers and consumers to transact across our ecosystems. It primarily includes Square payments, software,
  and hardware, Cash App Card, Cash App Pay, our BNPL products, and Cash App Business." A large-cap payments and
  software benchmark.

**Size.** Public float ranges from 1,351,794,027 USD (FLYW) to 71,300,000,000 USD (PYPL); see notes 1, 2 and 4.

**Geography.** All eight have a US business address in `submissions.json`.

## Candidates left out

- **AFRM:** a lender; credit risk drives its valuation, which a payments and software target does not share.
- **RELY:** consumer remittances, not business payments.
- **FI:** not in EDGAR's ticker list when the candidates were checked (2026-11-03).
- **MQ** (optional candidate): card-issuing infrastructure for fintechs, further from B2B invoicing and payments.
- **FIS** (optional candidate): banking and merchant technology, the weakest model fit; PYPL and GPN already cover
  large caps.

None of these was ingested, so no figures are given for them.

## Notes

1. **Public-float dates differ.** BILL's is as of 2025-12-31; the others are as of 2025-06-30. BILL's 10-K cover
   page gives the reason: the float is "based on the closing price of the shares of common stock on December 31,
   2025 (the last business day of the Registrant’s most recently completed second fiscal quarter)"
   (`data/edgar_comps/BILL/10-K/0001628280-26-058238/unpacked/bill-20260630.htm`). BILL's fiscal year ends June 30
   (FYE 0630), so its date is correct but not the same date as the others. The figures show relative size, not a
   same-day comparison.
2. **Several floats are round numbers** (BILL, FOUR, TOST, PYPL, XYZ). They are cited exactly as the filers tagged
   them on the 10-K cover page; BILL's cover page, for example, says "approximately $ 4.8 billion" (same file).
3. **State of incorporation is not cited.** `stateOfIncorporation` is empty in `submissions.json` for BILL, FOUR,
   TOST and PYPL, so geography uses the business address only.
4. **The target's size is not compared yet.** No Deal A financial figure exists in the repo yet, so this report shows
   the spread of the comparables, not their distance from the target.

Revenue-based size is added in Part 1, once `config/metrics_vocab.yaml` maps revenue to XBRL concepts.
