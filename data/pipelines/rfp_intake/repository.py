"""Acceso a las tablas `rfp_*` (Supabase). Única capa que escribe en ellas.

Escrituras del resultado en UNA transacción (`save_result`): el ticket pasa
a su estado final a la vez que se guardan metadatos y secciones. Si algo
falla a mitad, no queda un ticket `analisis_completo` sin secciones.
"""

from __future__ import annotations

import secrets
import uuid
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlmodel import Session, delete, select

from data.pipelines.rfp_intake.models import (
    APPROVAL_DECISION_RECORDED,
    APPROVAL_PENDING,
    APPROVAL_REVISING,
    DECISION_REQUEST_CHANGES,
    IN_PROGRESS_STATUSES,
    NEXT_STAGE_APPROVAL,
    SECTION_PENDING,
    STATUS_ANALYZING,
    STATUS_AWAITING_APPROVAL,
    STATUS_COMPLETE,
    STATUS_DISCARDED,
    STATUS_DRAFTING,
    STATUS_ERROR,
    STATUS_FINISHED,
    RfpDepartmentSection,
    RfpFinalDocument,
    RfpMetadata,
    RfpTicket,
    RfpTraceEvent,
    utcnow,
)

# Un ticket `analizando` sin novedades durante este tiempo se considera
# huérfano (worker muerto sin llegar a registrar el fallo). Muy por encima
# de una corrida normal (~1-2 min con 5 llamadas al modelo) y del límite de
# la tarea de Celery (15 min).
STALE_AFTER = timedelta(minutes=30)
ERROR_MESSAGE_MAX = 500


def new_rfp_id() -> str:
    """Identificador legible para Revenue Cycle: RFP-20261019-3F9A2C."""
    return f"RFP-{utcnow():%Y%m%d}-{secrets.token_hex(3).upper()}"


def create_ticket(session: Session, *, ticket_id: uuid.UUID, raw_pdf_path: str, created_by: str) -> RfpTicket:
    ticket = RfpTicket(ticket_id=ticket_id, rfp_id=new_rfp_id(), raw_pdf_path=raw_pdf_path, created_by=created_by)
    session.add(ticket)
    session.commit()
    session.refresh(ticket)
    return ticket


def get_ticket(session: Session, ticket_id: uuid.UUID) -> Optional[RfpTicket]:
    return session.get(RfpTicket, ticket_id)


def list_tickets(session: Session, *, limit: int = 50) -> List[Tuple[RfpTicket, Optional[RfpMetadata]]]:
    rows = session.exec(
        select(RfpTicket, RfpMetadata)
        .join(RfpMetadata, RfpMetadata.ticket_id == RfpTicket.ticket_id, isouter=True)
        .order_by(RfpTicket.created_at.desc())
        .limit(limit)
    ).all()
    return [(ticket, metadata) for ticket, metadata in rows]


def get_details(session: Session, ticket_id: uuid.UUID) -> Tuple[Optional[RfpMetadata], List[RfpDepartmentSection]]:
    metadata = session.get(RfpMetadata, ticket_id)
    sections = session.exec(
        select(RfpDepartmentSection).where(RfpDepartmentSection.ticket_id == ticket_id)
    ).all()
    return metadata, list(sections)


def set_task(session: Session, ticket: RfpTicket, task_id: str) -> None:
    ticket.task_id = task_id
    ticket.updated_at = utcnow()
    session.add(ticket)
    session.commit()


def start_attempt(session: Session, ticket: RfpTicket) -> None:
    ticket.attempts += 1
    ticket.updated_at = utcnow()
    session.add(ticket)
    session.commit()


def reset_for_reprocess(session: Session, ticket: RfpTicket) -> None:
    """Vuelve a `analizando` y borra el resultado anterior (reproceso manual)."""
    _delete_results(session, ticket.ticket_id)
    ticket.status = STATUS_ANALYZING
    ticket.discard_reason = None
    ticket.classifier_reason = None
    ticket.requires_compliance_review = False
    ticket.phi_categories = []
    ticket.error_message = None
    ticket.handoff_payload = None
    ticket.response_handoff = None
    ticket.next_stage = None
    ticket.completed_at = None
    ticket.approval_thread_id = None  # el runner de la Parte 3 borra el checkpoint huérfano
    ticket.arbitration = None
    ticket.updated_at = utcnow()
    session.add(ticket)
    session.commit()


def mark_error(session: Session, ticket_id: uuid.UUID, message: str) -> None:
    ticket = session.get(RfpTicket, ticket_id)
    if ticket is None:
        return
    ticket.status = STATUS_ERROR
    ticket.error_message = message[:ERROR_MESSAGE_MAX]
    ticket.updated_at = utcnow()
    session.add(ticket)
    session.commit()


def recover_stale(session: Session, ticket: RfpTicket) -> bool:
    """Si el ticket lleva demasiado en un estado de trabajo (`analizando`,
    `generando_borrador`, `en_evaluación`), pasa a `error`: el estado vuelve
    a decir la verdad aunque el worker haya muerto sin avisar. En la Parte 2
    cada avance de una sección renueva `updated_at`, así que solo caduca un
    ticket que de verdad dejó de moverse."""
    updated = ticket.updated_at
    if updated.tzinfo is None:  # SQLite (tests) devuelve fechas sin zona
        from datetime import timezone

        updated = updated.replace(tzinfo=timezone.utc)
    if ticket.status not in IN_PROGRESS_STATUSES or utcnow() - updated < STALE_AFTER:
        return False
    stage = "El análisis" if ticket.status == STATUS_ANALYZING else "La generación de borradores"
    mark_error(session, ticket.ticket_id, f"{stage} no terminó a tiempo (worker detenido). Reprocesar el ticket.")
    session.refresh(ticket)
    return True


# --------------------------------------------------------------------------
# Parte 2: generación y evaluación de borradores
# --------------------------------------------------------------------------


def _section(session: Session, ticket_id: uuid.UUID, department_id: str) -> Optional[RfpDepartmentSection]:
    return session.exec(
        select(RfpDepartmentSection).where(
            RfpDepartmentSection.ticket_id == ticket_id, RfpDepartmentSection.department_id == department_id
        )
    ).first()


def start_drafting(session: Session, ticket: RfpTicket) -> None:
    """Ticket → `generando_borrador` y secciones a `pendiente`, borrando lo
    que dejara un intento anterior (reintento de Celery o reproceso)."""
    ticket.status = STATUS_DRAFTING
    ticket.error_message = None
    ticket.response_handoff = None
    ticket.approval_thread_id = None
    ticket.arbitration = None
    ticket.updated_at = utcnow()
    session.add(ticket)
    for section in session.exec(
        select(RfpDepartmentSection).where(RfpDepartmentSection.ticket_id == ticket.ticket_id)
    ).all():
        section.draft_content = None
        section.evaluation_results = None
        section.evaluation_history = []
        section.draft_status = SECTION_PENDING
        section.draft_iterations = 0
        section.drafted_at = None
        # Un borrador nuevo invalida las aprobaciones del anterior.
        section.approval_status = None
        section.approval_request = None
        section.pending_decision = None
        section.approval_round = 0
        section.approval_history = []
        section.approval_comments = None
        section.approver = None
        section.approved_at = None
        section.decided_by = None
        section.confirmed_capacity = None
        session.add(section)
    session.commit()


def record_section_progress(
    session: Session,
    ticket_id: uuid.UUID,
    department_id: str,
    *,
    draft_status: str,
    iteration: int,
    draft: Optional[str] = None,
    evaluation: Optional[Dict[str, Any]] = None,
    ticket_status: Optional[str] = None,
) -> None:
    """Progreso en tiempo real de una sección (lo pinta /rfps/[id]). El
    resultado final lo escribe `save_response`; esto es solo el avance."""
    section = _section(session, ticket_id, department_id)
    ticket = session.get(RfpTicket, ticket_id)
    if section is None or ticket is None:
        return
    section.draft_status = draft_status
    section.draft_iterations = iteration
    if draft is not None:
        section.draft_content = draft
        section.drafted_at = utcnow()
    if evaluation is not None:
        section.evaluation_results = evaluation
        section.evaluation_history = [*(section.evaluation_history or []), evaluation]
    session.add(section)
    if ticket_status and ticket.status in IN_PROGRESS_STATUSES:
        ticket.status = ticket_status
    ticket.updated_at = utcnow()  # latido: evita que recover_stale lo dé por muerto
    session.add(ticket)
    session.commit()


def save_response(
    session: Session,
    ticket: RfpTicket,
    *,
    status: str,
    sections: List[Dict[str, Any]],
    handoff: Dict[str, Any],
    requires_compliance_review: bool,
) -> None:
    """Estado final + borradores + EvaluationResult + handoff a la Parte 3,
    en una sola transacción."""
    for result in sections:
        section = _section(session, ticket.ticket_id, result["department_id"])
        if section is None:
            continue
        section.draft_content = result["draft_content"]
        section.evaluation_results = result["evaluation"]
        section.evaluation_history = result["history"]
        section.draft_status = result["status"]
        section.draft_iterations = result["iterations"]
        section.drafted_at = section.drafted_at or utcnow()
        session.add(section)
    ticket.status = status
    ticket.next_stage = NEXT_STAGE_APPROVAL
    ticket.response_handoff = handoff
    # Una sección con PHI dispara el árbitro fijo `phi-detected` (Claire Whitfield).
    ticket.requires_compliance_review = ticket.requires_compliance_review or requires_compliance_review
    ticket.updated_at = utcnow()
    session.add(ticket)
    session.commit()


def reset_for_response(session: Session, ticket: RfpTicket) -> None:
    """Reproceso manual de la Parte 2: vuelve a `analisis_completo` sin tocar
    el análisis de la Parte 1 (metadatos, key_aspects, handoff_payload)."""
    ticket.status = STATUS_COMPLETE
    ticket.error_message = None
    ticket.response_handoff = None
    ticket.approval_thread_id = None
    ticket.arbitration = None
    ticket.completed_at = None
    session.exec(delete(RfpFinalDocument).where(RfpFinalDocument.ticket_id == ticket.ticket_id))
    ticket.updated_at = utcnow()
    session.add(ticket)
    session.commit()


def _delete_results(session: Session, ticket_id: uuid.UUID) -> None:
    session.exec(delete(RfpFinalDocument).where(RfpFinalDocument.ticket_id == ticket_id))
    session.exec(delete(RfpDepartmentSection).where(RfpDepartmentSection.ticket_id == ticket_id))
    session.exec(delete(RfpMetadata).where(RfpMetadata.ticket_id == ticket_id))


def _parse_date(value: Optional[str]) -> Optional[date]:
    try:
        return date.fromisoformat(value) if value else None
    except ValueError:
        return None


def save_result(
    session: Session,
    ticket: RfpTicket,
    *,
    ticket_fields: Dict[str, Any],
    metadata_fields: Optional[Dict[str, Any]],
    sections: List[Dict[str, Any]],
) -> None:
    """Estado final + metadatos + secciones en una sola transacción."""
    _delete_results(session, ticket.ticket_id)
    for key, value in ticket_fields.items():
        setattr(ticket, key, value)
    ticket.updated_at = utcnow()
    ticket.completed_at = ticket.updated_at
    session.add(ticket)
    if metadata_fields is not None:
        fields = dict(metadata_fields)
        fields["deadline"] = _parse_date(fields.get("deadline"))
        session.add(RfpMetadata(ticket_id=ticket.ticket_id, **fields))
    for section in sections:
        session.add(RfpDepartmentSection(ticket_id=ticket.ticket_id, **section))
    session.commit()


# --------------------------------------------------------------------------
# Parte 3: aprobación humana, arbitraje, documento final y trazabilidad
# --------------------------------------------------------------------------


def sections_by_department(session: Session, ticket_id: uuid.UUID) -> Dict[str, RfpDepartmentSection]:
    rows = session.exec(select(RfpDepartmentSection).where(RfpDepartmentSection.ticket_id == ticket_id)).all()
    return {row.department_id: row for row in rows}


def start_approval(session: Session, ticket: RfpTicket, thread_id: str) -> None:
    """Ticket → `esperando_aprobación`. Conserva todo lo de las Partes 1 y 2
    (README: "conserva valores previos en la misma fila")."""
    ticket.status = STATUS_AWAITING_APPROVAL
    ticket.approval_thread_id = thread_id
    ticket.error_message = None
    ticket.updated_at = utcnow()
    session.add(ticket)
    session.commit()


def record_pending_decision(session: Session, section: RfpDepartmentSection, decision: Dict[str, Any]) -> None:
    """La API guarda la decisión ya validada; el worker la aplica (Redis solo
    lleva el ticket_id, nunca un comentario)."""
    section.pending_decision = decision
    section.approval_status = APPROVAL_DECISION_RECORDED
    session.add(section)
    session.commit()


def clear_pending_decision(session: Session, section: RfpDepartmentSection, *, error: Optional[str] = None) -> None:
    """Decisión que ya no corresponde a ninguna petición del grafo (llegó
    tarde, o el borrador cambió entre medias). Se descarta con el motivo
    visible para quien la envió."""
    section.pending_decision = None
    if section.approval_request is not None:
        section.approval_status = APPROVAL_PENDING
        section.approval_request = {**section.approval_request, "last_error": error}
    session.add(section)


def mark_decision_applying(session: Session, section: RfpDepartmentSection, decision: Dict[str, Any]) -> None:
    """La decisión entra al grafo: pasa de pendiente al historial. Los
    comentarios se guardan (ya pasaron el detector de PHI); el historial solo
    lleva si los hubo."""
    section.approval_history = [
        *(section.approval_history or []),
        {
            "decision": decision["decision"],
            "round": section.approval_round,
            "decided_by": decision.get("decided_by"),
            "decided_at": decision.get("decided_at"),
            "has_comments": bool(decision.get("comments")),
            "confirmed_capacity": decision.get("confirmed_capacity"),
        },
    ]
    # Se conserva el último comentario escrito: aprobar sin comentar no borra
    # lo que el responsable pidió en la ronda anterior.
    if decision.get("comments"):
        section.approval_comments = decision["comments"]
    section.decided_by = decision.get("decided_by")
    section.pending_decision = None
    section.approval_status = APPROVAL_REVISING if decision["decision"] == DECISION_REQUEST_CHANGES else APPROVAL_DECISION_RECORDED
    session.add(section)


def record_pending_arbitration(session: Session, ticket: RfpTicket, decision: Dict[str, Any]) -> None:
    ticket.arbitration = {**(ticket.arbitration or {}), "pending_decision": decision}
    ticket.updated_at = utcnow()
    session.add(ticket)
    session.commit()


def save_final_document(session: Session, ticket: RfpTicket, document: Dict[str, Any]) -> None:
    """FinalDocument + ticket `terminado`, en la misma transacción que el
    resto de la sincronización (la hace el runner con un solo commit)."""
    session.exec(delete(RfpFinalDocument).where(RfpFinalDocument.ticket_id == ticket.ticket_id))
    session.add(RfpFinalDocument(
        ticket_id=ticket.ticket_id,
        sections=document["sections"],
        currency=document["currency"],
        client_country=document["client_country"],
        approvals=document["approvals"],
        content_markdown=document["content_markdown"],
        generated_at=datetime.fromisoformat(document["generated_at"]),
    ))
    ticket.status = STATUS_FINISHED
    ticket.next_stage = None
    ticket.completed_at = utcnow()


def close_without_document(session: Session, ticket: RfpTicket, *, reason: str, requires_compliance_review: bool) -> None:
    ticket.status = STATUS_DISCARDED
    ticket.discard_reason = reason
    ticket.next_stage = None
    ticket.completed_at = utcnow()
    ticket.requires_compliance_review = ticket.requires_compliance_review or requires_compliance_review


def get_final_document(session: Session, ticket_id: uuid.UUID) -> Optional[RfpFinalDocument]:
    return session.get(RfpFinalDocument, ticket_id)


def insert_trace_events(session: Session, ticket_id: uuid.UUID, entries: List[Dict[str, Any]]) -> int:
    """Inserta ignorando los ya registrados (`event_id` determinista: un nodo
    reejecutado al reanudar no duplica su evento). Devuelve los nuevos."""
    if not entries:
        return 0
    rows = [
        {
            "event_id": e["event_id"], "ticket_id": ticket_id, "part": e["part"], "node": e["node"],
            "agent": e["agent"][:80], "department_id": e.get("department_id"), "input": e.get("input") or {},
            "output": e.get("output") or {}, "timestamp": datetime.fromisoformat(e["timestamp"]),
        }
        for e in entries
    ]
    dialect = session.get_bind().dialect.name
    insert = pg_insert if dialect == "postgresql" else sqlite_insert
    statement = insert(RfpTraceEvent.__table__).values(rows).on_conflict_do_nothing(index_elements=["event_id"])
    result = session.exec(statement)
    session.commit()
    return result.rowcount or 0


def list_trace_events(session: Session, ticket_id: uuid.UUID) -> List[RfpTraceEvent]:
    return list(session.exec(
        select(RfpTraceEvent)
        .where(RfpTraceEvent.ticket_id == ticket_id)
        .order_by(RfpTraceEvent.timestamp, RfpTraceEvent.part, RfpTraceEvent.event_id)
    ).all())
