# HealthCore — Cierre financiero mensual, octubre de 2026

_Versión 1 · estado: pendiente de revisión · revisor: Tom Callahan_

> Borrador pendiente de revisión. No se distribuye hasta que lo apruebe Tom Callahan.

## 1. Resumen ejecutivo

- Cierre de octubre de 2026: resultado consolidado de 318.284,19 USD frente a un presupuesto comparable de 400.468,00 USD (-20,5 %).
- Ingresos 2.027.420,00 USD (-3,2 %); gastos 1.709.135,81 USD (+0,8 %).
- Desviaciones relevantes iguales o superiores al 5 %: Medicare / Medicaid (US) -10,0 %, Laboratorio — ClearPath Labs (US) +7,0 %.
- Datos faltantes: Licencias de software clínico (UK) (sin registros; no se tratan como cero).
- 1 registro(s) dudoso(s) excluido(s) de los totales; 1 comunicado(s) a Cumplimiento por posibles datos de pacientes.

## 2. Resultado consolidado (USD)

| | Presupuesto | Presupuesto comparable | Real | Desviación | Desviación % |
|---|---:|---:|---:|---:|---:|
| Ingresos | 2.095.450,00 | 2.095.450,00 | 2.027.420,00 | -68.030,00 | -3,2 % |
| Gastos * | 1.705.142,00 | 1.694.982,00 | 1.709.135,81 | 14.153,81 | +0,8 % |
| Resultado * | 390.308,00 | 400.468,00 | 318.284,19 | -82.183,81 | -20,5 % |

\* El presupuesto comparable excluye las partidas presupuestadas sin registros (Licencias de software clínico (UK)); se declaran en la sección 6 y no se tratan como cero. Conversión: 1 GBP = 1,27 USD.

## 3. Ingresos por país y pagador (moneda local)

**Estados Unidos (USD)**

| Partida | Presupuesto | Real | Desviación | Desviación % |
|---|---:|---:|---:|---:|
| Seguros comerciales (US) | 1.150.000,00 | 1.120.000,00 | -30.000,00 | -2,6 % |
| Medicare / Medicaid (US) | 520.000,00 | 468.000,00 | -52.000,00 | -10,0 % |

**Reino Unido (GBP)**

| Partida | Presupuesto | Real | Desviación | Desviación % |
|---|---:|---:|---:|---:|
| Privado (UK) | 260.000,00 | 271.000,00 | 11.000,00 | +4,2 % |
| Contrato NHS (UK) | 75.000,00 | 75.000,00 | 0,00 | 0,0 % |

## 4. Gastos por país (moneda local)

**Estados Unidos (USD)**

| Partida | Presupuesto | Real | Desviación | Desviación % |
|---|---:|---:|---:|---:|
| Material clínico — MedSupply Partners (US) | 44.800,00 | 46.816,00 | 2.016,00 | +4,5 % |
| Laboratorio — ClearPath Labs (US) | 45.000,00 | 48.150,00 | 3.150,00 | +7,0 % |
| Interpretación médica — LinguaCare Interpreting (US) | 17.550,00 | 17.550,00 | 0,00 | 0,0 % |
| Nómina (US) | 1.050.000,00 | 1.060.000,00 | 10.000,00 | +1,0 % |
| Alquiler de clínicas (US) | 180.000,00 | 180.000,00 | 0,00 | 0,0 % |

**Reino Unido (GBP)**

| Partida | Presupuesto | Real | Desviación | Desviación % |
|---|---:|---:|---:|---:|
| Material clínico — BritMed Supplies (UK) | 9.450,00 | 9.450,00 | 0,00 | 0,0 % |
| Laboratorio — Thames Pathology (UK) | 10.150,00 | 10.353,00 | 203,00 | +2,0 % |
| Nómina (UK) | 210.000,00 | 209.000,00 | -1.000,00 | -0,5 % |
| Alquiler de clínicas (UK) | 52.000,00 | 52.000,00 | 0,00 | 0,0 % |
| Licencias de software clínico (UK) | 8.000,00 | Sin registros | — | — |

## 5. Desviaciones relevantes (≥ 5 %)

_Las hipótesis son explicaciones que sugieren los datos, no causas demostradas._

### Medicare / Medicaid (US) — -10,0 %

Sin explicar: Los ingresos de Medicare / Medicaid (US) se desviaron -10,0 % frente al presupuesto (468.000,00 frente a 520.000,00 USD). No es posible una hipótesis sostenida por datos: faltan los datos operativos necesarios para explicarla (Reclamaciones presentadas, Reclamaciones presentadas (presupuesto), Tasa de denegación (%), Tasa de denegación (%) (presupuesto)).

Cifras que la sostienen:
- `line:US.medicare_medicaid.patient_services:budget` (Presupuesto): 520.000,00 USD — budget.csv fila 3
- `line:US.medicare_medicaid.patient_services:actual` (Real): 468.000,00 USD — `JE-2026OCT-0010`, `JE-2026OCT-0011`, `JE-2026OCT-0012`, `JE-2026OCT-0013`, `JE-2026OCT-0014`, `JE-2026OCT-0015`, `JE-2026OCT-0016`, `JE-2026OCT-0017`, `JE-2026OCT-0018`
- `line:US.medicare_medicaid.patient_services:variance_pct` (Desviación): -10,0 % — cálculo

### Laboratorio — ClearPath Labs (US) — +7,0 %

Hipótesis: Laboratorio — ClearPath Labs (US) se desvió +7,0 % frente al presupuesto (48.150,00 frente a 45.000,00 USD). El volumen pasó de 1.500 a 1.500 pruebas (0,0 %), mientras que el precio unitario pasó de 30,00 a 32,10 USD (+7,0 %). La desviación es coherente con un cambio de precio, no de volumen.

Cifras que la sostienen:
- `driver:US.n/a.laboratory:unit_price` (Precio unitario): 32,10 USD — `JE-2026OCT-0027`, `JE-2026OCT-0028`, `JE-2026OCT-0029`
- `driver:US.n/a.laboratory:unit_price_budget` (Precio unitario (presupuesto)): 30,00 USD — cálculo
- `driver:US.n/a.laboratory:unit_price_change_pct` (Variación del precio unitario): +7,0 % — cálculo
- `driver:US.n/a.laboratory:units` (Unidades): 1.500 — `JE-2026OCT-0027`, `JE-2026OCT-0028`, `JE-2026OCT-0029`
- `driver:US.n/a.laboratory:units_budget` (Unidades (presupuesto)): 1.500 — operations.csv fila 9
- `driver:US.n/a.laboratory:units_change_pct` (Variación de unidades): 0,0 % — cálculo
- `line:US.n/a.laboratory:budget` (Presupuesto): 45.000,00 USD — budget.csv fila 7
- `line:US.n/a.laboratory:actual` (Real): 48.150,00 USD — `JE-2026OCT-0027`, `JE-2026OCT-0028`, `JE-2026OCT-0029`
- `line:US.n/a.laboratory:variance_pct` (Desviación): +7,0 % — cálculo

## 6. Datos faltantes o dudosos

**Datos faltantes**

- Licencias de software clínico (UK): presupuesto 8.000,00 GBP, **sin registros** en ledger.csv. Declarado como faltante; no se trata como 0 ni como una desviación del −100 %.
- Medicare / Medicaid (US): operations.csv — claims_submitted, claims_submitted_budget, denial_rate_pct, denial_rate_pct_budget.

**Registros dudosos (excluidos de los totales)**

- `JE-2026OCT-0053` (ledger.csv fila 54): la descripción puede identificar a un paciente (retenida; Cumplimiento avisado).

## 7. Anexo de trazabilidad

| Cifra | Valor | Origen |
|---|---:|---|
| `consolidated:expense:actual` | 1.709.135,81 USD | `JE-2026OCT-0024`, `JE-2026OCT-0025`, `JE-2026OCT-0026`, `JE-2026OCT-0027`, `JE-2026OCT-0028`, `JE-2026OCT-0029`, `JE-2026OCT-0030`, `JE-2026OCT-0031`, `JE-2026OCT-0032`, `JE-2026OCT-0033`, `JE-2026OCT-0034`, `JE-2026OCT-0035`, `JE-2026OCT-0036`, `JE-2026OCT-0037`, `JE-2026OCT-0038`, `JE-2026OCT-0039`, `JE-2026OCT-0040`, `JE-2026OCT-0041`, `JE-2026OCT-0042`, `JE-2026OCT-0043`, `JE-2026OCT-0044`, `JE-2026OCT-0045`, `JE-2026OCT-0046`, `JE-2026OCT-0047`, `JE-2026OCT-0048`, `JE-2026OCT-0049`, `JE-2026OCT-0050`, `JE-2026OCT-0051`, `JE-2026OCT-0052`; tipo de cambio (1 GBP = 1,27 USD) |
| `consolidated:expense:budget` | 1.705.142,00 USD | cálculo; tipo de cambio (1 GBP = 1,27 USD) |
| `consolidated:expense:comparable_budget` | 1.694.982,00 USD | cálculo; tipo de cambio (1 GBP = 1,27 USD) |
| `consolidated:expense:variance_pct` | +0,8 % | cálculo |
| `consolidated:result:actual` | 318.284,19 USD | `JE-2026OCT-0001`, `JE-2026OCT-0002`, `JE-2026OCT-0003`, `JE-2026OCT-0004`, `JE-2026OCT-0005`, `JE-2026OCT-0006`, `JE-2026OCT-0007`, `JE-2026OCT-0008`, `JE-2026OCT-0009`, `JE-2026OCT-0010`, `JE-2026OCT-0011`, `JE-2026OCT-0012`, `JE-2026OCT-0013`, `JE-2026OCT-0014`, `JE-2026OCT-0015`, `JE-2026OCT-0016`, `JE-2026OCT-0017`, `JE-2026OCT-0018`, `JE-2026OCT-0019`, `JE-2026OCT-0020`, `JE-2026OCT-0021`, `JE-2026OCT-0022`, `JE-2026OCT-0023`, `JE-2026OCT-0024`, `JE-2026OCT-0025`, `JE-2026OCT-0026`, `JE-2026OCT-0027`, `JE-2026OCT-0028`, `JE-2026OCT-0029`, `JE-2026OCT-0030`, `JE-2026OCT-0031`, `JE-2026OCT-0032`, `JE-2026OCT-0033`, `JE-2026OCT-0034`, `JE-2026OCT-0035`, `JE-2026OCT-0036`, `JE-2026OCT-0037`, `JE-2026OCT-0038`, `JE-2026OCT-0039`, `JE-2026OCT-0040`, `JE-2026OCT-0041`, `JE-2026OCT-0042`, `JE-2026OCT-0043`, `JE-2026OCT-0044`, `JE-2026OCT-0045`, `JE-2026OCT-0046`, `JE-2026OCT-0047`, `JE-2026OCT-0048`, `JE-2026OCT-0049`, `JE-2026OCT-0050`, `JE-2026OCT-0051`, `JE-2026OCT-0052`; tipo de cambio (1 GBP = 1,27 USD) |
| `consolidated:result:budget` | 390.308,00 USD | cálculo; tipo de cambio (1 GBP = 1,27 USD) |
| `consolidated:result:comparable_budget` | 400.468,00 USD | cálculo; tipo de cambio (1 GBP = 1,27 USD) |
| `consolidated:result:variance_pct` | -20,5 % | cálculo |
| `consolidated:revenue:actual` | 2.027.420,00 USD | `JE-2026OCT-0001`, `JE-2026OCT-0002`, `JE-2026OCT-0003`, `JE-2026OCT-0004`, `JE-2026OCT-0005`, `JE-2026OCT-0006`, `JE-2026OCT-0007`, `JE-2026OCT-0008`, `JE-2026OCT-0009`, `JE-2026OCT-0010`, `JE-2026OCT-0011`, `JE-2026OCT-0012`, `JE-2026OCT-0013`, `JE-2026OCT-0014`, `JE-2026OCT-0015`, `JE-2026OCT-0016`, `JE-2026OCT-0017`, `JE-2026OCT-0018`, `JE-2026OCT-0019`, `JE-2026OCT-0020`, `JE-2026OCT-0021`, `JE-2026OCT-0022`, `JE-2026OCT-0023`; tipo de cambio (1 GBP = 1,27 USD) |
| `consolidated:revenue:budget` | 2.095.450,00 USD | cálculo; tipo de cambio (1 GBP = 1,27 USD) |
| `consolidated:revenue:comparable_budget` | 2.095.450,00 USD | cálculo; tipo de cambio (1 GBP = 1,27 USD) |
| `consolidated:revenue:variance_pct` | -3,2 % | cálculo |
| `driver:US.n/a.laboratory:unit_price` | 32,10 USD | `JE-2026OCT-0027`, `JE-2026OCT-0028`, `JE-2026OCT-0029` |
| `driver:US.n/a.laboratory:unit_price_budget` | 30,00 USD | cálculo |
| `driver:US.n/a.laboratory:unit_price_change_pct` | +7,0 % | cálculo |
| `driver:US.n/a.laboratory:units` | 1.500 | `JE-2026OCT-0027`, `JE-2026OCT-0028`, `JE-2026OCT-0029` |
| `driver:US.n/a.laboratory:units_budget` | 1.500 | operations.csv fila 9 |
| `driver:US.n/a.laboratory:units_change_pct` | 0,0 % | cálculo |
| `line:UK.n/a.clinic_rent:actual` | 52.000,00 GBP | `JE-2026OCT-0050`, `JE-2026OCT-0051`, `JE-2026OCT-0052` |
| `line:UK.n/a.clinic_rent:budget` | 52.000,00 GBP | budget.csv fila 14 |
| `line:UK.n/a.clinic_rent:variance_pct` | 0,0 % | cálculo |
| `line:UK.n/a.clinical_software_licences:actual` | Sin registros | — |
| `line:UK.n/a.clinical_software_licences:budget` | 8.000,00 GBP | budget.csv fila 15 |
| `line:UK.n/a.clinical_supplies:actual` | 9.450,00 GBP | `JE-2026OCT-0043`, `JE-2026OCT-0044`, `JE-2026OCT-0045` |
| `line:UK.n/a.clinical_supplies:budget` | 9.450,00 GBP | budget.csv fila 11 |
| `line:UK.n/a.clinical_supplies:variance_pct` | 0,0 % | cálculo |
| `line:UK.n/a.laboratory:actual` | 10.353,00 GBP | `JE-2026OCT-0046`, `JE-2026OCT-0047` |
| `line:UK.n/a.laboratory:budget` | 10.150,00 GBP | budget.csv fila 12 |
| `line:UK.n/a.laboratory:variance_pct` | +2,0 % | cálculo |
| `line:UK.n/a.payroll:actual` | 209.000,00 GBP | `JE-2026OCT-0048`, `JE-2026OCT-0049` |
| `line:UK.n/a.payroll:budget` | 210.000,00 GBP | budget.csv fila 13 |
| `line:UK.n/a.payroll:variance_pct` | -0,5 % | cálculo |
| `line:UK.nhs_contract.patient_services:actual` | 75.000,00 GBP | `JE-2026OCT-0022`, `JE-2026OCT-0023` |
| `line:UK.nhs_contract.patient_services:budget` | 75.000,00 GBP | budget.csv fila 5 |
| `line:UK.nhs_contract.patient_services:variance_pct` | 0,0 % | cálculo |
| `line:UK.private.patient_services:actual` | 271.000,00 GBP | `JE-2026OCT-0019`, `JE-2026OCT-0020`, `JE-2026OCT-0021` |
| `line:UK.private.patient_services:budget` | 260.000,00 GBP | budget.csv fila 4 |
| `line:UK.private.patient_services:variance_pct` | +4,2 % | cálculo |
| `line:US.commercial_insurance.patient_services:actual` | 1.120.000,00 USD | `JE-2026OCT-0001`, `JE-2026OCT-0002`, `JE-2026OCT-0003`, `JE-2026OCT-0004`, `JE-2026OCT-0005`, `JE-2026OCT-0006`, `JE-2026OCT-0007`, `JE-2026OCT-0008`, `JE-2026OCT-0009` |
| `line:US.commercial_insurance.patient_services:budget` | 1.150.000,00 USD | budget.csv fila 2 |
| `line:US.commercial_insurance.patient_services:variance_pct` | -2,6 % | cálculo |
| `line:US.medicare_medicaid.patient_services:actual` | 468.000,00 USD | `JE-2026OCT-0010`, `JE-2026OCT-0011`, `JE-2026OCT-0012`, `JE-2026OCT-0013`, `JE-2026OCT-0014`, `JE-2026OCT-0015`, `JE-2026OCT-0016`, `JE-2026OCT-0017`, `JE-2026OCT-0018` |
| `line:US.medicare_medicaid.patient_services:budget` | 520.000,00 USD | budget.csv fila 3 |
| `line:US.medicare_medicaid.patient_services:variance_pct` | -10,0 % | cálculo |
| `line:US.n/a.clinic_rent:actual` | 180.000,00 USD | `JE-2026OCT-0034`, `JE-2026OCT-0035`, `JE-2026OCT-0036`, `JE-2026OCT-0037`, `JE-2026OCT-0038`, `JE-2026OCT-0039`, `JE-2026OCT-0040`, `JE-2026OCT-0041`, `JE-2026OCT-0042` |
| `line:US.n/a.clinic_rent:budget` | 180.000,00 USD | budget.csv fila 10 |
| `line:US.n/a.clinic_rent:variance_pct` | 0,0 % | cálculo |
| `line:US.n/a.clinical_supplies:actual` | 46.816,00 USD | `JE-2026OCT-0024`, `JE-2026OCT-0025`, `JE-2026OCT-0026` |
| `line:US.n/a.clinical_supplies:budget` | 44.800,00 USD | budget.csv fila 6 |
| `line:US.n/a.clinical_supplies:variance_pct` | +4,5 % | cálculo |
| `line:US.n/a.laboratory:actual` | 48.150,00 USD | `JE-2026OCT-0027`, `JE-2026OCT-0028`, `JE-2026OCT-0029` |
| `line:US.n/a.laboratory:budget` | 45.000,00 USD | budget.csv fila 7 |
| `line:US.n/a.laboratory:variance_pct` | +7,0 % | cálculo |
| `line:US.n/a.medical_interpreting:actual` | 17.550,00 USD | `JE-2026OCT-0030`, `JE-2026OCT-0031` |
| `line:US.n/a.medical_interpreting:budget` | 17.550,00 USD | budget.csv fila 8 |
| `line:US.n/a.medical_interpreting:variance_pct` | 0,0 % | cálculo |
| `line:US.n/a.payroll:actual` | 1.060.000,00 USD | `JE-2026OCT-0032`, `JE-2026OCT-0033` |
| `line:US.n/a.payroll:budget` | 1.050.000,00 USD | budget.csv fila 9 |
| `line:US.n/a.payroll:variance_pct` | +1,0 % | cálculo |
