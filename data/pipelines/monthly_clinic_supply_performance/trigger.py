"""Disparo manual del pipeline desde POST /reporting/pipeline-runs.

Dos pasos separados a proposito:

1. `reserve_monthly_run` (sincrono, dentro de la peticion): valida el mes,
   comprueba con UNA lectura que no haya otra corrida viva y reserva el
   run_id. Asi la API puede responder 400 (mes invalido) o 409 (mes ocupado)
   antes de encolar nada. No escribe: crear el lock en Supabase costaba
   ~500 ms y el Ticket #DEV-55 exige el 202 en menos de 200 ms.
2. La ejecucion, tras responder 202: el router encola la tarea de Celery
   `services/tasks/pipeline_tasks.py` y el worker crea la fila `queued` con
   el run_id reservado (start_pipeline_run). El lock sigue siendo el indice
   unico parcial de reporting.pipeline_runs; si dos peticiones pasan la
   comprobacion en el mismo instante, la segunda tarea termina `cancelled`.
   Este modulo no importa la tarea: data/pipelines no depende de services/.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from typing import Optional, Tuple

from sqlmodel import Session

from data.pipelines.monthly_clinic_supply_performance import run_log
from data.process.supply_performance_transforms import resolve_month_start


def reserve_monthly_run(
    session: Session, month_start: Optional[date], today: Optional[date] = None
) -> Tuple[date, uuid.UUID]:
    """Devuelve (mes resuelto, run_id reservado). Lanza ValueError si el mes
    no es valido y run_log.WindowLockedError si ya hay una corrida viva."""
    target = resolve_month_start(month_start, today or datetime.now(timezone.utc).date())
    # Una lectura suelta no necesita transaccion. Sin AUTOCOMMIT, psycopg2
    # envia BEGIN como viaje aparte a Supabase: medido, ~170 ms -> ~113 ms.
    session.connection(execution_options={"isolation_level": "AUTOCOMMIT"})
    blocking = run_log.find_blocking_run(session, target)
    if blocking is not None:
        raise run_log.WindowLockedError(blocking.run_id)
    return target, uuid.uuid4()
