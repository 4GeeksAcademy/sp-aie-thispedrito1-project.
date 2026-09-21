# Cola de tareas asíncronas con Redis y Celery — Ticket #DEV-55

Rama `feat/async-task-queue`, apilada sobre `feat/nightly-telemetry-export` (2026-09-13).

## Qué cambia

Hasta ahora, `POST /reporting/pipeline-runs` (recalcular el informe mensual con el pipeline de Prefect) respondía 202,
pero ejecutaba el flow con `BackgroundTasks` **dentro del proceso de FastAPI**. Si la API se reiniciaba a mitad, la
corrida moría con ella. Ahora la API solo encola y un worker de Celery, en otro proceso, lo ejecuta:

```
Cliente → API (productor) → Redis (broker) → worker de Celery (consumidor) → Redis (resultado)
             │                                        │
             └─ 202 {"task_id", "run_id"}             ├─ reporting.pipeline_runs (una fila por intento)
                                                      └─ task_dead_letters (si agota los reintentos)
```

### Por qué este endpoint

- Es la operación más pesada de la API: lee todos los eventos del mes de Supabase, calcula 4 KPIs con Pandas y
  escribe en `reporting.*`. Medido contra Supabase real: **3 a 11 s** por ejecución, frente a los ~120 ms del 202.
- Ya era asíncrono "de palabra" pero corría en el proceso de la API, que es justo lo que el ticket prohíbe.
- Encaja con la regla de mensajes ligeros: el mensaje lleva solo `month_start`, `run_id` y `triggered_by`. El worker
  lee él mismo los eventos.
- El pipeline ya era idempotente (recalcula la ventana completa y sustituye), así que reintentar es seguro.

## Mapa del código

| Archivo | Qué hace |
|---|---|
| `services/celery_app.py` | Instancia de Celery compartida por API y worker: `REDIS_URL` como broker y result backend, JSON, `acks_late`, `track_started`, timeouts, eventos para Flower. |
| `services/tasks/pipeline_tasks.py` | `ObservableTask` (log por ejecución + DLQ en `on_failure`), la tarea `reporting.run_monthly_clinic_supply_performance` y `enqueue_monthly_pipeline_run` (productor). |
| `services/tasks/dead_letters.py` | Tabla `task_dead_letters` y `record_dead_letter` (idempotente por `task_id`). |
| `services/tasks/status.py` | `read_task_snapshot` (lee Redis) y `to_public_status` (estados de Celery → los 4 del ticket). |
| `services/tasks/router.py` + `schemas.py` | `GET /tasks/{task_id}`. |
| `services/tasks/redaction.py` | Enmascara correos en nuestros logs, en los de Celery (`celery.app.trace`) y en la DLQ. |
| `data/pipelines/monthly_clinic_supply_performance/trigger.py` | `reserve_monthly_run`: valida el mes y comprueba el lock con una sola lectura. |
| `services/worker/Dockerfile` | Imagen del worker y de Flower. |
| `docker-compose.yml` | Servicios `redis`, `worker` y `flower`. `api` monta ahora `services/` y `data/` completos. |

## Cómo levantarlo y pararlo

### Con Docker (lo que pide el ticket)

```bash
docker compose up -d --build redis worker flower     # broker, consumidor y monitor
docker compose up -d --build api                     # productor (si no la corres en nativo)
docker compose logs -f worker                        # ver task_started / task_retry / task_failed
open http://localhost:5555                           # Flower

docker compose stop worker flower redis              # parar (warm shutdown: el worker acaba la tarea en curso)
docker compose down -v                               # borrar contenedores y el volumen de Redis
```

Ningún servicio lleva `restart:`: es un entorno de clase y nada debe quedarse corriendo solo.

### En nativo (desarrollo en el Mac, lo usado en la verificación)

Redis sigue en Docker; API, worker y Flower son procesos del venv. Desde la **raíz del repo**:

```bash
docker compose up -d redis
services/api/.venv/bin/celery -A services.celery_app worker --loglevel=INFO --concurrency=1   # terminal 1
services/api/.venv/bin/celery -A services.celery_app flower --port=5555                        # terminal 2
cd services/api && .venv/bin/uvicorn main:app --reload --port 8000                             # terminal 3
```

Para parar el worker, `Ctrl+C` una vez (parada en caliente: termina la tarea en curso). Dos veces la corta.
`REDIS_URL` es opcional en nativo: por defecto usa `redis://localhost:6379/0`.

## Contratos

### `POST /reporting/pipeline-runs` (solo admin)

- **202** `{"task_id", "run_id", "status": "queued", "month_start"}`. Nuevo: `task_id`.
- **400** mes inválido o mes en curso · **409** el mes ya tiene una corrida viva (con su `run_id`) · **503** Redis no responde.

### `GET /tasks/{task_id}` (solo admin, igual que quien puede encolar)

`{"task_id", "status", "result", "error"}`. `result` solo en `success`; `error` (`{type, message}`) en `failure` y
mientras espera un reintento. `task_id` que no es UUID → 422.

| Estado de Celery | `status` | Motivo |
|---|---|---|
| `PENDING`, `RECEIVED` | `pending` | En cola o recibida sin empezar. |
| `STARTED` | `started` | Ejecutándose (`task_track_started=True`). |
| `RETRY` | `started` | El trabajo ya empezó y no ha terminado. Nunca `failure`: el cliente dejaría de preguntar antes de un posible éxito. |
| `SUCCESS` | `success` | |
| `FAILURE`, `REVOKED`, `REJECTED`, `IGNORED` | `failure` | No va a ejecutarse más: el cliente debe dejar de esperar. |
| cualquier otro | `pending` | Nunca devolver un valor fuera del contrato. |

## Reintentos y Dead Letter Queue

- `autoretry_for=(Exception,)`, `max_retries=3` literal: **1 ejecución + 3 reintentos = 4 ejecuciones**.
- Backoff exponencial `10 * 2**reintento` → **10 s, 20 s, 40 s**, con `retry_jitter=False`. Por defecto Celery sortea la
  espera entre 0 y el tope, y un reintento podría salir inmediato.
- Tras el cuarto fallo, `ObservableTask.on_failure` inserta en `task_dead_letters`: `task_id`, `task_name`,
  `attempt`, `error_type`, `error_message` (hasta 4000 caracteres, correos enmascarados), `task_kwargs` y `failed_at`.
  `task_id` es único: un aviso duplicado no crea otra fila. Si la base de datos no responde, el fallo queda en el log
  como `dead_letter_write_failed` con todos los campos.
- **No se reintenta:** `SoftTimeLimitExceeded` (15 min), que va directo a la DLQ con `attempt=1`.
- **No es un fallo:** si el mes está ocupado, Prefect lanza `CancelledRun` y la tarea termina `success` con
  `{"status": "cancelled", "reason": ...}`. Sin esa rama, autoretry lo habría mandado a la DLQ tras 3 reintentos
  (lo detectó un test).
- Cada intento tiene su propia fila en `reporting.pipeline_runs`: el primero usa el `run_id` reservado por la API; los
  reintentos crean otra, porque la fallida ya soltó el lock y otra corrida pudo tomarlo durante la espera.

## Observabilidad

Cada ejecución deja una línea con `task_id`, intento, estado y duración; los fallos, además, el error completo.
Fragmento real de la ejecución con reintentos (completo en `docs/async-tasks/retry-run.log`):

```
18:44:04 task_started  task_id=dde9a57e-… attempt=1
18:44:08 task_retry    task_id=dde9a57e-… attempt=1 status=retry duration_ms=4584.9 next_retry_in_s=10 error_type=RuntimeError error=Failed to reach API at http://127.0.0.1:9/api/
18:44:18 task_started  task_id=dde9a57e-… attempt=2
18:44:20 task_retry    task_id=dde9a57e-… attempt=2 status=retry duration_ms=1576.7 next_retry_in_s=20 …
18:44:40 task_started  task_id=dde9a57e-… attempt=3
18:44:41 task_retry    task_id=dde9a57e-… attempt=3 status=retry duration_ms=1543.6 next_retry_in_s=40 …
18:45:21 task_started  task_id=dde9a57e-… attempt=4
18:45:23 task_failed   task_id=dde9a57e-… attempt=4 status=failure duration_ms=1524.8 error_type=RuntimeError error=Failed to reach API at http://127.0.0.1:9/api/
18:45:23 task_dead_lettered task_id=dde9a57e-… attempt=4 error_type=RuntimeError
```

Capturas de Flower: `docs/async-tasks/flower-tasks.jpg` (tareas `SUCCESS` y una `FAILURE`) y
`docs/async-tasks/flower-dlq-task.jpg` (detalle de la tarea fallida: `Retries 3`, excepción, worker).

## Decisiones que conviene no romper

- **La API comprueba, el worker bloquea.** Crear el lock (`create_queued_run`) en la petición costaba **~520 ms**
  (varios viajes a Supabase en Irlanda, ~60 ms cada uno); publicar en Redis, **2-4 ms**. Con la decisión del
  usuario, la API hace una sola lectura (`run_log.find_blocking_run`) para el 409, reserva el `run_id` y encola. El
  worker crea la fila con ese id (`start_pipeline_run`). El índice único parcial sigue siendo el lock: si dos
  peticiones pasan la comprobación a la vez, la segunda tarea termina `cancelled`, nunca hay dos corridas.
- **`AUTOCOMMIT` en esa lectura:** psycopg2 envía `BEGIN` como viaje aparte. Medido: ~170 ms → ~113 ms.
- **Una corrida caducada (sin heartbeat 30 min) no bloquea**, igual que en `create_queued_run` (`run_log.is_stale`).
- **`services/tasks/pipeline_tasks.py` no importa Prefect a nivel de módulo:** la API lo importa para encolar y no
  debe cargar Prefect al arrancar.
- **`data/pipelines` no importa `services/`:** el router encola; `trigger.py` solo reserva.
- **Solo JSON** en los mensajes (`accept_content=["json"]`), nunca pickle.
- **`visibility_timeout` (2 h) > `task_time_limit` (20 min):** con `acks_late`, un valor menor haría que Redis
  reentregara una corrida sana y se ejecutara dos veces.

## Verificación real (2026-09-13)

Redis 7.4 en Docker; API, worker y Flower en nativo con el venv (Python 3.9), contra Supabase real. Usuario admin
temporal en una TinyDB aparte (la base real no tiene admins); token generado sin pasar por login para no meter
eventos de prueba en `telemetry_events`.

- Worker: `Connected to redis://localhost:6379/0`, tarea registrada. `CONFIG GET maxmemory-policy` → `noeviction`.
- **202 en ~120 ms** en caliente (5 mediciones: 134, 118, 209, 120, 120 ms). La primera petición tras varios minutos
  sin tráfico tardó 447 ms: reabre la conexión TLS con Supabase. Antes del cambio de diseño: 497-631 ms.
- Ciclo feliz: `started` → `success` en 10 s; mismos KPIs de agosto que la Parte 3 (`unchanged: 2`).
  `pipeline-runs/latest` devuelve el mismo `run_id` que el 202.
- 409 con el `run_id` de la fila creada por el worker.
- **API parada, worker vivo:** con el worker parado se encoló julio (`LLEN celery` = 1, `GET /tasks` → `pending`); se
  apagó la API; al arrancar el worker la tarea terminó `success` y la cola quedó a 0.
- **Fallo y DLQ:** worker de demo con el orquestador de Prefect inaccesible (`PREFECT_API_URL=http://127.0.0.1:9/api`,
  `PREFECT_CLIENT_MAX_RETRIES=0`), sin tocar datos ni código. `GET /tasks` visto cada segundo: `started` (con error
  entre intentos), reintentos a los 14, 35 y 77 s, `failure` a los 79 s. Fila en `task_dead_letters` con `attempt=4`.
- Limpieza: borradas de `reporting.pipeline_runs` las 11 corridas de verificación de meses sin datos (sin
  particiones asociadas) y las 3 filas del script de perfilado de latencia; agosto queda como última corrida y la
  fila de la DLQ se conserva como evidencia. Redis, su volumen y su imagen, eliminados.

**No verificado en esta sesión:** construir la imagen `services/worker/Dockerfile` y arrancar `worker`/`flower` dentro
de Docker. Con 2,8 GB libres en disco no se construyó sin permiso. `docker compose config` resuelve bien los tres
servicios. Para comprobarlo: `docker compose up -d --build redis worker flower`.

**Tests:** API 167/167 (136 previos + 31: cola, DLQ, `GET /tasks`, reserva del lock y carrera), cobertura de
`services/tasks` 94-100 %; `tests/jobs` + `tests/pipelines` 43/43; jest 85/85 y `tsc` limpio (sin cambios de frontend).

## Límites conocidos

- Un `task_id` desconocido devuelve `pending`: Celery no distingue "en cola" de "nunca existió". Pasadas 24 h
  (`result_expires`) una tarea terminada también vuelve a verse `pending`; su rastro permanente es `pipeline_runs` y
  la DLQ.
- El límite **duro** (20 min) mata el proceso hijo y Celery no llama a `on_failure`: esa tarea no llegaría a la DLQ.
  El blando (15 min) salta antes y sí pasa por ella.
- Flower no tiene login en desarrollo y muestra los `kwargs` (ids de usuario seudónimos): solo en `127.0.0.1`.
- Durante los segundos entre el 202 y que el worker recoge la tarea, `pipeline-runs/latest` aún no muestra la corrida;
  `GET /tasks` sí (`pending`).

## Borrador del cuerpo del PR

> Etiqueta: `async-tasks`

**Endpoint convertido:** `POST /reporting/pipeline-runs`. Es el recálculo del informe mensual con Prefect (3-11 s
contra Supabase) y hasta ahora corría con `BackgroundTasks` dentro del proceso de FastAPI. El mensaje solo lleva
`month_start`, `run_id` y `triggered_by`.

**Qué incluye:** Redis (`noeviction`), worker y Flower en `docker-compose.yml`; `services/celery_app.py`; tarea con
`max_retries=3` y backoff 10/20/40 s sin jitter; DLQ en `task_dead_letters`; `GET /tasks/{task_id}` con los 4 estados;
logs con `task_id`, intento, estado y duración (correos enmascarados también en los logs de Celery).

**Captura de Flower:** `docs/async-tasks/flower-tasks.jpg` y `docs/async-tasks/flower-dlq-task.jpg`.

**Log de una ejecución con reintento:** ver el bloque de "Observabilidad" (completo en `docs/async-tasks/retry-run.log`).

**Cambio de diseño a revisar:** el lock del mes lo crea ahora el worker; la API solo lee (el 202 pasó de ~500 ms a
~120 ms). Dos peticiones simultáneas reciben 202 y la segunda tarea termina `cancelled`.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
