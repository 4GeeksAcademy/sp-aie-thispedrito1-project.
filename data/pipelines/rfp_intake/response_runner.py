"""Ejecuta la Parte 2 para un ticket: lee el handoff de la Parte 1, corre el
grafo `rfp_response` y persiste borradores + EvaluationResult.

Entrada (contrato de docs/rfp-intake.md): ticket `analisis_completo` con
`next_stage = generando_borrador` y `handoff_payload` v1. Nunca relee el PDF.

Progreso en tiempo real: el grafo avisa (`on_progress`) desde los hilos de
cada departamento y `TicketProgress` lo escribe con una sesión propia por
aviso, en serie (un candado). Un fallo al escribir progreso solo se registra:
el resultado lo decide `save_response` al final, en una transacción.

Estado final del ticket:
- `evaluacion_completa`: las tres secciones aprobaron los tres evaluadores.
- `needs_human_review`: al menos una agotó el límite (o no era arreglable).
  Sus borradores van igual en el handoff, marcados `provisional`.
En ambos casos `next_stage = esperando_aprobación` (bandera para la Parte 3).
"""

from __future__ import annotations

import logging
import threading
import time
import uuid
from typing import Any, Dict, List

from sqlmodel import Session

from data.pipelines.rfp_intake import repository
from data.pipelines.rfp_intake.agents import LlmCall, call_model
from data.pipelines.rfp_intake.departments import DEPARTMENT_IDS, DEPARTMENTS
from data.pipelines.rfp_intake.models import (
    NEXT_STAGE_APPROVAL,
    SECTION_DRAFTING,
    SECTION_EVALUATING,
    SECTION_NEEDS_REVIEW,
    STATUS_COMPLETE,
    STATUS_DRAFTING,
    STATUS_EVALUATED,
    STATUS_EVALUATING,
    STATUS_NEEDS_REVIEW,
)
from data.pipelines.rfp_intake.response_graph import (
    DEFAULT_MAX_ITERATIONS,
    EVENT_DRAFTING,
    EVENT_EVALUATED,
    EVENT_EVALUATING,
    compile_response_graph,
)
from data.pipelines.rfp_intake.runner import HANDOFF_VERSION

logger = logging.getLogger("healthcore.rfp_intake")

RESPONSE_HANDOFF_VERSION = 1
# Estados desde los que la Parte 2 puede (re)empezar: el handoff listo, o un
# intento anterior que Celery está reintentando.
STARTABLE_STATUSES = (STATUS_COMPLETE, STATUS_DRAFTING, STATUS_EVALUATING)


class HandoffError(ValueError):
    """El ticket no trae un handoff de la Parte 1 que esta versión entienda."""


class TicketProgress:
    """`on_progress` del grafo → filas de Supabase."""

    def __init__(self, engine: Any, ticket_id: uuid.UUID) -> None:
        self._engine = engine
        self._ticket_id = ticket_id
        self._lock = threading.Lock()

    def __call__(self, department_id: str, event: str, data: Dict[str, Any]) -> None:
        if event == EVENT_DRAFTING:
            fields: Dict[str, Any] = {"draft_status": SECTION_DRAFTING}
        elif event == EVENT_EVALUATING:
            fields = {"draft_status": SECTION_EVALUATING, "draft": data["draft"], "ticket_status": STATUS_EVALUATING}
        elif event == EVENT_EVALUATED:
            fields = {"draft_status": SECTION_EVALUATING, "evaluation": data["evaluation"]}
        else:
            return
        try:
            with self._lock, Session(self._engine) as session:
                repository.record_section_progress(
                    session, self._ticket_id, department_id, iteration=data["iteration"], **fields
                )
        except Exception as exc:  # noqa: BLE001 — el progreso no debe tumbar la generación
            logger.warning(
                "rfp_response_progress_write_failed ticket=%s department=%s error_type=%s",
                self._ticket_id, department_id, type(exc).__name__,
            )


def run_response(
    session: Session,
    ticket_id: uuid.UUID,
    *,
    llm: LlmCall = call_model,
    max_iterations: int = DEFAULT_MAX_ITERATIONS,
) -> str:
    """Devuelve el estado final. Si el ticket no está en un estado de
    arranque (ya terminó, se descartó…), no hace nada: idempotente."""
    ticket = repository.get_ticket(session, ticket_id)
    if ticket is None:
        raise LookupError(f"Ticket {ticket_id} no existe")
    if ticket.status not in STARTABLE_STATUSES or not ticket.handoff_payload:
        logger.info("rfp_response_skipped ticket=%s status=%s", ticket_id, ticket.status)
        return ticket.status
    handoff = ticket.handoff_payload
    if handoff.get("handoff_version") != HANDOFF_VERSION or not handoff.get("workstreams"):
        raise HandoffError(f"handoff_version {handoff.get('handoff_version')!r} no soportado")

    repository.start_drafting(session, ticket)
    started = time.monotonic()
    graph = compile_response_graph(
        llm=llm, on_progress=TicketProgress(session.get_bind(), ticket_id), max_iterations=max_iterations
    )
    state = graph.invoke(
        {"ticket_id": str(ticket_id), "handoff": handoff, "max_iterations": max_iterations, "sections": []}
    )
    sections = sorted(state["sections"], key=lambda s: DEPARTMENT_IDS.index(s["department_id"]))
    status = STATUS_NEEDS_REVIEW if any(s["status"] == SECTION_NEEDS_REVIEW for s in sections) else STATUS_EVALUATED

    repository.save_response(
        session,
        ticket,
        status=status,
        sections=sections,
        handoff=build_response_handoff(ticket, handoff, sections, status, max_iterations),
        requires_compliance_review=any(s["evaluation"]["compliance"]["contains_phi"] for s in sections),
    )
    logger.info(
        "rfp_response_finished ticket=%s status=%s iterations=%s duration_ms=%.0f",
        ticket_id, status, ",".join(f"{s['department_id']}:{s['iterations']}" for s in sections),
        (time.monotonic() - started) * 1000,
    )
    return status


def build_response_handoff(
    ticket: Any, source: Dict[str, Any], sections: List[Dict[str, Any]], status: str, max_iterations: int
) -> Dict[str, Any]:
    """Contrato de handoff v1 hacia la Parte 3 (docs/rfp-response.md): por
    cada departamento, el contenido generado y su EvaluationResult."""
    metadata = source["metadata"]
    return {
        "handoff_version": RESPONSE_HANDOFF_VERSION,
        "ticket_id": str(ticket.ticket_id),
        "rfp_id": ticket.rfp_id,
        "ticket_status": status,
        "next_stage": NEXT_STAGE_APPROVAL,
        "client_name": metadata.get("client_name"),
        "client_country": metadata.get("client_country"),
        "currency": metadata.get("currency"),
        "max_iterations": max_iterations,
        "sections": [
            {
                "department_id": s["department_id"],
                "owner": DEPARTMENTS[s["department_id"]].owner,
                "draft_status": s["status"],
                # Provisional = Ventas no debe usarlo sin revisarlo (pregunta de diseño 3).
                "provisional": s["status"] == SECTION_NEEDS_REVIEW,
                "iterations": s["iterations"],
                "draft_content": s["draft_content"],
                "evaluation_result": s["evaluation"],
            }
            for s in sections
        ],
    }
