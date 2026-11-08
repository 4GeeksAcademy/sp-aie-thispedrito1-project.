# services/job_runner — job nocturno de telemetría (`scripts/nightly_export.py`)

Exporta `telemetry_events` del día a `data/raw/telemetry_YYYY-MM-DD.csv`, lanza el pipeline de negocio como subproceso y registra la ejecución en `job_runs`. Detalle y logs reales en `docs/nightly-export.md`.

```bash
services/api/.venv/bin/python scripts/nightly_export.py                         # ayer (UTC)
TARGET_DATE=2026-08-21 services/api/.venv/bin/python scripts/nightly_export.py
docker compose up -d --build scheduler                                          # 02:15 UTC (supercronic)
services/api/.venv/bin/python -m pytest tests/jobs                              # SQLite, sin FastAPI, incluye dos procesos reales
```

Mapa: `models.py` (`job_runs`), `repository.py` (`create_run`, `mark_processing`, `finish_run`, `cancel_run`, `has_processing_lock`, `has_completed_for_date`, `recover_stale_runs`), `migrations/001_create_job_runs.sql`. Disparador en `services/scheduler/` (`Dockerfile` + `crontab`; también lanza `scripts/financial_close_scheduled.py` cada hora).

## Reglas que no hay que romper

- El job es independiente de la API: un test comprueba que `fastapi`/`prefect` no se importan. Por eso sus tests están en `tests/jobs/` y no en `services/api/tests`.
- **El lock es el estado `processing` + índice único parcial** (`uq_job_runs_single_processing`). Una consulta previa sola no basta (dos procesos reales pasaron a la vez).
- El estado final empieza en `failed`; solo la última línea del `try` lo pone en `completed`; el `finally` lo escribe con sesión nueva. SIGTERM → `JobTerminatedError`.
- Fila perdedora de la carrera → `failed` con prefijo `Cancelada (sin trabajo):` (`CANCELLED_PREFIX`), no borrada.
- Idempotencia por `(job_name, target_date)`, revisada otra vez con el lock tomado. `TARGET_DATE` debe ser un día cerrado.
- Zombis: `recover_stale_runs` pasa a `failed` lo de más de 3 h (`STALE_AFTER`).
- `job_runs` ≠ `reporting.pipeline_runs`: cada una con su lock.
- CSV escrito en `.tmp` y renombrado; es backup, no input; está en `.gitignore` (lleva `userId` seudonimizados).

## Gotchas

- supercronic en vez de `cron` (que borra variables de entorno y no escribe en stdout); descarga verificada por sha256.
- `services/scheduler/Dockerfile.dockerignore` limita el contexto (que es la raíz del repo).
- Test de dos procesos: crear las tablas antes de lanzarlos (`create(checkfirst=True)` no es atómico); SQLite con `timeout=30`.
