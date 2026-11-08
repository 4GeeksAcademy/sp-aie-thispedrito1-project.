# data/pipelines/rfp_intake — RFPs: recepción, respuesta, aprobación y aviso SSE

Un mismo ticket recorre tres grafos LangGraph (Hito 9, Partes 1-3) y avisa al backoffice por SSE. Diseño en `docs/rfp-intake.md`, `docs/rfp-response.md`, `docs/rfp-approval.md` y `docs/sse-notifications.md`. La API vive en `services/rfp_intake/` y la pantalla en `uis/backoffice/app/rfps/`.

## Comandos (desde la raíz)

```bash
docker compose up -d redis
services/api/.venv/bin/celery -A services.celery_app worker --loglevel=INFO --pool=solo
cd services/api && .venv/bin/uvicorn main:app --port 8000      # crea las tablas rfp_* al arrancar
cd uis/backoffice && npm run dev -- --port 3001                # /rfps
services/api/.venv/bin/python scripts/make_phi_test_rfp.py                       # PDF con PHI ficticia
services/api/.venv/bin/python scripts/reprocess_rfp.py <ticket_id> [--inline]    # Parte 1
services/api/.venv/bin/python scripts/generate_rfp_response.py <ticket_id> [--inline]   # Parte 2
services/api/.venv/bin/python scripts/advance_rfp_approval.py <ticket_id> [--inline]    # Parte 3
services/api/.venv/bin/python scripts/run_rfp_e2e.py --offline                   # E2E sin red
services/api/.venv/bin/python scripts/run_rfp_e2e.py --live data/raw/rfp/samples/healthcore-request-2.pdf
services/api/.venv/bin/python -m pytest tests/pipelines/test_rfp_intake.py tests/pipelines/test_rfp_response.py \
  tests/pipelines/test_rfp_approval.py tests/pipelines/test_rfp_e2e.py
```

Las Partes 2 y 3 se encadenan solas en el worker. `--live` no arranca la API: en una base nueva arrancarla una vez antes (crea columnas con `ensure_rfp_response_columns`). Los PDF de muestra están en `data/raw/rfp/samples/` (en `.gitignore`; originales en el repo del currículo `content/contexts/09-agentic-workflows/rfp-requests/healthcore/`). `punkt_tab` de NLTK se descarga solo la primera vez.

## Mapa

- **Parte 1** (grafo `rfp_intake`): `convert → phi_screen → classify → orchestrate → department_worker ×N (Send) → synthesize`, salida a `discard`. `agents.py`, `departments.py`, `phi.py`, `conversion.py` (MarkItDown), `readability.py`, `models.py`, `repository.py`, `runner.py`, `notifications.py`.
- **Parte 2** (grafo `rfp_response` + subgrafo `rfp_section`): `generators.py`, `evaluators.py`, `response_graph.py`, `response_runner.py`.
- **Parte 3** (grafo `rfp_approval` + subgrafo `rfp_department_approval`): `approval_graph.py`, `approval_runner.py` (`advance`: start | resume | retry | sync), `arbitration.py`, `decisions.py`, `final_document.py`, `checkpointer.py`, `trace.py`.
- Tareas Celery en `services/tasks/rfp_tasks.py`: `rfp_intake.process_ticket` → `rfp_response.generate_ticket` → `rfp_approval.advance_ticket`.

## Reglas que no hay que romper

**Transversales**
- El grafo no toca la base de datos; la tarea Celery solo lleva el `ticket_id`. El PDF se guarda como `data/raw/rfp/<ticket_id>.pdf`, nunca con el nombre original.
- Las reglas sin excepción del BRIEFING viven en código, no en prompts: `revenue` y `compliance` siempre (`required_departments`), moneda y BAA/DPA por `client_country`, contactos del catálogo. Anti-invención: cifras que no están en el texto se eliminan.
- Traza de las tres partes en `rfp_trace_events` (sin texto libre, `event_id` determinista). La Parte 1 usa `graph.stream` para tener la hora real de cada nodo.

**Parte 1**
- Estados `analizando` → `analisis_completo` | `descartado` | `error`. Un fallo técnico es `error`, nunca `descartado`.
- PHI: `phi_screen` antes de cualquier agente; con PHI ningún modelo ve el documento → `descartado` + `phi_detected` + `requires_compliance_review`, solo se guardan categorías. Regla propia `is_patient_phi` (el email de compras no es PHI) sobre `phi_guard.find_matches`; no cambiar `phi_guard` para esto (afecta a la memoria del agente).
- Handoff: `next_stage = "generando_borrador"` + `handoff_payload` v1.

**Parte 2**
- Estados `generando_borrador` → `en_evaluación` → `needs_human_review` | `evaluacion_completa`. `handoff_payload` nunca se pisa: lo de la Parte 2 va en `response_handoff`.
- Cumplimiento es código (reglas con `rule_id`: `no-phi`, `baa-us`, `dpa-uk`, `currency-by-country`, `no-invented-figures`, `client-country-known`); pertinencia es modelo; el silencio del modelo no aprueba. `client-country-known` no es arreglable: revisión humana sin gastar iteraciones. Máximo 3 iteraciones.
- Legibilidad: ≥100 y ≤450 palabras, ≤25 palabras/frase, Fernández Huerta ≥50 (Compliance ronda 51-53; subirlo la suspendería siempre).
- Progreso en vivo por `on_progress` (sesión propia + candado). `recover_stale` vigila también estos estados. Sin RAG en el generador.

**Parte 3**
- Con todo aprobado (Compliance siempre) el documento final se genera solo y el ticket pasa a `terminado`.
- Checkpointer Postgres en Supabase, hilo `rfp-{ticket_id}`, `prepare_threshold=None` (pooler en modo transacción). `langgraph-checkpoint==3.0.1` + `langgraph-checkpoint-postgres==3.0.5` fijados.
- Pausa por rama: `interrupt()` en el subgrafo y `Command(resume={interrupt_id: …})` solo para quien decidió. Lo anterior a un `interrupt()` debe ser puro.
- La decisión no viaja por Redis: la API la valida, la guarda en `pending_decision` y encola el `ticket_id`; el worker revalida (huella del borrador incluida).
- Límites en código: 2 rondas de cambios por sección y 2 de arbitraje. Rechazar o agotar arbitraje → `descartado` (`rejected_in_approval` / `arbitration_limit`).
- Arbitraje sin LLM; los regulatorios primero. Capacidad: `confirmed_capacity` frente a `covered_population`.
- Firma: cualquier autenticado en nombre del responsable; `decided_by` guarda quién pulsó.

**SSE**
- `notify_ticket_created` al terminar `orchestrate` (no avisa de no-RFP ni PHI), publica en Redis `healthcore:rfp-events` y nunca lanza. Evento `rfp_ticket_created` con 7 campos fijos, filtrados también en la API (`parse_message`). `status = analizando`.
- `tests/pipelines/conftest.py` sustituye la publicación por una lista (`published_rfp_events`, autouse): sin él, un Redis local recibiría avisos falsos.

## Gotchas

- Celery prefork falla en macOS: worker con `--pool=solo` (si no, el ticket queda `analizando` hasta `recover_stale`).
- MarkItDown: viñetas como `(cid:127)` y tablas desemparejadas. `py-readability-metrics` exige ≥100 palabras (si no, `null` + `readability_note`).
- deepseek escribe cifras con letras (`spoken_numbers_es`) y "3.000" español frente a "3,000": se normaliza antes de comparar.
- El proxy corta a los 60 s: con `--inline` no hay reintento y el ticket queda `error`.
- LangGraph 0.6 deja pausas ya resueltas en `get_state` (viva = su tarea sin `result`) y conserva el `error` de una tarea reintentada con éxito.
- Los fixtures SQLite crean todo el esquema RFP (`rfp_fixtures.RFP_TABLES`).
- `test_rfp_response.py::test_ticket_shows_progress_in_real_time` es sensible al tiempo en la batería completa.
