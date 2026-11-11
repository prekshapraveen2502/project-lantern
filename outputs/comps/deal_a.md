# Comparable companies: deal_a

Built for the variant's sector archetype from params comps.tickers; uses no data-room file. Prices: `data/reference/prices_asof_20260930.csv` (as of 2026-09-30). Every value is a calculator output; its calc_id is in `outputs/comps/deal_a.csv` and its trace in `outputs/comps/deal_a_calcs.jsonl`.

| Ticker | Company | LTM revenue | Revenue growth (FY) | Gross margin (LTM) | EBITDA margin (LTM) | Net debt | Enterprise value | EV / LTM revenue | EV / LTM EBITDA |
|---|---|---|---|---|---|---|---|---|---|
| BILL | BILL Holdings, Inc. | $1,653.2m | 13.0% | 80.9% | -1.1% | $-101.4m | $3,526.3m | 2.13x | n/a |
| FLYW | Flywire Corp | $713.5m | 26.6% | n/a | 9.6% | $-294.2m | $1,835.0m | 2.57x | 26.83x |
| PAYO | Payoneer Global Inc. | $1,081.4m | 7.7% | n/a | 17.4% | $-346.3m | $2,073.1m | 1.92x | 11.02x |
| FOUR | Shift4 Payments, Inc. | $4,782.0m | 25.5% | n/a | 19.3% | $4,155.0m | $7,128.0m | 1.49x | 7.74x |
| TOST | Toast, Inc. | $6,804.0m | 24.1% | 26.7% | 7.1% | $-1,015.0m | $15,741.2m | 2.31x | 32.66x |
| GPN | GLOBAL PAYMENTS INC | $10,206.7m | -0.4% | 63.9% | n/a | $17,106.5m | $39,366.1m | 3.86x | n/a |
| PYPL | PayPal Holdings, Inc. | $34,128.0m | 4.3% | n/a | 20.2% | $-361.0m | $44,576.4m | 1.31x | 6.45x |
| XYZ | Block, Inc. | $25,042.0m | 0.3% | 46.4% | 6.2% | $455.2m | $44,595.9m | 1.78x | 28.79x |
| **Median** | | $5,793.0m | 10.4% | 55.1% | 9.6% | $-197.8m | $11,434.6m | 2.03x | 18.93x |
| **Q1 (25th pct)** | | $1,510.2m | 3.3% | 41.4% | 6.6% | $-350.0m | $3,163.0m | 1.71x | 8.56x |
| **Q3 (75th pct)** | | $13,915.6m | 24.4% | 68.2% | 18.3% | $1,380.1m | $40,668.7m | 2.38x | 28.30x |
| n | | 8 | 8 | 4 | 7 | 8 | 8 | 8 | 6 |

LTM basis, balance-sheet date and notes per comparable:

| Ticker | LTM basis | Balance sheet | Shares date | Debt | Notes |
|---|---|---|---|---|---|
| BILL | FY2026 | 2026-06-30 | 2026-08-13 | total_debt | nci: not tagged at 2026-06-30; left out; ev_ebitda: n.m.: ev_ebitda denominator CALC-0a15fec80bff is -17547000.0 (not positive) |
| FLYW | FY2025 + H1 FY2026 - H1 FY2025 | 2026-06-30 | 2026-07-31 | none | gross_margin: neither gross_profit nor cost_of_revenue is tagged; debt: not tagged at 2026-06-30; left out; nci: not tagged at 2026-06-30; left out |
| PAYO | FY2025 + H1 FY2026 - H1 FY2025 | 2026-06-30 | 2026-07-31 | none | gross_margin: neither gross_profit nor cost_of_revenue is tagged; debt: not tagged at 2026-06-30; left out; short_term_investments: not tagged at 2026-06-30; left out; preferred: not tagged at 2026-06-30; left out; nci: not tagged at 2026-06-30; left out |
| FOUR | FY2025 + H1 FY2026 - H1 FY2025 | 2026-06-30 | 2026-07-31 | total_debt | gross_margin: neither gross_profit nor cost_of_revenue is tagged; short_term_investments: not tagged at 2026-06-30; left out; preferred: not tagged at 2026-06-30; left out |
| TOST | FY2025 + H1 FY2026 - H1 FY2025 | 2026-06-30 | 2026-07-30 | none | debt: not tagged at 2026-06-30; left out; short_term_investments: not tagged at 2026-06-30; left out; nci: not tagged at 2026-06-30; left out |
| GPN | FY2025 + H1 FY2026 - H1 FY2025 | 2026-06-30 | 2026-07-31 | total_debt | ebitda_margin: no H1 d_and_a for FY2026 and FY2025 (needed for LTM); short_term_investments: not tagged at 2026-06-30; left out; ev_ebitda: EV or LTM EBITDA missing |
| PYPL | FY2025 + H1 FY2026 - H1 FY2025 | 2026-06-30 | 2026-07-22 | debt_components | gross_margin: neither gross_profit nor cost_of_revenue is tagged; nci: not tagged at 2026-06-30; left out |
| XYZ | FY2025 + H1 FY2026 - H1 FY2025 | 2026-06-30 | 2026-07-31 | debt_components | short_term_investments: not tagged at 2026-06-30; left out |
