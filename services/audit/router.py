"""/audit-log: visor del registro de auditoría. SOLO lectura.

No existe (ni debe existir) ninguna ruta POST/PUT/PATCH/DELETE aquí: las
entradas se escriben desde las acciones auditadas con `writer.record`. Hay un
test que recorre `app.routes` y falla si aparece una.

Quién ve qué (BRIEFING §3, aplicado SIEMPRE primero rol y después departamento):

| Ruta                          | Rol mínimo | Alcance                                    |
|-------------------------------|------------|--------------------------------------------|
| GET /audit-log                | supervisor | su departamento (Admin: todo)              |
| GET /audit-log/actors         | supervisor | ídem                                       |
| GET /audit-log/anomalies      | supervisor | ídem                                       |
| GET /audit-log/catalog        | supervisor | —                                          |
| GET /audit-log/integrity      | admin      | toda la cadena (solo devuelve el veredicto)|
| GET /audit-log/my-patients    | employee   | sus pacientes asignados                    |

Admin ve QUIÉN accedió a QUÉ; el registro no tiene contenido clínico que ver.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlmodel import Session, select

from access_models import CAP_AUDIT_READ, CAP_DEPARTMENTS_ALL, CAP_RECORDS_READ
from auth_repository import AuthRepository
from database import get_inventory_db
from permissions import department_scope, require_capability
from security import access_repo
from services.audit import anomalies, catalog, queries
from services.audit.chain import verify_chain
from services.audit.models import AuditLogEntry
from services.audit.schemas import (
    AnomaliesRead,
    AuditActor,
    AuditCatalogRead,
    AuditEntryRead,
    AuditPageRead,
    IntegrityRead,
    MyPatientAccess,
    MyPatientAccessPage,
    UnusualAccessRead,
)
from services.audit.writer import format_timestamp
from services.patients.models import PatientRecord

router = APIRouter(prefix="/audit-log", tags=["audit-log"])

User = dict[str, Any]

PROCESS_LABELS = {
    catalog.PROCESS_CLINICAL_DOCUMENTATION: "Asistente de documentación clínica",
    catalog.PROCESS_ONBOARDING_AGENT: "Agente de incorporaciones",
    catalog.PROCESS_FINANCIAL_CLOSE_AGENT: "Agente de cierre financiero",
    catalog.PROCESS_AUDIT_SEED: "Script de semilla de auditoría",
}


class _Labels:
    """Nombre visible de cada actor, resuelto al LEER (la entrada solo guarda
    el id: si mañana la persona cambia de nombre, el registro no cambia)."""

    def __init__(self) -> None:
        self._repo = AuthRepository()
        self._cache: dict[tuple[str, Optional[str]], str] = {}

    def __call__(self, actor_type: str, actor_id: Optional[str]) -> str:
        key = (actor_type, actor_id)
        if key not in self._cache:
            self._cache[key] = self._resolve(actor_type, actor_id)
        return self._cache[key]

    def _resolve(self, actor_type: str, actor_id: Optional[str]) -> str:
        if actor_type == catalog.ACTOR_PROCESS:
            return PROCESS_LABELS.get(actor_id or "", actor_id or "Proceso")
        if actor_type == catalog.ACTOR_ANONYMOUS or not actor_id:
            return "Sin cuenta identificada"
        profile = self._repo.get_profile_by_user_id(actor_id) or {}
        if profile.get("name"):
            return str(profile["name"])
        return f"Cuenta {actor_id[:8]}" if self._repo.get_user_by_id(actor_id) else f"Cuenta eliminada {actor_id[:8]}"


def _bad_request(field: str, message: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail={"errors": [{"field": field, "message": message}]})


def _parse_datetime(value: Optional[str], field: str) -> Optional[datetime]:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise _bad_request(field, "Use an ISO-8601 date-time.") from None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _read(entry: AuditLogEntry, labels: _Labels) -> AuditEntryRead:
    return AuditEntryRead(
        sequence=entry.sequence,
        entry_id=entry.entry_id,
        occurred_at=entry.occurred_at,
        actor_type=entry.actor_type,
        actor_id=entry.actor_id,
        actor_label=labels(entry.actor_type, entry.actor_id),
        on_behalf_of=entry.on_behalf_of,
        on_behalf_of_label=labels(catalog.ACTOR_HUMAN, entry.on_behalf_of) if entry.on_behalf_of else None,
        action=entry.action,
        resource_type=entry.resource_type,
        resource_id=entry.resource_id,
        patient_ref=entry.patient_ref,
        department=access_repo.department_code(entry.department_id) if entry.department_id else None,
        context=entry.context,
        origin=entry.origin,
        outcome=entry.outcome,
        details=json.loads(entry.details or "{}"),
        prev_hash=entry.prev_hash,
        entry_hash=entry.entry_hash,
    )


def _scope_label(scope: Optional[str]) -> str:
    return "all" if scope is None else (access_repo.department_code(scope) or scope)


@router.get("/catalog", response_model=AuditCatalogRead)
def audit_catalog(_: User = Depends(require_capability(CAP_AUDIT_READ))) -> AuditCatalogRead:
    return AuditCatalogRead(
        actions=dict(catalog.ACTIONS),
        resource_types=sorted(catalog.RESOURCE_TYPES),
        actor_types=sorted(catalog.ACTOR_TYPES),
        outcomes=sorted(catalog.OUTCOMES),
        purposes=dict(catalog.ACCESS_PURPOSES),
    )


@router.get("", response_model=AuditPageRead)
def list_entries(
    actor_type: Optional[str] = Query(default=None),
    actor_id: Optional[str] = Query(default=None, max_length=64),
    action: Optional[str] = Query(default=None),
    resource_type: Optional[str] = Query(default=None),
    resource_id: Optional[str] = Query(default=None, max_length=64),
    patient_ref: Optional[str] = Query(default=None, max_length=16),
    date_from: Optional[str] = Query(default=None, description="Inicio inclusivo (ISO-8601)"),
    date_to: Optional[str] = Query(default=None, description="Fin exclusivo (ISO-8601)"),
    limit: int = Query(default=queries.DEFAULT_PAGE_SIZE, ge=1, le=queries.MAX_PAGE_SIZE),
    before: Optional[int] = Query(default=None, ge=1, description="Cursor: entradas anteriores a esta"),
    user: User = Depends(require_capability(CAP_AUDIT_READ)),
    session: Session = Depends(get_inventory_db),
) -> AuditPageRead:
    if actor_type and actor_type not in catalog.ACTOR_TYPES:
        raise _bad_request("actor_type", "Unknown actor type.")
    if action and action not in catalog.ACTIONS:
        raise _bad_request("action", "Unknown action.")
    if resource_type and resource_type not in catalog.RESOURCE_TYPES:
        raise _bad_request("resource_type", "Unknown resource type.")
    start = _parse_datetime(date_from, "date_from")
    end = _parse_datetime(date_to, "date_to")
    if start and end and start >= end:
        raise _bad_request("date_to", "The end must be after the start.")

    scope = department_scope(user)
    result = queries.page(
        session,
        queries.AuditFilters(
            actor_type=actor_type,
            actor_id=actor_id,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            patient_ref=patient_ref,
            date_from=start,
            date_to=end,
            department_id=scope,
        ),
        limit=limit,
        before_sequence=before,
    )
    labels = _Labels()
    return AuditPageRead(
        items=[_read(entry, labels) for entry in result.items],
        next_cursor=result.next_cursor,
        total=result.total,
        scope=_scope_label(scope),
    )


@router.get("/actors", response_model=list[AuditActor])
def list_actors(
    user: User = Depends(require_capability(CAP_AUDIT_READ)),
    session: Session = Depends(get_inventory_db),
) -> list[AuditActor]:
    labels = _Labels()
    return [
        AuditActor(actor_type=actor_type, actor_id=actor_id, label=labels(actor_type, actor_id), entries=count)
        for actor_type, actor_id, count in queries.actors(session, department_id=department_scope(user))
    ]


@router.get("/anomalies", response_model=AnomaliesRead)
def unusual_access(
    user: User = Depends(require_capability(CAP_AUDIT_READ)),
    session: Session = Depends(get_inventory_db),
) -> AnomaliesRead:
    labels = _Labels()
    flagged = anomalies.detect_unusual_access(session, department_id=department_scope(user))
    return AnomaliesRead(
        window_hours=int(anomalies.UNUSUAL_WINDOW.total_seconds() // 3600),
        baseline_days=anomalies.BASELINE_DAYS,
        items=[
            UnusualAccessRead(
                actor_id=item.actor_id,
                actor_label=labels(catalog.ACTOR_HUMAN, item.actor_id),
                recent_count=item.recent_count,
                baseline_average=item.baseline_average,
            )
            for item in flagged
        ],
    )


@router.get("/integrity", response_model=IntegrityRead)
def verify_integrity(
    _: User = Depends(require_capability(CAP_DEPARTMENTS_ALL)),
    session: Session = Depends(get_inventory_db),
) -> IntegrityRead:
    """Recorre TODA la cadena y recalcula cada huella. Solo Admin: necesita
    leer entradas de todos los departamentos (aunque solo devuelve el veredicto)."""
    verdict = verify_chain(queries.iter_chain(session))
    return IntegrityRead(
        ok=verdict.ok,
        checked=verdict.checked,
        head_sequence=verdict.head_sequence,
        head_hash=verdict.head_hash,
        broken_at=verdict.broken_at,
        reason=verdict.reason,
        verified_at=format_timestamp(datetime.now(timezone.utc)),
    )


@router.get("/my-patients", response_model=MyPatientAccessPage)
def my_patients_access(
    limit: int = Query(default=queries.DEFAULT_PAGE_SIZE, ge=1, le=queries.MAX_PAGE_SIZE),
    before: Optional[int] = Query(default=None, ge=1),
    user: User = Depends(require_capability(CAP_RECORDS_READ)),
    session: Session = Depends(get_inventory_db),
) -> MyPatientAccessPage:
    """BRIEFING §3, Empleado: "quién accedió a mis propios pacientes asignados"."""
    refs = list(
        session.exec(select(PatientRecord.patient_ref).where(PatientRecord.assigned_user_id == user["id"])).all()
    )
    result = queries.page(session, queries.AuditFilters(patient_refs=refs), limit=limit, before_sequence=before)
    labels = _Labels()
    return MyPatientAccessPage(
        items=[
            MyPatientAccess(
                sequence=entry.sequence,
                occurred_at=entry.occurred_at,
                actor_type=entry.actor_type,
                actor_label=labels(entry.actor_type, entry.actor_id),
                action=entry.action,
                patient_ref=entry.patient_ref,
                context=entry.context,
                outcome=entry.outcome,
            )
            for entry in result.items
        ],
        next_cursor=result.next_cursor,
        total=result.total,
    )
