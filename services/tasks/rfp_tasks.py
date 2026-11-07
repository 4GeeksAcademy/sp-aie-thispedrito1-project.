"""Tareas asíncronas del flujo de RFPs (Hito 9, Partes 1 y 2).

`POST /rfp-tickets` crea el ticket `analizando`, guarda el PDF y encola esta
tarea; el worker de Celery ejecuta el grafo `rfp_intake` (5 llamadas al
modelo, 1-2 minutos) fuera del proceso de la API.

Cómo el ticket sigue diciendo la verdad si algo falla (pregunta de diseño 5):
- Durante los reintentos sigue `analizando`: todavía puede terminar bien.
- Fallo definitivo (reintentos agotados o timeout): `on_failure` lo pasa a
  `error` con un motivo legible y la tarea queda en la DLQ (ObservableTask).
- Worker muerto sin llegar a `on_failure`: la API lo detecta al consultar
  (`repository.recover_stale`, 30 min sin novedades) y lo pasa a `error`.
- Un `error` se reprocesa con scripts/reprocess_rfp.py.

Recibe solo el `ticket_id` (JSON): el worker lee el PDF del disco y el
ticket de Supabase. Ningún contenido de la RFP viaja por Redis.

Parte 2 (decisión del usuario, 2026-10-21: arranque automático): cuando la
Parte 1 termina en `analisis_completo`, la propia tarea encola
`rfp_response.generate_ticket`, que lee el handoff de Supabase. Si Redis
falla en ese instante, el ticket queda `analisis_completo` con
`next_stage = generando_borrador` y se relanza con
scripts/generate_rfp_response.py. Sus fallos siguen el mismo camino que los
de la Parte 1 (reintentos → `error` + DLQ).
"""

from __future__ import annotations

import logging
import uuid
from typing import Any, Dict

from celery import Task
from celery.exceptions import SoftTimeLimitExceeded
from sqlmodel import Session

from data.pipelines.rfp_intake.conversion import ConversionError
from data.pipelines.rfp_intake.response_runner import HandoffError
from services.celery_app import celery_app
from services.tasks.pipeline_tasks import ObservableTask, _engine

logger = logging.getLogger("healthcore.tasks")

RFP_TASK_NAME = "rfp_intake.process_ticket"
RFP_RESPONSE_TASK_NAME = "rfp_response.generate_ticket"
# Cada intento repite 5 llamadas al modelo: 2 reintentos bastan para un pico
# del proxy sin multiplicar el coste de un fallo persistente.
RFP_MAX_RETRIES = 2
RFP_RETRY_BACKOFF_SECONDS = 15
# La Parte 2 hace hasta 2 llamadas × 3 iteraciones × 3 departamentos: un solo
# reintento cubre un pico del proxy sin duplicar un fallo persistente.
RFP_RESPONSE_MAX_RETRIES = 1


def _error_message(exc: BaseException, stage: str = "el análisis") -> str:
    """Motivo para la UI, sin el mensaje crudo del proveedor (podría repetir
    contenido de la petición)."""
    from data.pipelines.rfp_intake.agents import AgentError

    if isinstance(exc, ConversionError):
        return f"No se pudo leer el PDF: {exc}"
    if isinstance(exc, AgentError):
        return f"El modelo no respondió correctamente ({exc})."
    if isinstance(exc, HandoffError):
        return "El análisis de la Parte 1 no tiene un handoff válido. Reprocesar el análisis."
    if isinstance(exc, SoftTimeLimitExceeded):
        return f"{stage[0].upper()}{stage[1:]} superó el tiempo máximo."
    return f"Error técnico durante {stage} ({type(exc).__name__})."


class RfpIntakeTask(ObservableTask):
    stage = "el análisis"

    def on_failure(self, exc: BaseException, task_id: str, args: Any, kwargs: Any, einfo: Any) -> None:
        super().on_failure(exc, task_id, args, kwargs, einfo)  # DLQ + log
        ticket_id = kwargs.get("ticket_id")
        if not ticket_id:
            return
        from data.pipelines.rfp_intake import repository

        try:
            with Session(_engine()) as session:
                repository.mark_error(session, uuid.UUID(ticket_id), _error_message(exc, self.stage))
        except Exception as db_error:  # noqa: BLE001 — recover_stale lo cerrará al consultar
            logger.critical("rfp_ticket_error_write_failed ticket=%s db_error_type=%s", ticket_id, type(db_error).__name__)


@celery_app.task(
    bind=True,
    base=RfpIntakeTask,
    name=RFP_TASK_NAME,
    autoretry_for=(Exception,),
    # PDF ilegible: repetir no lo arregla. Timeout: ya se gastó el tiempo.
    # Los dos van directos a on_failure → `error`.
    dont_autoretry_for=(SoftTimeLimitExceeded, ConversionError),
    max_retries=RFP_MAX_RETRIES,
    retry_backoff=RFP_RETRY_BACKOFF_SECONDS,
    retry_jitter=False,
)
def process_rfp_ticket(self: Task, ticket_id: str) -> Dict[str, Any]:
    from data.pipelines.rfp_intake.runner import run_intake

    from data.pipelines.rfp_intake.models import STATUS_COMPLETE

    with Session(_engine()) as session:
        status = run_intake(session, uuid.UUID(ticket_id))
    if status == STATUS_COMPLETE:
        _chain_response(ticket_id)
    return {"ticket_id": ticket_id, "status": status}


def _chain_response(ticket_id: str) -> None:
    """Handoff a la Parte 2. Fallar aquí no debe repetir la Parte 1 (ya
    guardada): se registra y el ticket espera al reproceso manual."""
    try:
        enqueue_rfp_response(uuid.UUID(ticket_id))
    except Exception as exc:  # noqa: BLE001 — Redis caído justo ahora
        logger.error("rfp_response_enqueue_failed ticket=%s error_type=%s", ticket_id, type(exc).__name__)


def enqueue_rfp_ticket(ticket_id: uuid.UUID) -> str:
    """retry=False: si Redis no responde, la API lo sabe ya (503)."""
    return process_rfp_ticket.apply_async(kwargs={"ticket_id": str(ticket_id)}, retry=False).id


class RfpResponseTask(RfpIntakeTask):
    stage = "la generación de borradores"


@celery_app.task(
    bind=True,
    base=RfpResponseTask,
    name=RFP_RESPONSE_TASK_NAME,
    autoretry_for=(Exception,),
    # Handoff inválido: repetir no lo arregla (va directo a `error`).
    dont_autoretry_for=(SoftTimeLimitExceeded, HandoffError),
    max_retries=RFP_RESPONSE_MAX_RETRIES,
    retry_backoff=RFP_RETRY_BACKOFF_SECONDS,
    retry_jitter=False,
)
def generate_rfp_response(self: Task, ticket_id: str) -> Dict[str, Any]:
    from data.pipelines.rfp_intake import repository
    from data.pipelines.rfp_intake.response_runner import run_response

    with Session(_engine()) as session:
        ticket = repository.get_ticket(session, uuid.UUID(ticket_id))
        if ticket is not None and self.request.id:
            ticket.response_task_id = self.request.id
            session.add(ticket)
            session.commit()
        status = run_response(session, uuid.UUID(ticket_id))
    return {"ticket_id": ticket_id, "status": status}


def enqueue_rfp_response(ticket_id: uuid.UUID) -> str:
    return generate_rfp_response.apply_async(kwargs={"ticket_id": str(ticket_id)}, retry=False).id
