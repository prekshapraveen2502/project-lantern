# Spot check: deal_a comps, 10 cells (to fill in by hand)

For each cell: open every input's URL, check the value, filing and concept, recompute the cell from the formula, and fill in the last columns. Prices: check the close against a second public source.

## 1. BILL EV / LTM revenue

- value: `2.1330592963052575` (2.13x), calc_id `CALC-f770499839b0`, formula `ev / denominator`
- recomputed by hand: 2.1330592963 (2.13x)  match (y/n): y  notes: arithmetic recomputed independently from the listed inputs

| Input | Value | Filing | Concept | Period | Dims | URL | Checked (y/n) |
|---|---|---|---|---|---|---|---|
| PRICE-BILL-20260930 | 42.54 | data/reference/prices_asof_20260930.csv | close (snapshot) | 2026-09-30 |  | https://finance.yahoo.com/quote/BILL/history/ | y |
| F-b60781e5a57e | 85276782.0 | 0001628280-26-058238 | dei:EntityCommonStockSharesOutstanding | 2026-08-13 |  | https://www.sec.gov/Archives/edgar/data/1786352/000162828026058238/bill-20260630.htm#f-30 | y |
| F-3031077a7f0e | 1836123000.0 | 0001628280-26-058238 | us-gaap:LongTermDebt | 2026-06-30 |  | https://www.sec.gov/Archives/edgar/data/1786352/000162828026058238/bill-20260630.htm#f-1054 | y |
| F-6ed28ef816d1 | 0.0 | 0001628280-26-058238 | us-gaap:PreferredStockValue | 2026-06-30 |  | https://www.sec.gov/Archives/edgar/data/1786352/000162828026058238/bill-20260630.htm#f-120 | y |
| F-99387253f8a6 | 1031053000.0 | 0001628280-26-058238 | us-gaap:CashAndCashEquivalentsAtCarryingValue | 2026-06-30 |  | https://www.sec.gov/Archives/edgar/data/1786352/000162828026058238/bill-20260630.htm#f-54 | y |
| F-f433b93b979b | 906456000.0 | 0001628280-26-058238 | us-gaap:AvailableForSaleSecuritiesDebtSecuritiesCurrent | 2026-06-30 |  | https://www.sec.gov/Archives/edgar/data/1786352/000162828026058238/bill-20260630.htm#f-56 | y |
| F-9be7b085667e | 1653160000.0 | 0001628280-26-058238 | us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax | 2025-07-01..2026-06-30 |  | https://www.sec.gov/Archives/edgar/data/1786352/000162828026058238/bill-20260630.htm#f-148 | y |

## 2. BILL Enterprise value

- value: `3526288306.2799997` ($3,526.3m), calc_id `CALC-49e60cb2963e`, formula `price * sum(shares) + debt + preferred + nci - cash`
- recomputed by hand: 3,526,288,306.28 ($3,526.3m)  match (y/n): y  notes: arithmetic recomputed independently; STI (AFS debt securities, current) subtracted as cash-like

| Input | Value | Filing | Concept | Period | Dims | URL | Checked (y/n) |
|---|---|---|---|---|---|---|---|
| PRICE-BILL-20260930 | 42.54 | data/reference/prices_asof_20260930.csv | close (snapshot) | 2026-09-30 |  | https://finance.yahoo.com/quote/BILL/history/ | y |
| F-b60781e5a57e | 85276782.0 | 0001628280-26-058238 | dei:EntityCommonStockSharesOutstanding | 2026-08-13 |  | https://www.sec.gov/Archives/edgar/data/1786352/000162828026058238/bill-20260630.htm#f-30 | y |
| F-3031077a7f0e | 1836123000.0 | 0001628280-26-058238 | us-gaap:LongTermDebt | 2026-06-30 |  | https://www.sec.gov/Archives/edgar/data/1786352/000162828026058238/bill-20260630.htm#f-1054 | y |
| F-6ed28ef816d1 | 0.0 | 0001628280-26-058238 | us-gaap:PreferredStockValue | 2026-06-30 |  | https://www.sec.gov/Archives/edgar/data/1786352/000162828026058238/bill-20260630.htm#f-120 | y |
| F-99387253f8a6 | 1031053000.0 | 0001628280-26-058238 | us-gaap:CashAndCashEquivalentsAtCarryingValue | 2026-06-30 |  | https://www.sec.gov/Archives/edgar/data/1786352/000162828026058238/bill-20260630.htm#f-54 | y |
| F-f433b93b979b | 906456000.0 | 0001628280-26-058238 | us-gaap:AvailableForSaleSecuritiesDebtSecuritiesCurrent | 2026-06-30 |  | https://www.sec.gov/Archives/edgar/data/1786352/000162828026058238/bill-20260630.htm#f-56 | y |

## 3. FLYW LTM revenue

- value: `713538000.0` ($713.5m), calc_id `CALC-985d91c67516`, formula `fy + ytd - prior_ytd`
- recomputed by hand: 713,538,000 ($713.5m)  match (y/n): y  notes: arithmetic recomputed independently

| Input | Value | Filing | Concept | Period | Dims | URL | Checked (y/n) |
|---|---|---|---|---|---|---|---|
| F-52e8db95be96 | 623025000.0 | 0001193125-26-067540 | us-gaap:Revenues | 2025-01-01..2025-12-31 |  | https://www.sec.gov/Archives/edgar/data/1580560/000119312526067540/flyw-20251231.htm#F_72cbcf94-f8c4-4f60-9feb-4d2bbd8cc8ee | y |
| F-25662b2f1227 | 355856000.0 | 0001580560-26-000016 | us-gaap:Revenues | 2026-01-01..2026-06-30 |  | https://www.sec.gov/Archives/edgar/data/1580560/000158056026000016/flyw-20260630.htm#F_11c42ef5-bb36-48ee-b1cb-136503351cbd | y |
| F-26e61e49a66e | 265343000.0 | 0001580560-26-000016 | us-gaap:Revenues | 2025-01-01..2025-06-30 |  | https://www.sec.gov/Archives/edgar/data/1580560/000158056026000016/flyw-20260630.htm#F_4eb21f9d-a249-4211-abfa-efd7faf573d8 | y |

## 4. PAYO EBITDA margin (LTM)

- value: `0.17389282002152773` (17.4%), calc_id `CALC-4ad0940572d5`, formula `numerator / revenue`
- recomputed by hand: 0.1738928200 (17.4%)  match (y/n): y  notes: arithmetic recomputed independently; EBITDA = operating income + D&A, LTM

| Input | Value | Filing | Concept | Period | Dims | URL | Checked (y/n) |
|---|---|---|---|---|---|---|---|
| F-786e1241724e | 124667000.0 | 0001104659-26-020487 | us-gaap:OperatingIncomeLoss | 2025-01-01..2025-12-31 |  | https://www.sec.gov/Archives/edgar/data/1845815/000110465926020487/payo-20251231x10k.htm#Tc_Q9lOwvk9UUeYe5N6L5tdZg_14_3 | y |
| F-46f75ed32827 | 46954000.0 | 0001104659-26-091988 | us-gaap:OperatingIncomeLoss | 2026-01-01..2026-06-30 |  | https://www.sec.gov/Archives/edgar/data/1845815/000110465926091988/payo-20260630x10q.htm#Tc_dZQCxtrshUeBBQ4K2ndnzA_14_9 | y |
| F-ac8433cbde1e | 59396000.0 | 0001104659-26-091988 | us-gaap:OperatingIncomeLoss | 2025-01-01..2025-06-30 |  | https://www.sec.gov/Archives/edgar/data/1845815/000110465926091988/payo-20260630x10q.htm#Tc_OHdcyXXOoUa8NshFvwpI1A_14_12 | y |
| F-0da9033891b1 | 65625000.0 | 0001104659-26-020487 | us-gaap:DepreciationDepletionAndAmortization | 2025-01-01..2025-12-31 | | https://www.sec.gov/Archives/edgar/data/1845815/000110465926020487/payo-20251231x10k.htm#Tc_-Nt1ofSpaESe-MI5FggLMQ_11_3 | y |
| F-1ee5fdbbff4d | 40140000.0 | 0001104659-26-091988 | us-gaap:DepreciationDepletionAndAmortization | 2026-01-01..2026-06-30 | | https://www.sec.gov/Archives/edgar/data/1845815/000110465926091988/payo-20260630x10q.htm#Tc_ZNq6aooFAkO0Ido51aec_Q_11_9 | y |
| F-d47a501d9396 | 29943000.0 | 0001104659-26-091988 | us-gaap:DepreciationDepletionAndAmortization | 2025-01-01..2025-06-30 | | https://www.sec.gov/Archives/edgar/data/1845815/000110465926091988/payo-20260630x10q.htm#Tc_8Zt9dFyvykOsZwMNWiJDKQ_11_12 | y |
| F-172b9a61a1b6 | 1052774000.0 | 0001104659-26-020487 | us-gaap:Revenues | 2025-01-01..2025-12-31 |  | https://www.sec.gov/Archives/edgar/data/1845815/000110465926020487/payo-20251231x10k.htm#Tc_x7377oofs0GMtVoXdNfKOg_4_3 | y |
| F-80849a496eee | 535853000.0 | 0001104659-26-091988 | us-gaap:Revenues | 2026-01-01..2026-06-30 |  | https://www.sec.gov/Archives/edgar/data/1845815/000110465926091988/payo-20260630x10q.htm#Tc_0sKVknTN6Uy9XkDJPhK8Ag_4_9 | y |
| F-a5fb2be149bd | 507231000.0 | 0001104659-26-091988 | us-gaap:Revenues | 2025-01-01..2025-06-30 |  | https://www.sec.gov/Archives/edgar/data/1845815/000110465926091988/payo-20260630x10q.htm#Tc_lrgOcn-DIEytdBaOFZ5bDA_4_12 | y |

## 5. FOUR EV / LTM EBITDA

- value: `7.739400152442997` (7.74x), calc_id `CALC-4f343c0d7f3c`, formula `ev / denominator`
- recomputed by hand: 7.7394001524 (7.74x); EV 7,127,987,540.40, LTM EBITDA 921,000,000  match (y/n): y  notes: arithmetic recomputed independently; NCI added

| Input | Value | Filing | Concept | Period | Dims | URL | Checked (y/n) |
|---|---|---|---|---|---|---|---|
| PRICE-FOUR-20260930 | 36.17 | data/reference/prices_asof_20260930.csv | close (snapshot) | 2026-09-30 |  | https://finance.yahoo.com/quote/FOUR/history/ | y |
| F-0c6cf05025f7 | 78960120.0 | 0001794669-26-000045 | dei:EntityCommonStockSharesOutstanding | 2026-07-31 |  | https://www.sec.gov/Archives/edgar/data/1794669/000179466926000045/four-20260630.htm#f-27 | y |
| F-c8d33817b404 | 4511000000.0 | 0001794669-26-000045 | us-gaap:LongTermDebt | 2026-06-30 |  | https://www.sec.gov/Archives/edgar/data/1794669/000179466926000045/four-20260630.htm#f-655 | y |
| F-568b4d4bea52 | 117000000.0 | 0001794669-26-000045 | us-gaap:MinorityInterest | 2026-06-30 |  | https://www.sec.gov/Archives/edgar/data/1794669/000179466926000045/four-20260630.htm#f-139 | y |
| F-fa78cb413881 | 356000000.0 | 0001794669-26-000045 | us-gaap:CashAndCashEquivalentsAtCarryingValue | 2026-06-30 |  | https://www.sec.gov/Archives/edgar/data/1794669/000179466926000045/four-20260630.htm#f-553 | y |
| F-2177e674ad09 | 351000000.0 | 0001794669-26-000010 | us-gaap:OperatingIncomeLoss | 2025-01-01..2025-12-31 |  | https://www.sec.gov/Archives/edgar/data/1794669/000179466926000010/four-20251231.htm#f-202 | y |
| F-29e3db3abb4f | 145000000.0 | 0001794669-26-000045 | us-gaap:OperatingIncomeLoss | 2026-01-01..2026-06-30 |  | https://www.sec.gov/Archives/edgar/data/1794669/000179466926000045/four-20260630.htm#f-175 | y |
| F-9b89d316da3b | 108000000.0 | 0001794669-26-000045 | us-gaap:OperatingIncomeLoss | 2025-01-01..2025-06-30 |  | https://www.sec.gov/Archives/edgar/data/1794669/000179466926000045/four-20260630.htm#f-176 | y |
| F-a2cd62d6def5 | 432000000.0 | 0001794669-26-000010 | us-gaap:DepreciationDepletionAndAmortization | 2025-01-01..2025-12-31 |  | https://www.sec.gov/Archives/edgar/data/1794669/000179466926000010/four-20251231.htm#f-448 | y |
| F-5af5eec4bda8 | 274000000.0 | 0001794669-26-000045 | us-gaap:DepreciationDepletionAndAmortization | 2026-01-01..2026-06-30 |  | https://www.sec.gov/Archives/edgar/data/1794669/000179466926000045/four-20260630.htm#f-448 | y |
| F-78b0e1929f87 | 173000000.0 | 0001794669-26-000045 | us-gaap:DepreciationDepletionAndAmortization | 2025-01-01..2025-06-30 |  | https://www.sec.gov/Archives/edgar/data/1794669/000179466926000045/four-20260630.htm#f-449 | y |

## 6. TOST Gross margin (LTM)

- value: `0.2671957671957672` (26.7%), calc_id `CALC-e92ee5f961ba`, formula `numerator / revenue`
- recomputed by hand: 0.2671957672 (26.7%)  match (y/n): y  notes: arithmetic recomputed independently

| Input | Value | Filing | Concept | Period | Dims | URL | Checked (y/n) |
|---|---|---|---|---|---|---|---|
| F-65c36808ac49 | 1593000000.0 | 0001650164-26-000057 | us-gaap:GrossProfit | 2025-01-01..2025-12-31 |  | https://www.sec.gov/Archives/edgar/data/1650164/000165016426000057/tost-20251231.htm#f-163 | y |
| F-8207b625c35b | 963000000.0 | 0001650164-26-000164 | us-gaap:GrossProfit | 2026-01-01..2026-06-30 |  | https://www.sec.gov/Archives/edgar/data/1650164/000165016426000164/tost-20260630.htm#f-147 | y |
| F-1fbdd13492e9 | 738000000.0 | 0001650164-26-000164 | us-gaap:GrossProfit | 2025-01-01..2025-06-30 |  | https://www.sec.gov/Archives/edgar/data/1650164/000165016426000164/tost-20260630.htm#f-148 | y |
| F-6a1f86f46138 | 6153000000.0 | 0001650164-26-000057 | us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax | 2025-01-01..2025-12-31 |  | https://www.sec.gov/Archives/edgar/data/1650164/000165016426000057/tost-20251231.htm#f-145 | y |
| F-4e10088adc8f | 3538000000.0 | 0001650164-26-000164 | us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax | 2026-01-01..2026-06-30 |  | https://www.sec.gov/Archives/edgar/data/1650164/000165016426000164/tost-20260630.htm#f-123 | y |
| F-5a76d15282a9 | 2887000000.0 | 0001650164-26-000164 | us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax | 2025-01-01..2025-06-30 |  | https://www.sec.gov/Archives/edgar/data/1650164/000165016426000164/tost-20260630.htm#f-124 | y |

## 7. GPN Revenue growth (FY)

- value: `-0.003889880648451305` (-0.4%), calc_id `CALC-6fa0d54be4c3`, formula `a / b - 1`
- recomputed by hand: -0.0038898806 (-0.4%)  match (y/n): y  notes: arithmetic recomputed independently

| Input | Value | Filing | Concept | Period | Dims | URL | Checked (y/n) |
|---|---|---|---|---|---|---|---|
| F-86e23854e817 | 7705878000.0 | 0001123360-26-000008 | us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax | 2025-01-01..2025-12-31 |  | https://www.sec.gov/Archives/edgar/data/1123360/000112336026000008/gpn-20251231.htm#f-56 | y |
| F-0bdab25e390d | 7735970000.0 | 0001123360-26-000008 | us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax | 2024-01-01..2024-12-31 |  | https://www.sec.gov/Archives/edgar/data/1123360/000112336026000008/gpn-20251231.htm#f-57 | y |

## 8. PYPL Enterprise value

- value: `44576359711.22` ($44,576.4m), calc_id `CALC-c09b666bcaac`, formula `price * sum(shares) + debt + preferred + nci - cash`
- recomputed by hand: 44,576,359,711.22 ($44,576.4m)  match (y/n): y  notes: arithmetic recomputed independently; debt is noncurrent only (current portion not tagged: known gap, EV likely understated)

| Input | Value | Filing | Concept | Period | Dims | URL | Checked (y/n) |
|---|---|---|---|---|---|---|---|
| PRICE-PYPL-20260930 | 52.53 | data/reference/prices_asof_20260930.csv | close (snapshot) | 2026-09-30 |  | https://finance.yahoo.com/quote/PYPL/history/ | y |
| F-ae5ac87df992 | 855460874.0 | 0001633917-26-000082 | dei:EntityCommonStockSharesOutstanding | 2026-07-22 |  | https://www.sec.gov/Archives/edgar/data/1633917/000163391726000082/pypl-20260630.htm#f-24 | y |
| F-e684beb2fc94 | 10895000000.0 | 0001633917-26-000082 | us-gaap:LongTermDebtNoncurrent | 2026-06-30 |  | https://www.sec.gov/Archives/edgar/data/1633917/000163391726000082/pypl-20260630.htm#f-72 | y |
| F-329db0643c80 | 0.0 | 0001633917-26-000082 | us-gaap:PreferredStockValue | 2026-06-30 |  | https://www.sec.gov/Archives/edgar/data/1633917/000163391726000082/pypl-20260630.htm#f-90 | y |
| F-ed27ba463f17 | 8306000000.0 | 0001633917-26-000082 | us-gaap:CashAndCashEquivalentsAtCarryingValue | 2026-06-30 |  | https://www.sec.gov/Archives/edgar/data/1633917/000163391726000082/pypl-20260630.htm#f-30 | y |
| F-d8e29b082d6b | 2950000000.0 | 0001633917-26-000082 | us-gaap:ShortTermInvestments | 2026-06-30 |  | https://www.sec.gov/Archives/edgar/data/1633917/000163391726000082/pypl-20260630.htm#f-32 | y |

## 9. XYZ Net debt

- value: `455182000.0` ($455.2m), calc_id `CALC-f2f0d7b4e0fb`, formula `sum(debt) - sum(cash)`
- recomputed by hand: 455,182,000 ($455.2m)  match (y/n): y  notes: arithmetic recomputed independently

| Input | Value | Filing | Concept | Period | Dims | URL | Checked (y/n) |
|---|---|---|---|---|---|---|---|
| F-deef99600bab | 589556000.0 | 0001628280-26-053368 | us-gaap:LongTermLineOfCredit | 2026-06-30 |  | https://www.sec.gov/Archives/edgar/data/1512673/000162828026053368/xyz-20260630.htm#f-68 | y |
| F-7c8dd2117135 | 0.0 | 0001628280-26-053368 | us-gaap:OtherLongTermDebtCurrent | 2026-06-30 |  | https://www.sec.gov/Archives/edgar/data/1512673/000162828026053368/xyz-20260630.htm#f-62 | y |
| F-cc3618c129c9 | 5720569000.0 | 0001628280-26-053368 | us-gaap:OtherLongTermDebtNoncurrent | 2026-06-30 |  | https://www.sec.gov/Archives/edgar/data/1512673/000162828026053368/xyz-20260630.htm#f-70 | y |
| F-3abecd073dec | 572388000.0 | 0001628280-26-053368 | us-gaap:SecuredDebtCurrent | 2026-06-30 |  | https://www.sec.gov/Archives/edgar/data/1512673/000162828026053368/xyz-20260630.htm#f-64 | y |
| F-623bde785895 | 6427331000.0 | 0001628280-26-053368 | us-gaap:CashAndCashEquivalentsAtCarryingValue | 2026-06-30 |  | https://www.sec.gov/Archives/edgar/data/1512673/000162828026053368/xyz-20260630.htm#f-32 | y |

## 10. TOST EV / LTM revenue

- value: `2.3135243974132864` (2.31x), calc_id `CALC-e175eee6444e`, formula `ev / denominator`
- recomputed by hand: 2.3135243974 (2.31x)  match (y/n): y  notes: arithmetic recomputed independently; no debt tagged for TOST; Class B at the Class A price

| Input | Value | Filing | Concept | Period | Dims | URL | Checked (y/n) |
|---|---|---|---|---|---|---|---|
| PRICE-TOST-20260930 | 28.99 | data/reference/prices_asof_20260930.csv | close (snapshot) | 2026-09-30 |  | https://finance.yahoo.com/quote/TOST/history/ | y |
| F-d1136379e46e | 514000000.0 | 0001650164-26-000164 | dei:EntityCommonStockSharesOutstanding | 2026-07-30 | {"us-gaap:StatementClassOfStockAxis": "us-gaap:CommonClassAMember"} | https://www.sec.gov/Archives/edgar/data/1650164/000165016426000164/tost-20260630.htm#f-24 | y |
| F-6245742e130a | 64000000.0 | 0001650164-26-000164 | dei:EntityCommonStockSharesOutstanding | 2026-07-30 | {"us-gaap:StatementClassOfStockAxis": "us-gaap:CommonClassBMember"} | https://www.sec.gov/Archives/edgar/data/1650164/000165016426000164/tost-20260630.htm#f-25 | y |
| F-dce17bcbfadc | 0.0 | 0001650164-26-000164 | us-gaap:PreferredStockValue | 2026-06-30 |  | https://www.sec.gov/Archives/edgar/data/1650164/000165016426000164/tost-20260630.htm#f-79 | y |
| F-fad7fe3a1bab | 1015000000.0 | 0001650164-26-000164 | us-gaap:CashAndCashEquivalentsAtCarryingValue | 2026-06-30 |  | https://www.sec.gov/Archives/edgar/data/1650164/000165016426000164/tost-20260630.htm#f-31 | y |
| F-6a1f86f46138 | 6153000000.0 | 0001650164-26-000057 | us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax | 2025-01-01..2025-12-31 |  | https://www.sec.gov/Archives/edgar/data/1650164/000165016426000057/tost-20251231.htm#f-145 | y |
| F-4e10088adc8f | 3538000000.0 | 0001650164-26-000164 | us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax | 2026-01-01..2026-06-30 |  | https://www.sec.gov/Archives/edgar/data/1650164/000165016426000164/tost-20260630.htm#f-123 | y |
| F-5a76d15282a9 | 2887000000.0 | 0001650164-26-000164 | us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax | 2025-01-01..2025-06-30 |  | https://www.sec.gov/Archives/edgar/data/1650164/000165016426000164/tost-20260630.htm#f-124 | y |
