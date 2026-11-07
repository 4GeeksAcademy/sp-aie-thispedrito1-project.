"""Informes internos: la colección canónica con alcance departamental.

Matriz del BRIEFING de Roles y Permisos, aplicando SIEMPRE primero el rol
(`require_capability`) y después el departamento (`ensure_in_scope` /
`department_scope`):

| Operación                  | Rol mínimo | Departamento                         |
|----------------------------|------------|--------------------------------------|
| GET /internal-reports      | employee   | solo el suyo (admin: todos)          |
| POST /internal-reports     | employee   | el suyo (solo admin elige otro)      |
| GET /internal-reports/{id} | employee   | 403 si es de otro                    |
| PUT  …/{id}                | supervisor | 403 si es de otro                    |
| POST …/{id}/approve        | supervisor | 403 si es de otro                    |
| DELETE …/{id}              | admin      | todos                                |

Título y cuerpo pasan por el validador de PHI (HIPAA / UK GDPR): son notas
operativas, nunca datos de un paciente. El texto nunca va a los logs.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status

from access_models import (
    CAP_RECORDS_APPROVE,
    CAP_RECORDS_CREATE,
    CAP_RECORDS_DELETE,
    CAP_RECORDS_READ,
    CAP_RECORDS_UPDATE,
    InternalReportCreate,
    InternalReportRead,
    InternalReportUpdate,
)
from audit_log import audit_event
from permissions import department_scope, ensure_in_scope, own_department, require_capability
from security import access_repo
from services.agent.memory import phi_guard

router = APIRouter(prefix="/internal-reports", tags=["internal-reports"])


def _phi_errors(*fields: tuple[str, Optional[str]]) -> None:
    errors = [
        {
            "field": name,
            "message": "Internal reports must not contain patient data ("
            + ", ".join(verdict.categories)
            + ").",
        }
        for name, text in fields
        if text and (verdict := phi_guard.scan(text)).contains_phi
    ]
    if errors:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail={"errors": errors})


def _read(report: dict[str, Any]) -> InternalReportRead:
    return InternalReportRead.model_validate(
        {**report, "department": access_repo.department_code(report.get("department_id"))}
    )


def _get_in_scope(report_id: str, user: dict[str, Any]) -> dict[str, Any]:
    report = access_repo.get_report(report_id)
    if not report:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Internal report not found")
    ensure_in_scope(user, report.get("department_id"))
    return report


@router.get("", response_model=list[InternalReportRead])
def list_reports(
    department_id: Optional[str] = Query(default=None),
    user: dict[str, Any] = Depends(require_capability(CAP_RECORDS_READ)),
) -> list[InternalReportRead]:
    """Solo el departamento del llamante (alcance, no denegación). Un admin
    ve todos y puede filtrar por `department_id`; un no-admin que pide otro
    departamento recibe 403, nunca una lista vacía."""
    scope = department_scope(user)
    if department_id is not None and scope is not None:
        ensure_in_scope(user, department_id)
    effective = scope if scope is not None else department_id
    return [_read(report) for report in access_repo.list_reports(effective)]


@router.post("", response_model=InternalReportRead, status_code=status.HTTP_201_CREATED)
def create_report(
    payload: InternalReportCreate,
    user: dict[str, Any] = Depends(require_capability(CAP_RECORDS_CREATE)),
) -> InternalReportRead:
    if payload.department_id is not None and not access_repo.get_department(payload.department_id):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"errors": [{"field": "department_id", "message": "Unknown department."}]},
        )
    if payload.department_id is not None:
        ensure_in_scope(user, payload.department_id)
        department_id = payload.department_id
    else:
        department_id = own_department(user)
    _phi_errors(("title", payload.title), ("body", payload.body))

    report = access_repo.create_report(
        department_id=department_id,
        title=payload.title.strip(),
        body=payload.body.strip(),
        created_by=str(user["id"]),
    )
    return _read(report)


@router.get("/{report_id}", response_model=InternalReportRead)
def get_report(
    report_id: str, user: dict[str, Any] = Depends(require_capability(CAP_RECORDS_READ))
) -> InternalReportRead:
    return _read(_get_in_scope(report_id, user))


@router.put("/{report_id}", response_model=InternalReportRead)
def update_report(
    report_id: str,
    payload: InternalReportUpdate,
    user: dict[str, Any] = Depends(require_capability(CAP_RECORDS_UPDATE)),
) -> InternalReportRead:
    _get_in_scope(report_id, user)
    _phi_errors(("title", payload.title), ("body", payload.body))
    updates = {key: value.strip() for key, value in payload.model_dump(exclude_none=True).items()}
    updated = access_repo.update_report(report_id, updates)
    return _read(updated or {})


@router.post("/{report_id}/approve", response_model=InternalReportRead)
def approve_report(
    report_id: str, user: dict[str, Any] = Depends(require_capability(CAP_RECORDS_APPROVE))
) -> InternalReportRead:
    """Aprobar es idempotente: aprobar dos veces conserva la primera firma."""
    report = _get_in_scope(report_id, user)
    if report.get("approved_at"):
        return _read(report)
    updated = access_repo.update_report(
        report_id,
        {"approved_by": str(user["id"]), "approved_at": datetime.now(timezone.utc).isoformat()},
    )
    audit_event(
        "internal_report_approved",
        actor_id=user.get("id"),
        role=user.get("role"),
        resource="internal_report",
        resource_id=report_id,
    )
    return _read(updated or report)


@router.delete("/{report_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_report(
    report_id: str, user: dict[str, Any] = Depends(require_capability(CAP_RECORDS_DELETE))
) -> Response:
    report = _get_in_scope(report_id, user)
    access_repo.delete_report(report["id"])
    audit_event(
        "internal_report_deleted",
        actor_id=user.get("id"),
        role=user.get("role"),
        resource="internal_report",
        resource_id=report_id,
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)

