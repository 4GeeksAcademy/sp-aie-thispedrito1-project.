"""Acceso a las tablas `rfp_*` (Supabase). Única capa que escribe en ellas.

Escrituras del resultado en UNA transacción (`save_result`): el ticket pasa
a su estado final a la vez que se guardan metadatos y secciones. Si algo
falla a mitad, no queda un ticket `analisis_completo` sin secciones.
"""

from __future__ import annotations

import secrets
import uuid
from datetime import date, timedelta
from typing import Any, Dict, List, Optional, Tuple

from sqlmodel import Session, delete, select

from data.pipelines.rfp_intake.models import (
    IN_PROGRESS_STATUSES,
    NEXT_STAGE_APPROVAL,
    SECTION_PENDING,
    STATUS_ANALYZING,
    STATUS_COMPLETE,
    STATUS_DRAFTING,
    STATUS_ERROR,
    RfpDepartmentSection,
    RfpMetadata,
    RfpTicket,
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
    ticket.updated_at = utcnow()
    session.add(ticket)
    session.commit()


def _delete_results(session: Session, ticket_id: uuid.UUID) -> None:
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
