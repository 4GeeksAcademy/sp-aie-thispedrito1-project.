"""Endpoints del cierre financiero (`/financial-close`).

Solo HTTP: validar, comprobar el rol organizativo y llamar a `workflow`.
- Cualquier autenticado del directorio `fc_staff` lee informes y su
  trazabilidad (los destinatarios del BRIEFING; también Tom antes de aprobar).
- Solo Tom Callahan (`revenue_cycle_director`) aprueba o rechaza.
- Solo un admin de la plataforma encola un cierre manual y ve el registro de acciones.
Entradas validadas a mano (400 `{"errors"}`): un 422 de Pydantic repetiría el
comentario recibido, que podría llevar el dato que se quiere rechazar.
"""

from __future__ import annotations

import logging
import uuid
from datetime import date, datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Query, status
from fastapi.responses import JSONResponse
from sqlmodel import Session

from database import get_inventory_db
from security import get_current_user, require_admin
from services.financial_close import repository, schedule, workflow
from services.financial_close.catalog import PEOPLE, REVIEWER_ROLE
from services.financial_close.models import REPORT_PENDING, FcReport
from services.financial_close.runtime import ActionLog
from services.financial_close.schemas import (
    AgentActionRead,
    FigureTrace,
    InboxItem,
    ReportListItem,
    ReportRead,
    RunEnqueued,
    StaffRole,
)

logger = logging.getLogger("healthcore.financial_close.api")
router = APIRouter(prefix="/financial-close", tags=["financial-close"], dependencies=[Depends(get_current_user)])


def _bad_request(field: str, message: str, code: int = status.HTTP_400_BAD_REQUEST) -> HTTPException:
    return HTTPException(status_code=code, detail={"errors": [{"field": field, "message": message}]})


def _staff_role(session: Session, user: Dict[str, Any]) -> Optional[str]:
    staff = repository.get_staff(session, user.get("id"))
    return staff.org_role if staff else None


def _require_staff(session: Session, user: Dict[str, Any]) -> Optional[str]:
    """Los informes financieros solo los ve el directorio del cierre (o un admin)."""
    role = _staff_role(session, user)
    if role is None and user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="No tienes acceso a los informes de cierre.")
    return role


def _report_id(value: str) -> uuid.UUID:
    try:
        return uuid.UUID(value)
    except ValueError:
        raise _bad_request("report_id", "Identificador no válido.")


def _get_report(session: Session, value: str) -> FcReport:
    row = repository.get_report(session, _report_id(value))
    if row is None:
        raise HTTPException(status_code=404, detail="Informe no encontrado.")
    return row


def _list_item(row: FcReport) -> Dict[str, Any]:
    return {"id": str(row.id), "period": row.period, "version": row.version, "status": row.status,
            "trigger": row.trigger, "narrative_source": row.narrative_source, "figures_sha256": row.figures_sha256,
            "supersedes_id": str(row.supersedes_id) if row.supersedes_id else None, "created_at": row.created_at}


@router.get("/me", response_model=StaffRole)
def me(session: Session = Depends(get_inventory_db), user: Dict[str, Any] = Depends(get_current_user)):
    role = _staff_role(session, user)
    return {"org_role": role, "display_name": PEOPLE.get(role) if role else None,
            "is_reviewer": role == REVIEWER_ROLE, "next_scheduled_run": schedule.next_run(datetime.now(timezone.utc))}


@router.get("/reports", response_model=List[ReportListItem])
def list_reports(session: Session = Depends(get_inventory_db), user: Dict[str, Any] = Depends(get_current_user)):
    _require_staff(session, user)
    return [_list_item(r) for r in repository.list_reports(session)]


@router.get("/reports/{report_id}", response_model=ReportRead)
def get_report(report_id: str, lang: str = Query("en"), session: Session = Depends(get_inventory_db),
               user: Dict[str, Any] = Depends(get_current_user)):
    role = _require_staff(session, user)
    if lang not in ("en", "es"):
        raise _bad_request("lang", "Idioma no disponible (en | es).")
    row = _get_report(session, report_id)
    return {
        **_list_item(row), "language": lang, "markdown": row.report_en if lang == "en" else row.report_es,
        "figures": row.figures or {}, "quality": row.quality or {},
        "decisions": [{"decision": d.decision, "comments": d.comments, "decided_by": d.decided_by,
                       "decided_at": d.decided_at} for d in repository.decisions_for(session, row.id)],
        "distribution": [{"recipient_role": d.recipient_role, "recipient_name": d.recipient_name,
                          "delivered_at": d.delivered_at} for d in repository.distributions_for(session, row.id)],
        "reviewer": PEOPLE[REVIEWER_ROLE], "can_decide": role == REVIEWER_ROLE and row.status == REPORT_PENDING,
    }


@router.get("/reports/{report_id}/figures/{figure_id:path}", response_model=FigureTrace)
def trace_figure(report_id: str, figure_id: str, session: Session = Depends(get_inventory_db),
                 user: Dict[str, Any] = Depends(get_current_user)):
    """Origen de una cifra: sus fuentes y, para las del ledger, cada registro."""
    _require_staff(session, user)
    row = _get_report(session, report_id)
    figure = (row.figures or {}).get(figure_id)
    if figure is None:
        raise HTTPException(status_code=404, detail="Cifra no encontrada en este informe.")
    ids = [s["entry_id"] for s in figure["sources"] if s.get("kind") == "ledger"]
    records = [{
        "entry_id": e.entry_id, "source_file": e.source_file, "source_row": e.source_row, "source_ref": e.source_ref,
        "entry_date": e.entry_date, "country": e.country, "payer": e.payer, "category": e.category,
        "currency": e.currency, "amount": None if e.sensitive_categories else (str(e.amount) if e.amount is not None else None),
        "status": e.status, "description": e.description, "description_withheld": e.description is None,
    } for e in repository.ledger_entries(session, row.period, ids)]
    return {"figure_id": figure_id, "value": figure.get("value"), "unit": figure["unit"],
            "sources": figure["sources"], "ledger_records": records}


@router.post("/reports/{report_id}/decision", response_model=ReportListItem)
def decide(report_id: str, payload: Any = Body(None), session: Session = Depends(get_inventory_db),
           user: Dict[str, Any] = Depends(get_current_user)):
    if not isinstance(payload, dict):
        raise _bad_request("body", "El cuerpo debe ser un objeto JSON.")
    unknown = sorted(set(payload) - {"decision", "comments"})
    if unknown:
        raise _bad_request(unknown[0], "Campo no permitido.")
    rid = _report_id(report_id)
    try:
        row = workflow.decide(session, rid, user_id=user.get("id"), org_role=_staff_role(session, user),
                              decision=payload.get("decision"), comments=payload.get("comments"), log=ActionLog())
    except workflow.DecisionError as exc:
        if exc.status == 403:
            raise HTTPException(status_code=403, detail=exc.message)
        if exc.status == 404:
            raise HTTPException(status_code=404, detail=exc.message)
        raise _bad_request(exc.field, exc.message, exc.status)
    if row.status == "rejected":
        _enqueue_revision(row.id)
    return _list_item(row)


def _enqueue_revision(report_id: uuid.UUID) -> None:
    from services.tasks.financial_close_tasks import enqueue_revision

    try:
        enqueue_revision(report_id)
    except Exception as exc:  # noqa: BLE001 — Redis caído: la decisión ya está guardada
        logger.error("financial_close_revision_enqueue_failed report_id=%s error_type=%s", report_id,
                     type(exc).__name__)


@router.get("/inbox", response_model=List[InboxItem])
def inbox(session: Session = Depends(get_inventory_db), user: Dict[str, Any] = Depends(get_current_user)):
    role = _staff_role(session, user)
    if role is None:
        return []
    items = [{"kind": n.kind, "period": n.period, "report_id": str(n.report_id) if n.report_id else None,
              "entry_id": n.entry_id, "summary": n.summary, "created_at": n.created_at}
             for n in repository.notifications_for_role(session, role)]
    for d in repository.distributions_for_role(session, role):
        report = repository.get_report(session, d.report_id)
        items.append({"kind": "report_distributed", "period": report.period if report else date.today(),
                      "report_id": str(d.report_id), "entry_id": None,
                      "summary": f"Approved financial close {report.period:%Y-%m} v{report.version}." if report else "",
                      "created_at": d.delivered_at})
    return sorted(items, key=lambda i: i["created_at"], reverse=True)


@router.post("/runs", response_model=RunEnqueued, status_code=202)
def enqueue_run(payload: Any = Body(None), user: Dict[str, Any] = Depends(get_current_user)):
    require_admin(user)
    period_text = payload.get("period") if isinstance(payload, dict) else None
    try:
        period = date.fromisoformat(f"{period_text}-01") if period_text else schedule.period_to_close(
            datetime.now(timezone.utc))
    except (TypeError, ValueError):
        raise _bad_request("period", "Formato YYYY-MM.")
    from services.tasks.financial_close_tasks import enqueue_close

    try:
        task_id = enqueue_close(period, "manual")
    except Exception as exc:  # noqa: BLE001
        logger.error("financial_close_enqueue_failed error_type=%s", type(exc).__name__)
        return JSONResponse(status_code=503, content={"detail": "La cola de tareas no está disponible."})
    return {"task_id": task_id, "period": period, "status": "queued"}


@router.get("/actions", response_model=List[AgentActionRead])
def actions(request_id: Optional[str] = None, session: Session = Depends(get_inventory_db),
            user: Dict[str, Any] = Depends(get_current_user)):
    require_admin(user)
    return [{"request_id": a.request_id, "actor": a.actor, "actor_type": a.actor_type, "action": a.action,
             "tool": a.tool, "outcome": a.outcome, "details": a.details, "created_at": a.created_at}
            for a in repository.list_actions(session, request_id)]
