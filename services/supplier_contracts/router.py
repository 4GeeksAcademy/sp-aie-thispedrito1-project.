"""Endpoints de proveedores y contratos (`/supplier-contracts`).

Solo HTTP: validar, comprobar el rol organizativo y llamar al supervisor.
Roles del directorio `sc_staff` (no el admin de la plataforma):
- Dr. Marcus Reid (`clinical_ops_director`): barrido de alertas, aprobar y
  marcar como enviada una comunicación.
- Claire Whitfield (`compliance_officer`): aprobar comunicaciones de
  proveedores con datos de pacientes y autorizar a continuar sin BAA/DPA.
- Dra. Sandra Okonkwo (`ceo`): aprobar comunicaciones de contratos > 500.000 USD.
Cualquier usuario autenticado puede consultar, preguntar y pedir borradores
(redactar no envía nada).

Las preguntas y briefs se resuelven en la propia petición (como las dudas del
onboarding) y llevan el límite de uso de los canales con modelo.
"""

from __future__ import annotations

import os
import uuid
from datetime import date
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Body, Depends, HTTPException, status
from fastapi.responses import JSONResponse
from sqlmodel import Session

from database import get_inventory_db
from rate_limit import enforce_llm_rate_limit
from security import get_current_user
from services.supplier_contracts import alerts, brief, communications, repository, supervisor
from services.supplier_contracts.catalog import (
    CATEGORY_LABELS,
    PEOPLE,
    ROLE_CLINICAL_OPS,
    ROLE_COMPLIANCE,
    SUPPLIERS_BY_ID,
)
from services.supplier_contracts.ingestion import TERM_LABELS
from services.supplier_contracts.runtime import ActionLog, human_actor
from services.supplier_contracts.schemas import (
    AgentActionRead,
    AlertRead,
    AskResponse,
    BriefResponse,
    ClearanceRead,
    DraftRead,
    StaffRole,
    SupplierContractRead,
    SupplierListItem,
    SweepResult,
)

router = APIRouter(prefix="/supplier-contracts", tags=["supplier-contracts"], dependencies=[Depends(get_current_user)])

# Sustituible en tests: sin modelo, el supervisor planifica por reglas y los
# borradores salen de plantilla (deterministas y sin red).
USE_MODEL = os.getenv("SUPPLIER_CONTRACTS_USE_MODEL", "1") != "0"
MAX_QUESTION_LENGTH = 1000


def _today() -> date:
    return date.today()


def _bad_request(field: str, message: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail={"errors": [{"field": field, "message": message}]})


def _fields(payload: Any, allowed: tuple) -> Dict[str, Any]:
    if not isinstance(payload, dict):
        raise _bad_request("body", "El cuerpo debe ser un objeto JSON.")
    unknown = sorted(set(payload) - set(allowed))
    if unknown:
        raise _bad_request(unknown[0], "Campo no permitido.")
    return payload


def _supplier_id(payload: Dict[str, Any]) -> str:
    value = payload.get("supplier_id")
    if not isinstance(value, str) or value not in SUPPLIERS_BY_ID:
        raise _bad_request("supplier_id", "Proveedor desconocido.")
    return value


def _role(session: Session, user: Dict[str, Any]) -> Optional[str]:
    staff = repository.get_staff(session, user.get("id"))
    return staff.org_role if staff else None


def _require_role(session: Session, user: Dict[str, Any], *roles: str) -> str:
    role = _role(session, user)
    if role not in roles:
        raise HTTPException(status_code=403, detail="Tu rol no permite esta acción.")
    return role


# --- Lecturas ----------------------------------------------------------------------


@router.get("/me", response_model=StaffRole)
def me(session: Session = Depends(get_inventory_db), user: Dict[str, Any] = Depends(get_current_user)) -> Dict[str, Any]:
    role = _role(session, user)
    return {"org_role": role, "display_name": PEOPLE.get(role) if role else None}


@router.get("/suppliers", response_model=List[SupplierListItem])
def list_suppliers(session: Session = Depends(get_inventory_db)) -> List[Dict[str, Any]]:
    out = []
    for supplier in repository.list_suppliers(session):
        history = repository.price_history(session, supplier.supplier_id)
        contract = repository.contract_for_supplier(session, supplier.supplier_id)
        latest = history[-1] if history else None
        out.append({
            "supplier_id": supplier.supplier_id, "name": supplier.name, "country": supplier.country,
            "category": supplier.category, "category_label": CATEGORY_LABELS.get(supplier.category, supplier.category),
            "currency": supplier.currency, "processes_patient_data": supplier.processes_patient_data,
            "contract_id": contract.contract_id if contract else None,
            "latest_price": str(latest.unit_price) if latest else None,
            "latest_month": latest.month.strftime("%Y-%m") if latest else None,
            "unit": latest.unit if latest else None,
            "monthly_volume": latest.monthly_volume if latest else None,
            "source": f"{supplier.source_file} fila {supplier.source_row}",
        })
    return out


@router.get("/suppliers/{supplier_id}/contract", response_model=SupplierContractRead)
def supplier_contract(supplier_id: str, session: Session = Depends(get_inventory_db)) -> Dict[str, Any]:
    if supplier_id not in SUPPLIERS_BY_ID:
        raise HTTPException(status_code=404, detail="Proveedor desconocido.")
    contract = repository.contract_for_supplier(session, supplier_id)
    if contract is None:
        return {"supplier_id": supplier_id, "contract": None,
                "message": "No hay contrato: solo cotizaciones o pedidos puntuales."}
    terms = repository.contract_terms(session, contract.contract_id)
    return {
        "supplier_id": supplier_id,
        "contract": {"contract_id": contract.contract_id, "source_file": contract.source_file, "pages": contract.pages},
        "terms": [{
            "term": name, "label": TERM_LABELS[name], "status": t.status if t else "missing",
            "confidence": t.confidence if t else 0.0, "value": t.value if t else None,
            "clause": t.clause if t else None, "page": t.page if t else None,
            "quote": t.quote if t else None, "note": t.note if t else None,
        } for name, t in ((n, terms.get(n)) for n in TERM_LABELS)],
    }


@router.get("/alerts", response_model=List[AlertRead])
def list_alerts(mine: bool = False, session: Session = Depends(get_inventory_db),
                user: Dict[str, Any] = Depends(get_current_user)) -> List[Dict[str, Any]]:
    role = _role(session, user) if mine else None
    rows = repository.list_alerts(session, recipient_role=role) if (not mine or role) else []
    return [{
        "alert_key": a.alert_key, "kind": a.kind, "supplier_id": a.supplier_id, "contract_id": a.contract_id,
        "recipient_role": a.recipient_role, "recipient": PEOPLE.get(a.recipient_role),
        "due_date": a.due_date, "summary": a.summary, "details": a.details, "refs": a.refs,
        "raised_by": a.raised_by, "created_at": a.created_at,
    } for a in rows]


@router.post("/alerts/sweep", response_model=SweepResult)
def sweep_alerts(session: Session = Depends(get_inventory_db), user: Dict[str, Any] = Depends(get_current_user)) -> Dict[str, Any]:
    _require_role(session, user, ROLE_CLINICAL_OPS, ROLE_COMPLIANCE)
    created = alerts.run_alert_sweep(session, today=_today())
    return {"created": len(created), "alert_keys": [a.alert_key for a in created]}


@router.get("/actions", response_model=List[AgentActionRead])
def list_actions(request_id: Optional[str] = None, session: Session = Depends(get_inventory_db),
                 user: Dict[str, Any] = Depends(get_current_user)) -> List[Dict[str, Any]]:
    _require_role(session, user, ROLE_CLINICAL_OPS, ROLE_COMPLIANCE, "ceo")
    return [{"id": a.id, "request_id": a.request_id, "actor": a.actor, "action": a.action, "tool": a.tool,
             "outcome": a.outcome, "details": a.details, "created_at": a.created_at}
            for a in repository.list_actions(session, request_id=request_id)]


# --- Supervisor --------------------------------------------------------------------


@router.post("/ask", response_model=AskResponse, dependencies=[Depends(enforce_llm_rate_limit)])
def ask(payload: Any = Body(...), session: Session = Depends(get_inventory_db),
        user: Dict[str, Any] = Depends(get_current_user)) -> Dict[str, Any]:
    body = _fields(payload, ("question",))
    question = body.get("question")
    if not isinstance(question, str) or not question.strip():
        raise _bad_request("question", "Escribe una pregunta.")
    if len(question) > MAX_QUESTION_LENGTH:
        raise _bad_request("question", f"Máximo {MAX_QUESTION_LENGTH} caracteres.")
    result = supervisor.ask(session, question.strip(), requester=human_actor(user.get("id")), today=_today(),
                            use_model=USE_MODEL)
    return result.model_dump()


@router.post("/briefs", response_model=BriefResponse,
             responses={409: {"model": BriefResponse, "description": "Detenido: falta el BAA/DPA"}}, dependencies=[Depends(enforce_llm_rate_limit)])
def create_brief(payload: Any = Body(...), session: Session = Depends(get_inventory_db),
                 user: Dict[str, Any] = Depends(get_current_user)):
    supplier_id = _supplier_id(_fields(payload, ("supplier_id",)))
    result = brief.build_brief(session, supplier_id, requester=human_actor(user.get("id")), today=_today())
    if result.status == "blocked":
        # `detail` es lo que el backoffice (http.ts) muestra como mensaje de error.
        return JSONResponse(status_code=409, content={**result.model_dump(), "detail": result.message})
    return result.model_dump()


@router.post("/compliance-clearances", status_code=201, response_model=ClearanceRead)
def clear_supplier(payload: Any = Body(...), session: Session = Depends(get_inventory_db),
                   user: Dict[str, Any] = Depends(get_current_user)) -> Dict[str, Any]:
    _require_role(session, user, ROLE_COMPLIANCE)
    supplier_id = _supplier_id(_fields(payload, ("supplier_id",)))
    clearance = repository.add_clearance(session, supplier_id, human_actor(user.get("id")))
    log = ActionLog()
    log.record(human_actor(user.get("id")), "compliance_clearance", "granted", supplier_id=supplier_id)
    log.flush(session)
    return {"supplier_id": supplier_id, "cleared_at": clearance.created_at}


# --- Comunicaciones -------------------------------------------------------------------


@router.get("/drafts", response_model=List[DraftRead])
def list_drafts(session: Session = Depends(get_inventory_db)) -> List[Dict[str, Any]]:
    return [communications.draft_view(session, d) for d in repository.list_drafts(session)]


@router.post("/drafts", status_code=201, response_model=DraftRead, dependencies=[Depends(enforce_llm_rate_limit)])
def create_draft(payload: Any = Body(...), session: Session = Depends(get_inventory_db),
                 user: Dict[str, Any] = Depends(get_current_user)) -> Dict[str, Any]:
    body = _fields(payload, ("supplier_id", "purpose"))
    supplier_id = _supplier_id(body)
    if body.get("purpose") not in communications.PURPOSES:
        raise _bad_request("purpose", "Propósito no válido.")
    try:
        draft = communications.draft_communication(session, supplier_id, body["purpose"], requester_id=user.get("id"),
                                                   today=_today(), use_model=USE_MODEL)
    except communications.CommunicationBlocked as exc:
        raise HTTPException(status_code=409, detail=exc.message) from exc
    return communications.draft_view(session, draft)


def _draft_id(value: str) -> uuid.UUID:
    try:
        return uuid.UUID(value)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="Borrador no encontrado.") from exc


@router.post("/drafts/{draft_id}/decision", response_model=DraftRead)
def decide_draft(draft_id: str, payload: Any = Body(...), session: Session = Depends(get_inventory_db),
                 user: Dict[str, Any] = Depends(get_current_user)) -> Dict[str, Any]:
    body = _fields(payload, ("decision", "comment"))
    comment = body.get("comment")
    if comment is not None and not isinstance(comment, str):
        raise _bad_request("comment", "El comentario debe ser texto.")
    try:
        draft = communications.decide(session, _draft_id(draft_id), user_id=user.get("id"),
                                      org_role=_role(session, user), decision=body.get("decision"), comment=comment)
    except communications.DecisionError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.message) from exc
    return communications.draft_view(session, draft)


@router.post("/drafts/{draft_id}/send", response_model=DraftRead)
def send_draft(draft_id: str, session: Session = Depends(get_inventory_db),
               user: Dict[str, Any] = Depends(get_current_user)) -> Dict[str, Any]:
    try:
        draft = communications.send(session, _draft_id(draft_id), user_id=user.get("id"), org_role=_role(session, user))
    except communications.DecisionError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.message) from exc
    return communications.draft_view(session, draft)
