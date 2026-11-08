# services/financial_close — cierre financiero mensual

Un único agente con herramientas deterministas (no LangGraph); el modelo solo elige qué explicación calcular y redacta. Stakeholder Dra. Sandra Okonkwo; revisor Tom Callahan. Detalle en `docs/financial-close/financial-close.md`.

```bash
services/api/.venv/bin/python scripts/seed_financial_close.py [--incomplete]           # CSV + ingesta en fc_* + 7 cuentas
services/api/.venv/bin/python scripts/run_financial_close.py --month 2026-10 --inline  # sin --inline: encola en Celery
services/api/.venv/bin/python scripts/run_financial_close.py --offline --out docs/financial-close/examples/complete [--incomplete]
services/api/.venv/bin/python -m pytest tests/financial_close
(cd services/api && .venv/bin/python -m pytest tests/test_financial_close_api.py -o addopts="")
```

Cuentas en `data/financial_close/demo-users.db.json` (Tom es el revisor). Pantalla `/financial-close`. El `scheduler` lanza `scripts/financial_close_scheduled.py` cada hora.

Mapa: `catalog.py` (BRIEFING como datos: 14 partidas, indicadores, umbral 5 %, 1 GBP = 1,27 USD, revisor, 7 destinatarios, `AGENT_TOOLS`, calendario), `seed_data.py`, `ingestion.py` (`source_file`/`source_row`/`entry_id`; registros dudosos con motivos), `sensitive.py`, `calculations.py` + `drivers.py` (herramientas puras, `Decimal`), `runtime.py` (`ActionLog`, `AgentRuntime`, `read_only_session`), `narrative.py`, `agent.py` (`CloseAgent`), `report.py` (`build_figures` + 7 secciones), `workflow.py` (ingesta, `run_close`, `decide`, `revise`, `distribute`), `schedule.py`, `models.py` (10 tablas `fc_*`), `router.py`. Tareas `financial_close.run_close` y `financial_close.revise_report`. Frontend: `types/financialClose.ts` (`parseReport` pinta el markdown como bloques de React, sin inyectar HTML).

## Reglas que no hay que romper

- Informe EN + ES; modelo con plantilla de respaldo; distribución por bandeja interna.
- **El modelo nunca calcula ni ve descripciones del ledger.** Cada número de su texto debe existir en los hechos (formato EN o ES); si no, plantilla. Solo recibe el presupuesto comparable.
- Faltante ≠ 0: partida sin registros → `missing`, sin desviación; consolidado contra el presupuesto comparable, marcado `*`. Registros dudosos fuera de los totales.
- Umbral sobre el valor exacto (4,96 % no es relevante aunque se muestre "5,0 %").
- Mismos datos → mismas cifras (`figures_sha256` idéntico). Una revisión cambia el texto, nunca las cifras.
- Solo `revenue_cycle_director` (directorio `fc_staff`, no el admin) decide; rechazar exige comentarios sin PHI; aprobar distribuye a `DISTRIBUTION_ROLES` en la misma transacción tras `sensitive.screen`.
- Registro con PHI: dudoso, `description = NULL`, citado solo por `entry_id`, aviso a Claire con clave determinista.
- Calendario en hora de Austin (`zoneinfo`), días hábiles L-V sin Año Nuevo, 4-jul (observados) ni Labor Day. `nth_business_day` lanza `ValueError` si `n < 1` o el mes no tiene `n` días hábiles.

## Gotchas

- **Formato de `entry_id` = `JE-2026OCT-0001`**: con series largas de dígitos `phi_guard` lo toma por NHS number o teléfono y bloquea la distribución. Cualquier identificador en un texto saliente: sin series largas de dígitos.
- Tests de la raíz con nombre único (`test_fc_*.py`); ayudantes en `tests/financial_close/fc_helpers.py`, nunca `from conftest import`. `FC_TABLES` incluye `AuditLogEntry.__table__`.
- `operations.csv` solo tiene `value`: el presupuesto de cada indicador va como `<indicador>_budget`.
