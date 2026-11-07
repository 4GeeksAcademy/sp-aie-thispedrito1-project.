# HealthCore — Monthly financial close, October 2026

_Version 1 · status: pending review · reviewer: Tom Callahan_

> Draft pending review. Not for distribution until approved by Tom Callahan.

## 1. Executive summary

- October 2026 close: consolidated result 318,284.19 USD against a comparable budget of 400,468.00 USD (-20.5%).
- Revenue 2,027,420.00 USD (-3.2%); expenses 1,709,135.81 USD (+0.8%).
- Relevant deviations at or above 5%: Medicare / Medicaid (US) -10.0%, Laboratory — ClearPath Labs (US) +7.0%.
- Missing data: Clinical software licences (UK) (no records; not treated as zero).
- 1 doubtful record(s) excluded from totals; 1 flagged to Compliance for possible patient data.

## 2. Consolidated result (USD)

| | Budget | Comparable budget | Actual | Variance | Variance % |
|---|---:|---:|---:|---:|---:|
| Revenue | 2,095,450.00 | 2,095,450.00 | 2,027,420.00 | -68,030.00 | -3.2% |
| Expenses * | 1,705,142.00 | 1,694,982.00 | 1,709,135.81 | 14,153.81 | +0.8% |
| Result * | 390,308.00 | 400,468.00 | 318,284.19 | -82,183.81 | -20.5% |

\* Comparable budget excludes budgeted lines with no records (Clinical software licences (UK)); those lines are declared in section 6 and are not treated as zero. Converted at 1 GBP = 1.27 USD.

## 3. Revenue by country and payer (local currency)

**United States (USD)**

| Line | Budget | Actual | Variance | Variance % |
|---|---:|---:|---:|---:|
| Commercial insurance (US) | 1,150,000.00 | 1,120,000.00 | -30,000.00 | -2.6% |
| Medicare / Medicaid (US) | 520,000.00 | 468,000.00 | -52,000.00 | -10.0% |

**United Kingdom (GBP)**

| Line | Budget | Actual | Variance | Variance % |
|---|---:|---:|---:|---:|
| Private (UK) | 260,000.00 | 271,000.00 | 11,000.00 | +4.2% |
| NHS contract (UK) | 75,000.00 | 75,000.00 | 0.00 | 0.0% |

## 4. Expenses by country (local currency)

**United States (USD)**

| Line | Budget | Actual | Variance | Variance % |
|---|---:|---:|---:|---:|
| Clinical supplies — MedSupply Partners (US) | 44,800.00 | 46,816.00 | 2,016.00 | +4.5% |
| Laboratory — ClearPath Labs (US) | 45,000.00 | 48,150.00 | 3,150.00 | +7.0% |
| Medical interpreting — LinguaCare Interpreting (US) | 17,550.00 | 17,550.00 | 0.00 | 0.0% |
| Payroll (US) | 1,050,000.00 | 1,060,000.00 | 10,000.00 | +1.0% |
| Clinic rent (US) | 180,000.00 | 180,000.00 | 0.00 | 0.0% |

**United Kingdom (GBP)**

| Line | Budget | Actual | Variance | Variance % |
|---|---:|---:|---:|---:|
| Clinical supplies — BritMed Supplies (UK) | 9,450.00 | 9,450.00 | 0.00 | 0.0% |
| Laboratory — Thames Pathology (UK) | 10,150.00 | 10,353.00 | 203.00 | +2.0% |
| Payroll (UK) | 210,000.00 | 209,000.00 | -1,000.00 | -0.5% |
| Clinic rent (UK) | 52,000.00 | 52,000.00 | 0.00 | 0.0% |
| Clinical software licences (UK) | 8,000.00 | No records | — | — |

## 5. Relevant deviations (≥ 5%)

_Hypotheses are explanations suggested by the data, not established causes._

### Medicare / Medicaid (US) — -10.0%

Hypothesis: Medicare / Medicaid (US) revenue deviated -10.0% from budget (468,000.00 vs 520,000.00 USD). Claims submitted were 4,000 against 4,000 budgeted (0.0%), but the denial rate moved from 14.0% to 22.6% (8.6 pp), so paid claims went from 3,440 to 3,096 (-10.0%). The deviation is consistent with a higher denial rate rather than fewer claims.

Supporting figures:
- `driver:US.medicare_medicaid.patient_services:claims_change_pct` (Change in claims submitted): 0.0% — calculation
- `driver:US.medicare_medicaid.patient_services:claims_submitted` (Claims submitted): 4,000 — operations.csv row 2
- `driver:US.medicare_medicaid.patient_services:claims_submitted_budget` (Claims submitted (budget)): 4,000 — operations.csv row 3
- `driver:US.medicare_medicaid.patient_services:denial_rate_change_pp` (Change in denial rate): 8.6 pp — calculation
- `driver:US.medicare_medicaid.patient_services:denial_rate_pct` (Denial rate (%)): 22.6% — operations.csv row 4
- `driver:US.medicare_medicaid.patient_services:denial_rate_pct_budget` (Denial rate (%) (budget)): 14.0% — operations.csv row 5
- `driver:US.medicare_medicaid.patient_services:paid_claims` (Paid claims): 3,096 — calculation
- `driver:US.medicare_medicaid.patient_services:paid_claims_budget` (Paid claims (budget)): 3,440 — calculation
- `driver:US.medicare_medicaid.patient_services:paid_claims_change_pct` (Change in paid claims): -10.0% — calculation
- `line:US.medicare_medicaid.patient_services:budget` (Budget): 520,000.00 USD — budget.csv row 3
- `line:US.medicare_medicaid.patient_services:actual` (Actual): 468,000.00 USD — `JE-2026OCT-0010`, `JE-2026OCT-0011`, `JE-2026OCT-0012`, `JE-2026OCT-0013`, `JE-2026OCT-0014`, `JE-2026OCT-0015`, `JE-2026OCT-0016`, `JE-2026OCT-0017`, `JE-2026OCT-0018`
- `line:US.medicare_medicaid.patient_services:variance_pct` (Variance): -10.0% — calculation

### Laboratory — ClearPath Labs (US) — +7.0%

Hypothesis: Laboratory — ClearPath Labs (US) deviated +7.0% from budget (48,150.00 vs 45,000.00 USD). Volume moved from 1,500 to 1,500 tests (0.0%), while the unit price moved from 30.00 to 32.10 USD (+7.0%). The deviation is consistent with a price change rather than volume.

Supporting figures:
- `driver:US.n/a.laboratory:unit_price` (Unit price): 32.10 USD — `JE-2026OCT-0027`, `JE-2026OCT-0028`, `JE-2026OCT-0029`
- `driver:US.n/a.laboratory:unit_price_budget` (Unit price (budget)): 30.00 USD — calculation
- `driver:US.n/a.laboratory:unit_price_change_pct` (Change in unit price): +7.0% — calculation
- `driver:US.n/a.laboratory:units` (Units): 1,500 — `JE-2026OCT-0027`, `JE-2026OCT-0028`, `JE-2026OCT-0029`
- `driver:US.n/a.laboratory:units_budget` (Units (budget)): 1,500 — operations.csv row 13
- `driver:US.n/a.laboratory:units_change_pct` (Change in units): 0.0% — calculation
- `line:US.n/a.laboratory:budget` (Budget): 45,000.00 USD — budget.csv row 7
- `line:US.n/a.laboratory:actual` (Actual): 48,150.00 USD — `JE-2026OCT-0027`, `JE-2026OCT-0028`, `JE-2026OCT-0029`
- `line:US.n/a.laboratory:variance_pct` (Variance): +7.0% — calculation

## 6. Missing or doubtful data

**Missing data**

- Clinical software licences (UK): budget 8,000.00 GBP, **no records** in ledger.csv. Declared as missing; not treated as 0 nor as a −100% deviation.

**Doubtful records (excluded from totals)**

- `JE-2026OCT-0053` (ledger.csv row 54): description may identify a patient (withheld; Compliance notified).

## 7. Traceability annex

| Figure | Value | Sources |
|---|---:|---|
| `consolidated:expense:actual` | 1,709,135.81 USD | `JE-2026OCT-0024`, `JE-2026OCT-0025`, `JE-2026OCT-0026`, `JE-2026OCT-0027`, `JE-2026OCT-0028`, `JE-2026OCT-0029`, `JE-2026OCT-0030`, `JE-2026OCT-0031`, `JE-2026OCT-0032`, `JE-2026OCT-0033`, `JE-2026OCT-0034`, `JE-2026OCT-0035`, `JE-2026OCT-0036`, `JE-2026OCT-0037`, `JE-2026OCT-0038`, `JE-2026OCT-0039`, `JE-2026OCT-0040`, `JE-2026OCT-0041`, `JE-2026OCT-0042`, `JE-2026OCT-0043`, `JE-2026OCT-0044`, `JE-2026OCT-0045`, `JE-2026OCT-0046`, `JE-2026OCT-0047`, `JE-2026OCT-0048`, `JE-2026OCT-0049`, `JE-2026OCT-0050`, `JE-2026OCT-0051`, `JE-2026OCT-0052`; exchange rate (1 GBP = 1.27 USD) |
| `consolidated:expense:budget` | 1,705,142.00 USD | calculation; exchange rate (1 GBP = 1.27 USD) |
| `consolidated:expense:comparable_budget` | 1,694,982.00 USD | calculation; exchange rate (1 GBP = 1.27 USD) |
| `consolidated:expense:variance_pct` | +0.8% | calculation |
| `consolidated:result:actual` | 318,284.19 USD | `JE-2026OCT-0001`, `JE-2026OCT-0002`, `JE-2026OCT-0003`, `JE-2026OCT-0004`, `JE-2026OCT-0005`, `JE-2026OCT-0006`, `JE-2026OCT-0007`, `JE-2026OCT-0008`, `JE-2026OCT-0009`, `JE-2026OCT-0010`, `JE-2026OCT-0011`, `JE-2026OCT-0012`, `JE-2026OCT-0013`, `JE-2026OCT-0014`, `JE-2026OCT-0015`, `JE-2026OCT-0016`, `JE-2026OCT-0017`, `JE-2026OCT-0018`, `JE-2026OCT-0019`, `JE-2026OCT-0020`, `JE-2026OCT-0021`, `JE-2026OCT-0022`, `JE-2026OCT-0023`, `JE-2026OCT-0024`, `JE-2026OCT-0025`, `JE-2026OCT-0026`, `JE-2026OCT-0027`, `JE-2026OCT-0028`, `JE-2026OCT-0029`, `JE-2026OCT-0030`, `JE-2026OCT-0031`, `JE-2026OCT-0032`, `JE-2026OCT-0033`, `JE-2026OCT-0034`, `JE-2026OCT-0035`, `JE-2026OCT-0036`, `JE-2026OCT-0037`, `JE-2026OCT-0038`, `JE-2026OCT-0039`, `JE-2026OCT-0040`, `JE-2026OCT-0041`, `JE-2026OCT-0042`, `JE-2026OCT-0043`, `JE-2026OCT-0044`, `JE-2026OCT-0045`, `JE-2026OCT-0046`, `JE-2026OCT-0047`, `JE-2026OCT-0048`, `JE-2026OCT-0049`, `JE-2026OCT-0050`, `JE-2026OCT-0051`, `JE-2026OCT-0052`; exchange rate (1 GBP = 1.27 USD) |
| `consolidated:result:budget` | 390,308.00 USD | calculation; exchange rate (1 GBP = 1.27 USD) |
| `consolidated:result:comparable_budget` | 400,468.00 USD | calculation; exchange rate (1 GBP = 1.27 USD) |
| `consolidated:result:variance_pct` | -20.5% | calculation |
| `consolidated:revenue:actual` | 2,027,420.00 USD | `JE-2026OCT-0001`, `JE-2026OCT-0002`, `JE-2026OCT-0003`, `JE-2026OCT-0004`, `JE-2026OCT-0005`, `JE-2026OCT-0006`, `JE-2026OCT-0007`, `JE-2026OCT-0008`, `JE-2026OCT-0009`, `JE-2026OCT-0010`, `JE-2026OCT-0011`, `JE-2026OCT-0012`, `JE-2026OCT-0013`, `JE-2026OCT-0014`, `JE-2026OCT-0015`, `JE-2026OCT-0016`, `JE-2026OCT-0017`, `JE-2026OCT-0018`, `JE-2026OCT-0019`, `JE-2026OCT-0020`, `JE-2026OCT-0021`, `JE-2026OCT-0022`, `JE-2026OCT-0023`; exchange rate (1 GBP = 1.27 USD) |
| `consolidated:revenue:budget` | 2,095,450.00 USD | calculation; exchange rate (1 GBP = 1.27 USD) |
| `consolidated:revenue:comparable_budget` | 2,095,450.00 USD | calculation; exchange rate (1 GBP = 1.27 USD) |
| `consolidated:revenue:variance_pct` | -3.2% | calculation |
| `driver:US.medicare_medicaid.patient_services:claims_change_pct` | 0.0% | calculation |
| `driver:US.medicare_medicaid.patient_services:claims_submitted` | 4,000 | operations.csv row 2 |
| `driver:US.medicare_medicaid.patient_services:claims_submitted_budget` | 4,000 | operations.csv row 3 |
| `driver:US.medicare_medicaid.patient_services:denial_rate_change_pp` | 8.6 pp | calculation |
| `driver:US.medicare_medicaid.patient_services:denial_rate_pct` | 22.6% | operations.csv row 4 |
| `driver:US.medicare_medicaid.patient_services:denial_rate_pct_budget` | 14.0% | operations.csv row 5 |
| `driver:US.medicare_medicaid.patient_services:paid_claims` | 3,096 | calculation |
| `driver:US.medicare_medicaid.patient_services:paid_claims_budget` | 3,440 | calculation |
| `driver:US.medicare_medicaid.patient_services:paid_claims_change_pct` | -10.0% | calculation |
| `driver:US.n/a.laboratory:unit_price` | 32.10 USD | `JE-2026OCT-0027`, `JE-2026OCT-0028`, `JE-2026OCT-0029` |
| `driver:US.n/a.laboratory:unit_price_budget` | 30.00 USD | calculation |
| `driver:US.n/a.laboratory:unit_price_change_pct` | +7.0% | calculation |
| `driver:US.n/a.laboratory:units` | 1,500 | `JE-2026OCT-0027`, `JE-2026OCT-0028`, `JE-2026OCT-0029` |
| `driver:US.n/a.laboratory:units_budget` | 1,500 | operations.csv row 13 |
| `driver:US.n/a.laboratory:units_change_pct` | 0.0% | calculation |
| `line:UK.n/a.clinic_rent:actual` | 52,000.00 GBP | `JE-2026OCT-0050`, `JE-2026OCT-0051`, `JE-2026OCT-0052` |
| `line:UK.n/a.clinic_rent:budget` | 52,000.00 GBP | budget.csv row 14 |
| `line:UK.n/a.clinic_rent:variance_pct` | 0.0% | calculation |
| `line:UK.n/a.clinical_software_licences:actual` | No records | — |
| `line:UK.n/a.clinical_software_licences:budget` | 8,000.00 GBP | budget.csv row 15 |
| `line:UK.n/a.clinical_supplies:actual` | 9,450.00 GBP | `JE-2026OCT-0043`, `JE-2026OCT-0044`, `JE-2026OCT-0045` |
| `line:UK.n/a.clinical_supplies:budget` | 9,450.00 GBP | budget.csv row 11 |
| `line:UK.n/a.clinical_supplies:variance_pct` | 0.0% | calculation |
| `line:UK.n/a.laboratory:actual` | 10,353.00 GBP | `JE-2026OCT-0046`, `JE-2026OCT-0047` |
| `line:UK.n/a.laboratory:budget` | 10,150.00 GBP | budget.csv row 12 |
| `line:UK.n/a.laboratory:variance_pct` | +2.0% | calculation |
| `line:UK.n/a.payroll:actual` | 209,000.00 GBP | `JE-2026OCT-0048`, `JE-2026OCT-0049` |
| `line:UK.n/a.payroll:budget` | 210,000.00 GBP | budget.csv row 13 |
| `line:UK.n/a.payroll:variance_pct` | -0.5% | calculation |
| `line:UK.nhs_contract.patient_services:actual` | 75,000.00 GBP | `JE-2026OCT-0022`, `JE-2026OCT-0023` |
| `line:UK.nhs_contract.patient_services:budget` | 75,000.00 GBP | budget.csv row 5 |
| `line:UK.nhs_contract.patient_services:variance_pct` | 0.0% | calculation |
| `line:UK.private.patient_services:actual` | 271,000.00 GBP | `JE-2026OCT-0019`, `JE-2026OCT-0020`, `JE-2026OCT-0021` |
| `line:UK.private.patient_services:budget` | 260,000.00 GBP | budget.csv row 4 |
| `line:UK.private.patient_services:variance_pct` | +4.2% | calculation |
| `line:US.commercial_insurance.patient_services:actual` | 1,120,000.00 USD | `JE-2026OCT-0001`, `JE-2026OCT-0002`, `JE-2026OCT-0003`, `JE-2026OCT-0004`, `JE-2026OCT-0005`, `JE-2026OCT-0006`, `JE-2026OCT-0007`, `JE-2026OCT-0008`, `JE-2026OCT-0009` |
| `line:US.commercial_insurance.patient_services:budget` | 1,150,000.00 USD | budget.csv row 2 |
| `line:US.commercial_insurance.patient_services:variance_pct` | -2.6% | calculation |
| `line:US.medicare_medicaid.patient_services:actual` | 468,000.00 USD | `JE-2026OCT-0010`, `JE-2026OCT-0011`, `JE-2026OCT-0012`, `JE-2026OCT-0013`, `JE-2026OCT-0014`, `JE-2026OCT-0015`, `JE-2026OCT-0016`, `JE-2026OCT-0017`, `JE-2026OCT-0018` |
| `line:US.medicare_medicaid.patient_services:budget` | 520,000.00 USD | budget.csv row 3 |
| `line:US.medicare_medicaid.patient_services:variance_pct` | -10.0% | calculation |
| `line:US.n/a.clinic_rent:actual` | 180,000.00 USD | `JE-2026OCT-0034`, `JE-2026OCT-0035`, `JE-2026OCT-0036`, `JE-2026OCT-0037`, `JE-2026OCT-0038`, `JE-2026OCT-0039`, `JE-2026OCT-0040`, `JE-2026OCT-0041`, `JE-2026OCT-0042` |
| `line:US.n/a.clinic_rent:budget` | 180,000.00 USD | budget.csv row 10 |
| `line:US.n/a.clinic_rent:variance_pct` | 0.0% | calculation |
| `line:US.n/a.clinical_supplies:actual` | 46,816.00 USD | `JE-2026OCT-0024`, `JE-2026OCT-0025`, `JE-2026OCT-0026` |
| `line:US.n/a.clinical_supplies:budget` | 44,800.00 USD | budget.csv row 6 |
| `line:US.n/a.clinical_supplies:variance_pct` | +4.5% | calculation |
| `line:US.n/a.laboratory:actual` | 48,150.00 USD | `JE-2026OCT-0027`, `JE-2026OCT-0028`, `JE-2026OCT-0029` |
| `line:US.n/a.laboratory:budget` | 45,000.00 USD | budget.csv row 7 |
| `line:US.n/a.laboratory:variance_pct` | +7.0% | calculation |
| `line:US.n/a.medical_interpreting:actual` | 17,550.00 USD | `JE-2026OCT-0030`, `JE-2026OCT-0031` |
| `line:US.n/a.medical_interpreting:budget` | 17,550.00 USD | budget.csv row 8 |
| `line:US.n/a.medical_interpreting:variance_pct` | 0.0% | calculation |
| `line:US.n/a.payroll:actual` | 1,060,000.00 USD | `JE-2026OCT-0032`, `JE-2026OCT-0033` |
| `line:US.n/a.payroll:budget` | 1,050,000.00 USD | budget.csv row 9 |
| `line:US.n/a.payroll:variance_pct` | +1.0% | calculation |
