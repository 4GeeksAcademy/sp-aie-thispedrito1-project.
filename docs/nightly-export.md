# Job nocturno de telemetría — Ticket #DEV-53

Rama `feat/nightly-telemetry-export`, apilada sobre `feat/pipeline-subflows-dashboard` (2026-09-13).

Cada noche, sin intervención manual, `scripts/nightly_export.py`:

1. exporta las filas de `telemetry_events` del día anterior (UTC) a `data/raw/telemetry_YYYY-MM-DD.csv`, solo si el archivo no existe;
2. lanza el pipeline de negocio (`data/pipelines/pipeline.py`) como subproceso;
3. deja constancia en `job_runs`: `pending → processing → completed | failed`.

## Disparador

| | |
|---|---|
| Método | Contenedor scheduler dedicado (`scheduler` en `docker-compose.yml`) con [supercronic](https://github.com/aptible/supercronic) v0.2.49 |
| Expresión cron | `15 2 * * *` (02:15 UTC todos los días, `TZ=UTC`) |
| Archivo | `services/scheduler/crontab` |
| Logs | `docker compose logs -f scheduler` |

**Por qué un contenedor y no crontab del host ni un scheduler dentro de FastAPI:**

- **No comparte proceso con la API.** El ticket lo prohíbe, y además un scheduler dentro de uvicorn se duplicaría con cada worker y moriría en cada `--reload`.
- **No depende de que una máquina concreta esté encendida.** Al contrario que el crontab de un Mac, viaja con el `docker compose` del proyecto.
- **No lleva `depends_on: api`**: el job habla directamente con Supabase y funciona aunque la API esté caída.
- **supercronic en vez del `cron` de Debian**: pasa las variables de entorno del contenedor al job (cron las borra, y `DATABASE_URL` llega por `env_file`) y escribe en stdout.

**Por qué las 02:15 UTC:**

- es después de medianoche UTC, así que "ayer" ya es un día cerrado;
- deja 15 minutos de margen para eventos que el frontend manda en lote y llegan tarde;
- es antes de las 06:00 UTC, cuando `GET /reporting/pipeline-runs/latest` empieza a marcar el informe como atrasado.

## Máquina de estados y lock

- `pending`: se inserta antes de hacer ningún trabajo.
- `processing`: se actualiza antes de exportar. **Es el lock**: un índice único parcial (`uq_job_runs_single_processing`, `unique (job_name) where status = 'processing'`) impide que existan dos filas `processing` del mismo job. No es un segundo mecanismo, solo hace atómica la transición: dos instancias que consultan a la vez "¿hay alguien en processing?" pueden ver las dos "no", pero solo una consigue el `UPDATE`.
- `completed`: solo si el CSV y el pipeline terminaron bien.
- `failed`: cualquier otra salida. El estado final empieza valiendo `failed` y solo la última línea del `try` lo cambia; el `finally` lo escribe con una sesión nueva. También cubre `KeyboardInterrupt` y SIGTERM (`docker stop`), que el script convierte en `JobTerminatedError`.

Casos límite resueltos:

| Caso | Comportamiento |
|---|---|
| Ya hay una fila `processing` | Aborta sin crear fila, log `status=cancelled`, código 0 |
| Pierde la carrera por el lock | Su fila `pending` pasa a `failed` con `Cancelada (sin trabajo): ...` |
| Ya existe `completed` para `(nightly_export, target_date)` | No exporta ni lanza el pipeline, log `status=skipped`, código 0. Se vuelve a comprobar ya con el lock |
| El proceso murió sin pasar por el `finally` (kill -9, apagón) | Al arrancar, las filas `pending`/`processing` de más de 3 h pasan a `failed` ("Abandonada") |
| El CSV ya existía (reintento tras un fallo) | No se reescribe (`rows_exported` = null), el pipeline sí se lanza |
| Muere a mitad de escribir el CSV | Se escribe en `.telemetry_*.csv.tmp` y se renombra al final: nunca queda un CSV incompleto con el nombre bueno |
| `TARGET_DATE` es hoy o futuro | Error y código 2 sin tocar la base: exportar un día sin cerrar y marcarlo `completed` impediría para siempre exportarlo entero |
| El pipeline no termina | Timeout de 30 min, el hijo se mata y la fila queda `failed` |

`error_message` se redacta (direcciones de correo → `<email>`) y se recorta a 1000 caracteres.

## Qué mes recalcula el pipeline

El pipeline es mensual y rechaza el mes en curso a propósito. El job lo lanza **sin argumentos**, así que recalcula el último mes cerrado. Repetido cada noche recoge eventos tardíos, y si no hay cambios deja las filas `unchanged`. El 1 de cada mes, "ayer" es el último día del mes anterior y ese mes ya es el que se cierra. `TARGET_DATE` solo cambia el día del CSV y la clave de idempotencia, no el mes del pipeline.

## `job_runs` ≠ `reporting.pipeline_runs`

| Tabla | Quién escribe | Qué registra |
|---|---|---|
| `public.job_runs` | `scripts/nightly_export.py` | Export CSV, trigger del subproceso, lock e idempotencia por día |
| `reporting.pipeline_runs` | el subproceso del pipeline | Fases extract/transform/load, particiones, lock por mes |

Cada una tiene su propio lock (por job y por mes, respectivamente). Si el pipeline encuentra su mes bloqueado, por ejemplo por un disparo manual desde el backoffice, sale con código 1 y el job queda `failed`; la noche siguiente lo reintenta.

## Cómo probarlo

```bash
# Tests (33, sin base de datos real; incluye dos procesos reales a la vez)
services/api/.venv/bin/python -m pytest tests/jobs

# Una ejecución contra Supabase, para un día concreto
TARGET_DATE=2026-08-21 services/api/.venv/bin/python scripts/nightly_export.py

# Lock: dos instancias a la vez
TARGET_DATE=2026-08-20 services/api/.venv/bin/python scripts/nightly_export.py &
TARGET_DATE=2026-08-20 services/api/.venv/bin/python scripts/nightly_export.py; wait

# Contenedor
docker compose up -d --build scheduler
docker compose exec scheduler env TARGET_DATE=2026-08-21 python scripts/nightly_export.py
```

La tabla se crea sola al arrancar el script (`services/job_runner/migrations/001_create_job_runs.sql`, idempotente). También se puede pegar en el SQL Editor de Supabase.

## Ejemplos reales (Supabase, 2026-09-13)

**Ejecución correcta:**

```
2026-09-13T15:07:21Z INFO job=nightly_export status=starting target_date=2026-08-21 | inicio del job nocturno
2026-09-13T15:07:23Z INFO job=nightly_export status=pending target_date=2026-08-21 | ejecución registrada (run_id=0baef267-8efa-4371-8e71-308872356692)
2026-09-13T15:07:23Z INFO job=nightly_export status=processing target_date=2026-08-21 | lock tomado; empieza el trabajo (run_id=0baef267-8efa-4371-8e71-308872356692)
2026-09-13T15:07:23Z INFO job=nightly_export status=processing target_date=2026-08-21 | telemetry_2026-08-21.csv exportado con 24 fila(s)
2026-09-13T15:07:23Z INFO job=nightly_export status=processing target_date=2026-08-21 | lanzando el pipeline como subproceso
2026-09-13T15:07:37Z INFO job=nightly_export status=processing target_date=2026-08-21 | pipeline terminado con código 0
2026-09-13T15:07:37Z INFO job=nightly_export status=completed target_date=2026-08-21 | ejecución completada (run_id=0baef267-8efa-4371-8e71-308872356692)
2026-09-13T15:07:37Z INFO job=nightly_export status=completed target_date=2026-08-21 | fin del job nocturno
```

**Repetición del mismo día (idempotencia):**

```
2026-09-13T15:07:50Z INFO job=nightly_export status=starting target_date=2026-08-21 | inicio del job nocturno
2026-09-13T15:07:53Z INFO job=nightly_export status=skipped target_date=2026-08-21 | ese día ya está completed; omitido por duplicado
2026-09-13T15:07:53Z INFO job=nightly_export status=skipped target_date=2026-08-21 | fin del job nocturno
```

**Bloqueada: segunda instancia lanzada a la vez** (la primera completó normalmente):

```
2026-09-13T15:07:53Z INFO job=nightly_export status=starting target_date=2026-08-20 | inicio del job nocturno
2026-09-13T15:07:55Z INFO job=nightly_export status=pending target_date=2026-08-20 | ejecución registrada (run_id=49d8b35d-6fc7-421d-bf2c-b0b748b62448)
2026-09-13T15:07:56Z INFO job=nightly_export status=cancelled target_date=2026-08-20 | otra instancia tomó el lock primero; se aborta
2026-09-13T15:07:56Z INFO job=nightly_export status=cancelled target_date=2026-08-20 | fin del job nocturno
```

**Fallida: SIGTERM en mitad de la ejecución** (lo mismo que haría `docker stop`):

```
2026-09-13T15:08:34Z INFO job=nightly_export status=starting target_date=2026-08-19 | inicio del job nocturno
2026-09-13T15:08:35Z INFO job=nightly_export status=pending target_date=2026-08-19 | ejecución registrada (run_id=d106bcc4-794a-4adb-888e-6f3616221cf4)
2026-09-13T15:08:36Z INFO job=nightly_export status=processing target_date=2026-08-19 | lock tomado; empieza el trabajo (run_id=d106bcc4-794a-4adb-888e-6f3616221cf4)
2026-09-13T15:08:36Z INFO job=nightly_export status=processing target_date=2026-08-19 | telemetry_2026-08-19.csv exportado con 0 fila(s)
2026-09-13T15:08:36Z INFO job=nightly_export status=processing target_date=2026-08-19 | lanzando el pipeline como subproceso
2026-09-13T15:08:37Z ERROR job=nightly_export status=failed target_date=2026-08-19 | JobTerminatedError: señal 15 recibida
2026-09-13T15:08:37Z ERROR job=nightly_export status=failed target_date=2026-08-19 | el job terminó con error: JobTerminatedError: señal 15 recibida
```

**`job_runs` tras esas ejecuciones** (la repetición idempotente no crea fila):

| target_date | status | rows_exported | pipeline_exit_code | error_message |
|---|---|---|---|---|
| 2026-08-21 | completed | 24 | 0 | |
| 2026-08-20 | failed | | | Cancelada (sin trabajo): otra instancia tomó el lock primero |
| 2026-08-20 | completed | 0 | 0 | |
| 2026-08-19 | failed | 0 | | JobTerminatedError: señal 15 recibida |

**Primeras filas de `data/raw/telemetry_2026-08-21.csv`** (recortadas; `tags` va completo en el archivo):

```
id,timestamp,service,event_type,level,value,message,tags
3a427de3-3a97-46b3-87c4-36fb97250f15,2026-08-21T21:32:18.679088,api,login_succeeded,info,,,"{""eventId"": ""c6b17598-…"", ""login_method"": ""password"", …}"
20e47c72-a9e0-4da5-a681-4e7a36327bb7,2026-08-21T21:36:38.889277,api,login_failed,warn,,,"{""attempted_identifier_hash"": ""dcbffb3b…"", ""eventId"": ""9d991a49-…"", …}"
e8011862-3866-447d-b225-bc6f513ba6c1,2026-08-21T21:36:46.029614,api,login_succeeded,info,,,"{""eventId"": ""1200bcbf-…"", ""login_method"": ""password"", …}"
```

**Dentro del contenedor `scheduler`** (Python 3.12): `supercronic -test` confirma que el crontab es válido. `TARGET_DATE=2026-08-18` terminó `completed` con el pipeline real (21 s), y `TARGET_DATE=2026-08-21` dio `skipped` porque ese día ya estaba completado desde el Mac: la idempotencia vive en la base, no en la máquina.

## Antes de levantar el scheduler

`docker-compose.yml` pasa al contenedor el `.env` **de la raíz**, y `load_dotenv` no sobreescribe una variable que ya existe: si ese archivo y `services/api/.env` apuntan a bases distintas, el contenedor usa la del `.env` de la raíz. El 2026-09-13 ese archivo seguía con la `DATABASE_URL` del proyecto antiguo de Supabase (`eu-central-1`). Se corrigió ese mismo día con autorización del usuario, copiando la de `services/api/.env` (`eu-west-1`). Comprobado: el contenedor, sin variables pasadas a mano, da `skipped` para el 21 de agosto, que solo está completado en la base buena.

## Privacidad

El CSV es un backup fiel de `telemetry_events`, así que incluye los `userId` seudonimizados de `tags`. Nunca contiene emails: `login_failed` ya guarda el identificador como hash HMAC. `data/raw/telemetry_*.csv` está en `.gitignore`.
