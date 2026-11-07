# Cierre financiero mensual (Agentic Engineering)

Rama `feature/financial-close`, apilada sobre `feature/supplier-contracts`, 2026-11-07. Stakeholder: Dra. Sandra Okonkwo (CEO). Revisor: Tom Callahan (Revenue Cycle Director). BRIEFING de HealthCore descargado del repo del currículo (`content/contexts/financial-close/healthcore-BRIEFING.es.md`): el de Descargas era el del onboarding.

Un **único agente con herramientas** prepara el cierre de cada mes. El código calcula todas las cifras de forma determinista. El modelo elige qué explicación calcular para cada desviación y redacta. Tom aprueba o rechaza antes de cualquier distribución.

## 1. Estructura del informe (BRIEFING, "Estructura del informe")

| # | Sección | Qué contiene | Quién la produce |
|---|---|---|---|
| 1 | Resumen ejecutivo | Máx. 5 líneas | Modelo (validado) o plantilla |
| 2 | Resultado consolidado en USD | Ingresos, gastos y resultado: presupuesto, presupuesto comparable, real, desviación | Código |
| 3 | Ingresos por país y pagador | Moneda local (USD / GBP) | Código |
| 4 | Gastos por país | Moneda local | Código |
| 5 | Desviaciones relevantes (≥ 5 %) | Una por partida: hipótesis + cifras que la sostienen, cada una con su id | Hipótesis: modelo o plantilla; cifras: código |
| 6 | Datos faltantes o dudosos | Partidas sin registros, archivos ausentes, indicadores que faltan, registros dudosos (solo `entry_id` + motivo) | Código |
| 7 | Anexo de trazabilidad | Cada cifra → `entry_id` / fila de `budget.csv` / fila de `operations.csv` / tipo de cambio | Código |

Idiomas: inglés (base) y español (opcional, implementado). Las cifras se calculan una vez; solo cambian las palabras y el formato (`1,120,000.00` / `1.120.000,00`). Un test compara todos los números de las secciones 2 a 7 en los dos idiomas.

## 2. Herramientas de cálculo (lista cerrada, `catalog.AGENT_TOOLS`)

| Herramienta | Módulo | Qué hace |
|---|---|---|
| `read_period_data` | `runtime.py` | Lee el periodo ingerido en una transacción **de solo lectura** (Postgres `SET TRANSACTION READ ONLY`; SQLite `PRAGMA query_only`) |
| `compute_line_totals` | `calculations.py` | Suma por partida de los registros válidos (los dudosos nunca entran) |
| `compare_to_budget` | `calculations.py` | Desviación absoluta y % por partida; `missing` (presupuestada sin registros) y `unbudgeted` |
| `convert_to_usd` | `calculations.py` | 1 GBP = 1,27 USD (BRIEFING) |
| `consolidate_usd` | `calculations.py` | Ingresos, gastos y resultado en USD contra el **presupuesto comparable** |
| `find_relevant_deviations` | `drivers.py` | \|desviación\| ≥ 5 % con el valor exacto (4,96 % no llega aunque se muestre "5,0 %") |
| `payer_claims_drivers` | `drivers.py` | Reclamaciones presentadas, tasa de denegación y pagadas = presentadas × (1 − denegación) |
| `unit_cost_drivers` | `drivers.py` | Unidades (ledger) y precio unitario (importe / unidades) frente al presupuesto |
| `data_quality_report` | `drivers.py` | Lo que falta o es dudoso, sin descripciones |

Todas son funciones puras sobre `Decimal`. Cada llamada pasa por `AgentRuntime.call`: una herramienta fuera de la lista se rechaza (`denied`) y cada uso queda en `fc_agent_actions` con `actor = process:agent:financial-close` y `actor_type = automated_process`.

## 3. Resultados del BRIEFING (comprobados por tests y en Supabase)

| Partida | Resultado |
|---|---|
| Medicare / Medicaid −10,0 % | Relevante. Mismas reclamaciones (4.000), denegación del 14,0 % al 22,6 %; pagadas 3.440 → 3.096 = **−10,0 %**, la misma desviación que los ingresos |
| Laboratorio EE. UU. +7,0 % | Relevante. Mismas 1.500 pruebas; precio 30,00 → 32,10 USD (+7,0 %) |
| Material EE. UU. +4,5 % y Privado R. U. +4,2 % | No relevantes |
| Licencias de software R. U. | "Sin registros": ni 0 ni −100 %. El consolidado de gastos se compara contra el presupuesto comparable (sin esas 8.000 GBP) y se marca con `*` |
| Registro con nombre de paciente | `JE-2026OCT-0053`, dudoso, descripción retenida, citado solo por su `entry_id` (ni siquiera su importe); aviso a Claire Whitfield |

## 4. Ejemplos (evidencia del PR)

- `examples/complete/`: informe EN/ES + `figures.json` (trazabilidad completa). Determinista: `scripts/run_financial_close.py --offline`.
- `examples/incomplete/`: escenario sin los indicadores de Medicare/Medicaid (`--incomplete`). La desviación sigue apareciendo como **"Unexplained:"** y la sección 6 enumera los indicadores que faltan.
- `examples/model-run/`: versión real con deepseek y Supabase (v2 del 2026-11-07), con el registro de acciones del agente.

## 5. Flujo, revisión y distribución

```
scheduler (cada hora) → financial_close_scheduled.py → ¿3.er día hábil, 06:00 Austin? → Celery financial_close.run_close
  → ingesta (si hay CSV en data/raw/financial-close/<YYYY-MM>/) + aviso a Claire por cada registro con PHI
  → agente → informe v1 `pending_review` + aviso a Tom
Tom: aprobar → distribución (bandeja interna, 7 destinatarios del BRIEFING) → `distributed`
     rechazar + comentarios → `rejected` → Celery financial_close.revise_report → v2 con los comentarios (mismas cifras)
```

- **Calendario**: cron no sabe qué es un día hábil ni sigue el horario de verano de Austin. El servicio `scheduler` lanza el disparador cada hora y `schedule.is_close_due` decide con `zoneinfo` (America/Chicago). Días hábiles: lunes a viernes, sin Año Nuevo, 4 de julio (con su día observado) ni Labor Day.
- **Solo Tom decide** (`fc_staff.org_role = revenue_cycle_director`), no el admin de la plataforma. Rechazar exige comentarios; un comentario con datos de pacientes, salarios o datos bancarios se rechaza con 400 sin repetirlo.
- **Distribución**: solo de un informe aprobado, solo a `catalog.DISTRIBUTION_ROLES` y tras volver a pasar el texto saliente por `sensitive.screen` (si salta, 409 y nada se distribuye).
- **Revisión idempotente**: un reintento de la tarea no crea dos versiones.

## 6. Datos sensibles

Tipos (BRIEFING): datos de pacientes (`phi_guard`, la misma regla que la memoria del agente), salarios individuales y datos bancarios de proveedores (`sensitive.py`).

- **Ingesta** (antes de base de datos y agente): descripción sensible → registro dudoso, `description = NULL`, solo su SHA-256 y las categorías.
- **Modelo**: nunca recibe descripciones del ledger, solo cifras y etiquetas del catálogo. Del prompt solo se guarda su huella.
- **Logs y acciones**: solo identificadores, recuentos y huellas.
- **API**: la trazabilidad devuelve `description_withheld: true` y sin importe para los registros sensibles.
- Tests que lo fijan: `test_model_never_sees_descriptions_or_patient_data`, `test_logs_never_carry_patient_data`, `test_actions_identify_process_and_people_without_sensitive_data`, `test_no_response_carries_patient_data`.

## 7. Decisiones

- **Decisiones del usuario**: informe en inglés + español; el modelo planifica y redacta con plantilla de respaldo; distribución por bandeja interna (sin correo).
- `operations.csv` solo tiene `value`: el presupuesto de cada indicador va como `<indicador>_budget`.
- El presupuesto se guarda en positivo (como en el BRIEFING); los gastos del ledger son negativos y se comparan en valor absoluto.
- Los registros dudosos se excluyen de los totales y se declaran.
- **Validación del texto del modelo**: cada número debe existir en los hechos (en formato inglés o español), sin datos sensibles y con un máximo de 5 líneas en el resumen. Si falla, se usa la plantilla para ese elemento (en los dos idiomas a la vez).
- El prefijo "Hypothesis:" / "Hipótesis:" lo pone el código. Una desviación sin datos operativos nunca se pide al modelo: sale "Unexplained:".

## 8. Hallazgos de la prueba real (no los detectaban los tests)

- **Cifras ciertas mal emparejadas**: con el presupuesto completo y el comparable delante, deepseek escribió "0,8 % por encima de 1.705.142" (el 0,8 % es contra 1.694.982). El validador comprueba los números de uno en uno y no podía verlo. Ahora el modelo solo recibe el presupuesto comparable. Test: `test_model_only_sees_the_comparable_budget_and_line_kind`.
- **"Variación de ingresos" en un gasto**: ahora el modelo recibe `kind` y la regla de que un gasto nunca se llama ingreso.
- **Formato de `entry_id`**: `JE-202610-0001` encaja con un NHS number (3-3-4 dígitos) y `JE-2026-10-0001` con un teléfono (10 dígitos con guiones), así que el filtro de PHI bloqueaba la distribución de un informe legítimo. Ahora es `JE-2026OCT-0001`. Lo detectó el test de aprobación.
- Pendiente de verificar con el modelo real: la instrucción de lenguaje prudente ("es coherente con" en vez de "se debe a"), añadida después de la última corrida.

## 9. Cómo ejecutarlo

```bash
services/api/.venv/bin/python scripts/seed_financial_close.py [--incomplete]                     # CSV + ingesta en Supabase + 7 cuentas demo
services/api/.venv/bin/python scripts/run_financial_close.py --month 2026-10 --inline            # cierre en este proceso (modelo real)
services/api/.venv/bin/python scripts/run_financial_close.py --offline --out docs/financial-close/examples/complete   # ejemplo determinista
services/api/.venv/bin/python scripts/financial_close_scheduled.py --now 2026-11-04T12:00:00+00:00 --inline          # simula el disparo programado
services/api/.venv/bin/python -m pytest tests/financial_close                                     # sin red
(cd services/api && .venv/bin/python -m pytest tests/test_financial_close_api.py -o addopts="")
```

API con `SUPPLIERS_DB_PATH=data/financial_close/demo-users.db.json` (cuentas `tom@healthcore.test`, `sandra@…`, `claire@…`; la contraseña la imprime la semilla). Pantalla: `/financial-close` (menú Informes → "Cierre financiero"). `FINANCIAL_CLOSE_USE_MODEL=0` en el worker = solo plantillas.
