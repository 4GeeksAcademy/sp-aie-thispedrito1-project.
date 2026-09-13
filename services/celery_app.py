"""Instancia de Celery compartida por la API (productor) y el worker (consumidor).

Ticket #DEV-55. Patrón productor/consumidor:

    Cliente → API (encola y responde 202) → Redis (broker) → worker (ejecuta) → Redis (resultado)

La API y el worker importan este mismo módulo, así que los dos apuntan
siempre al mismo Redis (`REDIS_URL`) con la misma configuración. Pero son
procesos distintos: el worker se arranca desde la raíz del repo con

    services/api/.venv/bin/celery -A services.celery_app worker --loglevel=INFO

o como servicio `worker` de docker-compose.yml. Detalle en docs/async-tasks.md.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT_DIR = Path(__file__).resolve().parents[1]
API_DIR = ROOT_DIR / "services" / "api"
# El worker no pasa por main.py: necesita las mismas rutas que la API para
# importar `data.pipelines` (raíz del repo) y `database` (services/api).
for path in (str(ROOT_DIR), str(API_DIR)):
    if path not in sys.path:
        sys.path.insert(0, path)

# Dev nativo: REDIS_URL vive en services/api/.env junto a DATABASE_URL.
# load_dotenv no sobreescribe variables ya definidas, así que en Docker
# manda el valor de docker-compose.yml (redis://redis:6379/0).
load_dotenv(API_DIR / ".env")

import logging  # noqa: E402

from celery import Celery  # noqa: E402

# Mismo motivo que en main.py: Prefect habla con su API efímera por httpx, que
# loguea cada petición a INFO. En el worker eran ~40 líneas "HTTP Request" por
# corrida, que tapaban las de task_retry/task_failed.
logging.getLogger("httpx").setLevel(logging.WARNING)

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

# Límite duro de una tarea. El pipeline mensual tarda segundos con los datos
# actuales; con reintentos internos de Prefect (hasta ~2 min en la
# extracción) sigue muy por debajo. Un worker colgado no puede bloquear el
# pool para siempre (regla 3 del patrón).
TASK_SOFT_TIME_LIMIT_SECONDS = 15 * 60
TASK_TIME_LIMIT_SECONDS = 20 * 60

celery_app = Celery(
    "healthcore",
    broker=REDIS_URL,
    backend=REDIS_URL,
    include=["services.tasks.pipeline_tasks"],
)

celery_app.conf.update(
    # Solo JSON: los mensajes llevan identificadores (run_id, mes), nunca
    # objetos Python serializados con pickle ni lotes de datos.
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    # Sin esto Celery nunca publica el estado STARTED y GET /tasks/{id}
    # pasaría directamente de pending a success.
    task_track_started=True,
    # Regla 2 del patrón, ACK solo tras éxito: el mensaje se confirma cuando
    # la tarea termina, no cuando el worker lo recoge. Si el worker muere a
    # mitad, Redis lo vuelve a entregar.
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    # Un mensaje cada vez por proceso: con acks tardíos, reservar varios
    # dejaría encargos retenidos por un worker ocupado en una tarea larga.
    worker_prefetch_multiplier=1,
    task_soft_time_limit=TASK_SOFT_TIME_LIMIT_SECONDS,
    task_time_limit=TASK_TIME_LIMIT_SECONDS,
    broker_transport_options={
        # Con acks tardíos, Redis reentrega un mensaje no confirmado pasado
        # este tiempo. Tiene que superar de sobra el límite duro de la tarea,
        # o una corrida larga pero sana se ejecutaría dos veces.
        "visibility_timeout": 2 * 60 * 60,
        # La API no puede quedarse colgada si Redis no responde: falla
        # rápido y POST /reporting/pipeline-runs responde 503.
        "socket_connect_timeout": 3,
    },
    result_backend_transport_options={"socket_connect_timeout": 3},
    broker_connection_retry_on_startup=True,
    # Los resultados son resúmenes pequeños; a las 24 h Redis los borra.
    result_expires=24 * 60 * 60,
    # Flower: sin estos eventos no ve las tareas encoladas ni en curso.
    worker_send_task_events=True,
    task_send_sent_event=True,
)

# Alias corto para `celery -A services.celery_app`, que busca `app` o `celery`.
app = celery_app
