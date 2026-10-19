"""Endpoints de la recepción de RFPs (Hito 9, Parte 1), en la API existente.

Solo HTTP: validar la subida, crear el ticket, encolar y consultar. El grafo
y la persistencia viven en data/pipelines/rfp_intake/ (este módulo importa
de ahí, nunca al revés).

`POST /rfp-tickets` no ejecuta el pipeline: crea el ticket `analizando`,
guarda el PDF en data/raw/rfp/<ticket_id>.pdf, encola la tarea de Celery y
responde 202. La UI hace poll de `GET /rfp-tickets/{id}`.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any, Dict, List

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from kombu.exceptions import OperationalError
from redis.exceptions import RedisError
from sqlmodel import Session

from data.pipelines.rfp_intake import repository
from data.pipelines.rfp_intake.departments import DEPARTMENTS
from data.pipelines.rfp_intake.models import STATUS_COMPLETE
from data.pipelines.rfp_intake.runner import pdf_path_for, relative_to_root
from database import get_inventory_db
from security import get_current_user
from services.rfp_intake.schemas import (
    RfpDepartmentSectionRead,
    RfpHandoff,
    RfpMetadataRead,
    RfpTicketDetail,
    RfpTicketListItem,
    RfpTicketQueued,
)
from services.tasks.rfp_tasks import enqueue_rfp_ticket

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
            include=set(RfpTicketDetail.model_fields) - {"client_name", "client_country", "metadata", "sections"}
        ),
        client_name=metadata.client_name if metadata else None,
        client_country=metadata.client_country if metadata else None,
        metadata=RfpMetadataRead(**metadata.model_dump()) if metadata else None,
        sections=[
            RfpDepartmentSectionRead(department=DEPARTMENTS[s.department_id].name, **s.model_dump())
            for s in sorted(sections, key=lambda s: order.index(s.department_id))
        ],
    )


@router.get("/{ticket_id}/handoff", response_model=RfpHandoff)
def get_rfp_handoff(ticket_id: uuid.UUID, session: Session = Depends(get_inventory_db)) -> RfpHandoff:
    """Contrato de entrada de la Parte 2 (docs/rfp-intake.md)."""
    ticket = _get_or_404(session, ticket_id)
    if ticket.status != STATUS_COMPLETE or not ticket.handoff_payload:
        raise HTTPException(status_code=409, detail="The RFP analysis is not complete.")
    return RfpHandoff(ticket_id=ticket.ticket_id, next_stage=ticket.next_stage, payload=ticket.handoff_payload)
