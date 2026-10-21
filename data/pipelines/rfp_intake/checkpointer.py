"""Checkpointer del grafo de aprobación (Hito 9, Parte 3): Postgres en Supabase.

Por qué Postgres y no memoria ni SQLite (decisión del usuario, 2026-10-21):
la pausa de un departamento puede durar días, y la reanudación la ejecuta un
worker de Celery que no es el proceso que pausó el grafo (ni siquiera el
mismo contenedor). El estado tiene que vivir en la misma base que los
tickets, no en la RAM ni en un archivo de un disco concreto.

Identidad: un hilo por ticket, `rfp-{ticket_id}` (README: "tickets
concurrentes no deben compartir checkpoint"). Las ramas de cada departamento
son tareas del mismo hilo; LangGraph las separa por su `checkpoint_ns`.

Gotcha del pooler de Supabase (puerto 6543, modo transacción): no admite
prepared statements entre transacciones, y psycopg 3 los usa a partir de la
quinta ejecución de una misma consulta. `prepare_threshold=None` los
desactiva; sin eso, la sexta escritura de checkpoint falla con
"prepared statement ... does not exist".

Usa psycopg 3 (lo exige langgraph-checkpoint-postgres); el resto de la API
sigue con psycopg2 a través de SQLAlchemy. Misma `DATABASE_URL`.
"""

from __future__ import annotations

import os
import threading
import uuid
from contextlib import contextmanager
from typing import Iterator, Optional, Union

THREAD_PREFIX = "rfp-"

_setup_done = False
_setup_lock = threading.Lock()


class CheckpointerConfigError(RuntimeError):
    """Sin `DATABASE_URL` de Postgres no hay dónde guardar la pausa."""


def thread_id_for(ticket_id: Union[uuid.UUID, str]) -> str:
    return f"{THREAD_PREFIX}{ticket_id}"


def thread_config(ticket_id: Union[uuid.UUID, str]) -> dict:
    return {"configurable": {"thread_id": thread_id_for(ticket_id)}}


def _psycopg_url(database_url: str) -> str:
    """SQLAlchemy acepta `postgresql+psycopg2://`; psycopg no."""
    scheme, _, rest = database_url.partition("://")
    return f"{scheme.split('+', 1)[0]}://{rest}"


@contextmanager
def open_checkpointer(database_url: Optional[str] = None) -> Iterator["object"]:
    """Un `PostgresSaver` con su propia conexión, cerrada al salir. Crea las
    tablas `checkpoint*` la primera vez que se usa en el proceso."""
    global _setup_done
    url = database_url or os.getenv("DATABASE_URL", "")
    if not url.startswith(("postgresql", "postgres")):
        raise CheckpointerConfigError("El checkpointer de la Parte 3 necesita DATABASE_URL de Postgres (Supabase).")

    from langgraph.checkpoint.postgres import PostgresSaver
    from psycopg import Connection
    from psycopg.rows import dict_row

    with Connection.connect(
        _psycopg_url(url), autocommit=True, prepare_threshold=None, row_factory=dict_row
    ) as connection:
        saver = PostgresSaver(connection)
        with _setup_lock:
            if not _setup_done:
                saver.setup()  # idempotente: migraciones propias de LangGraph
                _setup_done = True
        yield saver
