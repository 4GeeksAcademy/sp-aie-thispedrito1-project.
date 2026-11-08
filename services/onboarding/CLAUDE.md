# services/onboarding — orquestador de incorporaciones

Un único agente LangGraph lleva cada incorporación por las 7 etapas del BRIEFING (trámites → empresa → operación → cumplimiento → función → cómo trabajamos → accesos y cierre). Stakeholder Diane Foster. Detalle en `docs/onboarding/onboarding-orchestrator.md`.

```bash
services/api/.venv/bin/python -m pytest tests/onboarding                                   # sin red, incluye reinicio con SIGKILL
(cd services/api && .venv/bin/python -m pytest tests/test_onboarding_api.py -o addopts="")
docker compose up -d qdrant && services/api/.venv/bin/python scripts/index_onboarding_docs.py   # ~20 min
services/api/.venv/bin/python scripts/evaluate_onboarding_retrieval.py                     # calibra DEFAULT_MIN_SCORE
services/api/.venv/bin/python scripts/seed_onboarding.py --blocked-attempt                 # → data/onboarding/demo-users.db.json
services/api/.venv/bin/python scripts/onboarding_followup.py                               # barrido de plazos
```

Para entrar con las cuentas de la semilla: API con `SUPPLIERS_DB_PATH=data/onboarding/demo-users.db.json` + Redis + worker (`--pool=solo`).

Mapa: `catalog.py` (fuente única del BRIEFING), `graph.py` (nodos generados desde `STAGES`), `runner.py` (`advance` idempotente + `sync` + efectos; checkpointer Postgres o SQLite; hilo `onboarding-<id>`), `checks.py`, `approvals.py`, `access.py`, `followup.py`, `qa.py`, `routing.py`, `sensitive.py`, `permissions.py`, `notifications.py`, `repository.py`, `models.py` (6 tablas `onboarding_*`), `router.py`. Corpus en `data/pipelines/onboarding_docs.py` + `docs/company-onboarding/{en,es}/`. Tareas `onboarding.advance_hire` y `onboarding.followup_sweep`. Pantallas `/onboarding`, `/onboarding/[id]`, `/onboarding/me`.

## Reglas que no hay que romper

- Comprobaciones con banco fijo corregido en código, no por modelo. Avisos en bandeja interna, sin correos.
- Material en inglés + español; español SOLO bajo HIPAA (no existe UK GDPR en español y nunca se traduce al vuelo).
- El grafo no toca la BD: deja `effects` con id determinista (`hire:sN:rR:...`) y `runner.persist_effects` guarda los nuevos. Lo anterior a un `interrupt()` es puro.
- Autorización dos veces (API 403 + dentro del grafo) con el directorio `onboarding_staff`, nunca con el `admin` de la plataforma.
- Accesos solo por `access.issue_access_request`: datos de pacientes sin `compliance_approved_at` → `blocked` + evento de seguridad + aviso a Claire.
- Entradas validadas a mano (400 `{"errors"}`), no con Pydantic: el 422 repetiría el valor (un SSN). Ninguna columna para datos sensibles; texto libre por `sensitive.py`.
- `pending_answers` (check→grade) y `last_result` (nota) son claves distintas.
- Si cambian documentos o modelo de embeddings, recalibrar `DEFAULT_MIN_SCORE`.

## Gotchas

- `langgraph-checkpoint-sqlite==3.0.3` (compatible con `langgraph-checkpoint==3.0.1`) para la prueba de reinicio y el modo local.
- Las aristas de `Command(goto=…)` necesitan `destinations=` en `add_node` para dibujarse (`docs/onboarding/graph.mmd` se genera del grafo).
- Tests de API: un hash bcrypt por módulo y fechas de inicio futuras.
- `test_onboarding_resume.py::test_reopening_the_checkpointer_resumes_the_same_stage` falla en `pytest tests` (pasa solo) por `from conftest import Driver`; arreglo pendiente: usar la fixture `driver`.
- El conftest de onboarding incluye `AuditLogEntry.__table__` (procesos auditados).
