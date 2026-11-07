"""Endpoints del flujo de RFPs (Hito 9, Partes 1-3), en la API existente.

Solo HTTP: validar la subida, crear el ticket, encolar y consultar. El grafo
y la persistencia viven en data/pipelines/rfp_intake/ (este módulo importa
de ahí, nunca al revés).

`POST /rfp-tickets` no ejecuta el pipeline: crea el ticket `analizando`,
guarda el PDF en data/raw/rfp/<ticket_id>.pdf, encola la tarea de Celery y
responde 202. La UI hace poll de `GET /rfp-tickets/{id}`.

Parte 3: `POST .../sections/{department_id}/decision` y `POST
.../arbitration` validan la respuesta humana contra la petición que hizo el
grafo (400 con errores por campo), la guardan en la fila y encolan
`rfp_approval.advance_ticket` (202). La API nunca reanuda el grafo: una
revisión puede tardar un minuto y no debe bloquear la petición HTTP.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from kombu.exceptions import OperationalError
from redis.exceptions import RedisError
from sqlmodel import Session

from data.pipelines.rfp_intake import repository
from data.pipelines.rfp_intake.approval_runner import (
    ApprovalStateError,
    submit_arbitration_decision,
    submit_section_decision,
)
from data.pipelines.rfp_intake.decisions import DecisionError
from data.pipelines.rfp_intake.departments import DEPARTMENTS
from data.pipelines.rfp_intake.models import (
    APPROVAL_DECISION_RECORDED,
    NEXT_STAGE_DRAFTING,
    STATUS_ANALYZING,
    STATUS_DISCARDED,
    STATUS_ERROR,
)
from data.pipelines.rfp_intake.runner import pdf_path_for, relative_to_root
from database import get_inventory_db
from security import get_current_user
from services.rfp_intake.schemas import (
    ArbitrationDecisionRequest,
    RfpDecisionQueued,
    RfpDepartmentSectionRead,
    RfpFinalDocumentRead,
    RfpTraceEventRead,
    SectionDecisionRequest,
    RfpHandoff,
    RfpMetadataRead,
    RfpResponseHandoff,
    RfpTicketDetail,
    RfpTicketListItem,
    RfpTicketQueued,
)
from services.tasks.rfp_tasks import enqueue_rfp_approval, enqueue_rfp_ticket

logger = logging.getLogger("healthcore.rfp_intake")

router = APIRouter(prefix="/rfp-tickets", tags=["rfp-intake"], dependencies=[Depends(get_current_user)])

MAX_PDF_BYTES = 10 * 1024 * 1024
PDF_MAGIC = b"%PDF-"


def _read_pdf(file: UploadFile) -> bytes:
    """Valida por contenido, no por nombre ni content-type (los pone el
    cliente). Lee como mucho 10 MB + 1 byte para no cargar un archivo enorme."""
    payload = file.file.read(MAX_PDF_BYTES + 1)
    if len(payload) > MAX_PDF_BYTES:
        raise HTTPException(status_code=413, detail="El PDF supera el máximo de 10 MB.")
    if not payload.startswith(PDF_MAGIC):
        raise HTTPException(status_code=400, detail="El archivo no es un PDF.")
    return payload


@router.post("", response_model=RfpTicketQueued, status_code=status.HTTP_202_ACCEPTED)
def upload_rfp(
    file: UploadFile = File(..., description="RFP en PDF"),
    session: Session = Depends(get_inventory_db),
    current_user: Dict[str, Any] = Depends(get_current_user),
) -> RfpTicketQueued:
    payload = _read_pdf(file)
    ticket_id = uuid.uuid4()
    # Nombre = ticket_id, nunca el nombre original (podría llevar PHI).
    pdf_path = pdf_path_for(ticket_id)
    pdf_path.parent.mkdir(parents=True, exist_ok=True)
    pdf_path.write_bytes(payload)

    ticket = repository.create_ticket(
        session, ticket_id=ticket_id, raw_pdf_path=relative_to_root(pdf_path), created_by=str(current_user["id"])
    )
    try:
        task_id = enqueue_rfp_ticket(ticket_id)
    except (RedisError, OperationalError, OSError) as exc:
        # El ticket existe y el PDF está guardado: queda en `error` (honesto y
        # reprocesable con scripts/reprocess_rfp.py), no colgado en `analizando`.
        repository.mark_error(session, ticket_id, "La cola de tareas no estaba disponible. Reprocesar el ticket.")
        logger.error("rfp_enqueue_failed ticket=%s error_type=%s", ticket_id, type(exc).__name__)
        raise HTTPException(status_code=503, detail="Task queue unavailable. Try again later.") from exc
    repository.set_task(session, ticket, task_id)
    logger.info("rfp_ticket_created ticket=%s rfp_id=%s bytes=%d", ticket_id, ticket.rfp_id, len(payload))
    return RfpTicketQueued(ticket_id=ticket.ticket_id, rfp_id=ticket.rfp_id, status=ticket.status)


@router.get("", response_model=List[RfpTicketListItem])
def list_rfp_tickets(
    limit: int = Query(default=50, ge=1, le=200),
    session: Session = Depends(get_inventory_db),
) -> List[RfpTicketListItem]:
    items = []
    for ticket, metadata in repository.list_tickets(session, limit=limit):
        repository.recover_stale(session, ticket)
        items.append(
            RfpTicketListItem(
                **ticket.model_dump(include=set(RfpTicketListItem.model_fields) - {"client_name", "client_country"}),
                client_name=metadata.client_name if metadata else None,
                client_country=metadata.client_country if metadata else None,
            )
        )
    return items


def _get_or_404(session: Session, ticket_id: uuid.UUID):
    ticket = repository.get_ticket(session, ticket_id)
    if ticket is None:
        raise HTTPException(status_code=404, detail="RFP ticket not found.")
    return ticket


@router.get("/{ticket_id}", response_model=RfpTicketDetail)
def get_rfp_ticket(ticket_id: uuid.UUID, session: Session = Depends(get_inventory_db)) -> RfpTicketDetail:
    ticket = _get_or_404(session, ticket_id)
    repository.recover_stale(session, ticket)
    metadata, sections = repository.get_details(session, ticket_id)
    order = list(DEPARTMENTS)
    return RfpTicketDetail(
        **ticket.model_dump(
            include=set(RfpTicketDetail.model_fields)
        - {"client_name", "client_country", "metadata", "sections", "arbitration", "has_final_document"}
        ),
        client_name=metadata.client_name if metadata else None,
        client_country=metadata.client_country if metadata else None,
        metadata=RfpMetadataRead(**metadata.model_dump()) if metadata else None,
        sections=[
            RfpDepartmentSectionRead(department=DEPARTMENTS[s.department_id].name, **s.model_dump())
            for s in sorted(sections, key=lambda s: order.index(s.department_id))
        ],
        arbitration=_public_arbitration(ticket.arbitration),
        has_final_document=repository.get_final_document(session, ticket_id) is not None,
    )


def _public_arbitration(arbitration: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Sin el texto de la decisión pendiente (la nota del árbitro va al
    generador, no hace falta repetirla en cada poll)."""
    if not arbitration:
        return None
    public = {k: v for k, v in arbitration.items() if k != "pending_decision"}
    public["decision_registered"] = bool(arbitration.get("pending_decision"))
    return public


@router.get("/{ticket_id}/handoff", response_model=RfpHandoff)
def get_rfp_handoff(ticket_id: uuid.UUID, session: Session = Depends(get_inventory_db)) -> RfpHandoff:
    """Contrato de entrada de la Parte 2 (docs/rfp-intake.md). Sigue
    disponible cuando la Parte 2 ya avanzó el estado del ticket: el análisis
    no deja de existir por haber empezado a redactar."""
    ticket = _get_or_404(session, ticket_id)
    if ticket.status in (STATUS_ANALYZING, STATUS_DISCARDED) or not ticket.handoff_payload:
        raise HTTPException(status_code=409, detail="The RFP analysis is not complete.")
    return RfpHandoff(
        ticket_id=ticket.ticket_id,
        next_stage=ticket.handoff_payload.get("next_stage", NEXT_STAGE_DRAFTING),
        payload=ticket.handoff_payload,
    )


@router.get("/{ticket_id}/response-handoff", response_model=RfpResponseHandoff)
def get_rfp_response_handoff(ticket_id: uuid.UUID, session: Session = Depends(get_inventory_db)) -> RfpResponseHandoff:
    """Contrato de entrada de la Parte 3 (docs/rfp-response.md): borrador +
    EvaluationResult por departamento; las secciones `needs_human_review`
    van incluidas y marcadas `provisional`."""
    ticket = _get_or_404(session, ticket_id)
    # Sigue disponible cuando la Parte 3 ya avanzó el estado (esperando
    # aprobación, terminado...): los borradores no dejan de existir.
    if not ticket.response_handoff:
        detail = "Draft generation failed." if ticket.status == STATUS_ERROR else "Draft generation is not complete."
        raise HTTPException(status_code=409, detail=detail)
    return RfpResponseHandoff(
        ticket_id=ticket.ticket_id,
        status=ticket.status,
        next_stage=ticket.response_handoff.get("next_stage", ticket.next_stage),
        payload=ticket.response_handoff,
    )


# --------------------------------------------------------------------------
# Parte 3: aprobación humana, arbitraje, documento final y trazabilidad
# --------------------------------------------------------------------------


def _enqueue_advance(ticket_id: uuid.UUID) -> None:
    try:
        enqueue_rfp_approval(ticket_id)
    except (RedisError, OperationalError, OSError) as exc:
        logger.error("rfp_approval_enqueue_failed ticket=%s error_type=%s", ticket_id, type(exc).__name__)
        raise HTTPException(
            status_code=503,
            detail="La decisión quedó guardada pero la cola no responde: se aplicará al relanzar el ticket.",
        ) from exc


@router.post(
    "/{ticket_id}/sections/{department_id}/decision",
    response_model=RfpDecisionQueued,
    status_code=status.HTTP_202_ACCEPTED,
)
def decide_section(
    ticket_id: uuid.UUID,
    department_id: str,
    body: SectionDecisionRequest,
    session: Session = Depends(get_inventory_db),
    current_user: Dict[str, Any] = Depends(get_current_user),
) -> RfpDecisionQueued:
    """Aprobar, solicitar cambios o rechazar la sección de un departamento.
    Se firma en nombre del responsable del BRIEFING; `decided_by` guarda
    quién pulsó (decisión del usuario: cualquier usuario autenticado)."""
    try:
        decision = submit_section_decision(
            session, ticket_id, department_id, body.model_dump(exclude={"draft_digest"}),
            decided_by=str(current_user["id"]), draft_digest=body.draft_digest,
        )
    except ApprovalStateError as exc:
        raise HTTPException(status_code=404 if exc.not_found else 409, detail=str(exc)) from exc
    except DecisionError as exc:
        raise HTTPException(status_code=400, detail={"errors": exc.errors}) from exc
    _enqueue_advance(ticket_id)
    logger.info("rfp_decision_recorded ticket=%s department=%s decision=%s", ticket_id, department_id, decision["decision"])
    return RfpDecisionQueued(ticket_id=ticket_id, department_id=department_id, approval_status=APPROVAL_DECISION_RECORDED)


@router.post("/{ticket_id}/arbitration", response_model=RfpDecisionQueued, status_code=status.HTTP_202_ACCEPTED)
def decide_arbitration(
    ticket_id: uuid.UUID,
    body: ArbitrationDecisionRequest,
    session: Session = Depends(get_inventory_db),
    current_user: Dict[str, Any] = Depends(get_current_user),
) -> RfpDecisionQueued:
    """Decisión del árbitro humano (`capacity-vs-population`: Tom Callahan)."""
    try:
        decision = submit_arbitration_decision(session, ticket_id, body.model_dump(), decided_by=str(current_user["id"]))
    except ApprovalStateError as exc:
        raise HTTPException(status_code=404 if exc.not_found else 409, detail=str(exc)) from exc
    except DecisionError as exc:
        raise HTTPException(status_code=400, detail={"errors": exc.errors}) from exc
    _enqueue_advance(ticket_id)
    logger.info("rfp_arbitration_recorded ticket=%s option=%s", ticket_id, decision["option"])
    return RfpDecisionQueued(ticket_id=ticket_id, approval_status=APPROVAL_DECISION_RECORDED)


@router.get("/{ticket_id}/final-document", response_model=RfpFinalDocumentRead)
def get_final_document(ticket_id: uuid.UUID, session: Session = Depends(get_inventory_db)) -> RfpFinalDocumentRead:
    ticket = _get_or_404(session, ticket_id)
    document = repository.get_final_document(session, ticket_id)
    if document is None:
        raise HTTPException(status_code=404, detail="The final document has not been generated.")
    return RfpFinalDocumentRead(rfp_id=ticket.rfp_id, **document.model_dump())


@router.get("/{ticket_id}/trace", response_model=List[RfpTraceEventRead])
def get_trace(ticket_id: uuid.UUID, session: Session = Depends(get_inventory_db)) -> List[RfpTraceEventRead]:
    """Qué agente hizo qué y en qué orden, de la Parte 1 a la 3."""
    _get_or_404(session, ticket_id)
    return [RfpTraceEventRead(**event.model_dump()) for event in repository.list_trace_events(session, ticket_id)]
