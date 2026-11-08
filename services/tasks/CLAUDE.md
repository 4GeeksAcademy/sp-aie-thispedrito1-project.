# services/tasks — cola Celery + Redis (y `services/celery_app.py`)

Detalle, mediciones y demo de la DLQ en `docs/async-tasks.md`.

## Comandos (desde la raíz)

```bash
docker compose up -d --build redis worker flower           # broker + worker + Flower (localhost:5555)
docker compose stop worker flower redis
services/api/.venv/bin/celery -A services.celery_app worker --loglevel=INFO --pool=solo   # nativo: en macOS prefork falla
services/api/.venv/bin/celery -A services.celery_app flower --port=5555
(cd services/api && .venv/bin/python -m pytest tests/test_async_tasks.py -o addopts="")
```

`REDIS_URL` por defecto `redis://localhost:6379/0`; en Docker lo fija el compose.

## Mapa

- `services/celery_app.py`: instancia compartida por API y worker (solo JSON, `acks_late`, `track_started`, `prefetch=1`, límites 15/20 min, `visibility_timeout` 2 h, eventos para Flower). Añade la raíz y `services/api` al path y carga `services/api/.env`. Sube `httpx` a WARNING.
- `pipeline_tasks.py` (`ObservableTask`, `reporting.run_monthly_clinic_supply_performance`, `enqueue_monthly_pipeline_run`), `rfp_tasks.py`, `onboarding_tasks.py`, `financial_close_tasks.py`, `dead_letters.py` (tabla `task_dead_letters`), `status.py` + `router.py` (`GET /tasks/{task_id}`, solo admin), `redaction.py`.

## Reglas que no hay que romper

- `POST /reporting/pipeline-runs`: **la API comprueba, el worker bloquea**. `reserve_monthly_run` hace una sola lectura (`find_blocking_run`, ignora corridas caducadas) con `AUTOCOMMIT` y encola un `uuid4`; el worker crea la fila. El índice único parcial sigue siendo el lock: si dos pasan a la vez, la segunda termina `cancelled`.
- `max_retries=3` (4 ejecuciones → DLQ con `attempt=4`), backoff 10/20/40 s con **`retry_jitter=False`** (el jitter por defecto puede reintentar al instante).
- Cada intento tiene su fila en `pipeline_runs`: el primero usa el `run_id` reservado, los reintentos `run_id=None`. `_release_run_if_still_active` marca `failed` lo que quedó `queued`/`running`. Worker muerto → `running` hasta que el heartbeat (30 min) lo marca `crashed`.
- `SoftTimeLimitExceeded` no se reintenta y va directo a la DLQ (el límite duro no llama a `on_failure`).
- Estados (`status.py`): `RECEIVED`→`pending`; `RETRY`→`started` con el error anterior; `REVOKED`/`REJECTED`/`IGNORED`→`failure`; desconocido o caducado (24 h) → `pending`.
- `pipeline_tasks.py` no importa Prefect a nivel de módulo (lo importa la API).
- Las tareas solo llevan ids, nunca datos de negocio.

## Gotchas

- **Prefect 3 lanza `CancelledRun`** cuando un flow devuelve `Cancelled(...)` y se llama directamente. La tarea lo captura y devuelve `{"status": "cancelled"}`; si no, autoretry lo mandaba a la DLQ.
- **`celery.app.trace` repite el error crudo** (y el traceback): `redaction.RedactingFilter` enmascara el mensaje y precalcula `record.exc_text`.
- `task.apply()` ejecuta los reintentos de forma síncrona y llama a `on_failure` al agotarlos: permite probar reintentos + DLQ sin Redis.
- Con `acks_late`, `visibility_timeout` > `task_time_limit` o Redis reentrega tareas sanas.
- psycopg2 manda `BEGIN` como viaje aparte: para una lectura suelta sensible a latencia, `session.connection(execution_options={"isolation_level": "AUTOCOMMIT"})`.
- Medir antes de culpar a la cola: los ~500 ms del 202 eran el lock en Supabase, no Redis (2-4 ms).
- Simular la DLQ sin tocar datos: worker con `PREFECT_API_URL=http://127.0.0.1:9/api PREFECT_CLIENT_MAX_RETRIES=0`.
- No verificado: build de `services/worker/Dockerfile` y `worker`/`flower` en Docker. Flower no tiene login (puertos solo en `127.0.0.1`).
