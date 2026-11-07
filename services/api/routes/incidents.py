from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Query, status

from packages.shared.incidents_validation import (
    INCIDENT_BRANCHES,
    INCIDENT_CATEGORIES,
    INCIDENT_ORIGINS,
    INCIDENT_STATUSES,
    REQUIRED_INCIDENT_FIELDS,
    TERMINAL_STATUSES,
    validate_incident_payload,
    validate_status_confirmation,
    validate_status_transition,
)

from access_models import CAP_RECORDS_CREATE, CAP_RECORDS_READ, CAP_RECORDS_UPDATE
from audit_log import audit_event
from cache import cache
from incident_repository import IncidentRepository
from models import IncidentListItem, IncidentRead, IncidentSummary
from permissions import can_access_department, department_scope, ensure_in_scope, own_department, require_capability
from security import access_repo, get_current_user

router = APIRouter(prefix="/api/incidents", tags=["incidents"], dependencies=[Depends(get_current_user)])
repo = IncidentRepository()

SUMMARY_CACHE_KEY = "incidents_summary"
SUMMARY_CACHE_TTL_SECONDS = 60

# Roles y Permisos: las incidencias tienen alcance departamental. Las nuevas
# llevan el departamento de quien las crea; las anteriores al ticket se
# resuelven por categoría al leerlas (AccessRepository.incident_department_id).
# Rol: crear/leer = employee, cambiar estado = supervisor.


def _with_department(record: dict[str, Any]) -> dict[str, Any]:
    return {**record, "department_id": access_repo.incident_department_id(record)}


def _in_scope(records: list[dict[str, Any]], user: dict[str, Any]) -> list[dict[str, Any]]:
    # La decisión es la central (permissions.can_access_department), nunca
    # una comparación aquí: un admin ve todas, el resto solo las suyas.
    enriched = [_with_department(record) for record in records]
    return [record for record in enriched if can_access_department(user, record["department_id"])]


def _summary_key(scope: str | None) -> str:
    # Una entrada por alcance: sin esto, el resumen de un departamento se
    # serviría a otro durante el TTL.
    return f"{SUMMARY_CACHE_KEY}:{scope or 'all'}"


def _field_errors(errors: list[dict[str, str]]) -> HTTPException:
    return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail={"errors": errors})


@router.post("", response_model=IncidentRead, status_code=status.HTTP_201_CREATED)
def create_incident(
    payload: dict[str, Any] = Body(...),
    user: dict[str, Any] = Depends(require_capability(CAP_RECORDS_CREATE)),
) -> IncidentRead:
    department_id = own_department(user)
    errors = validate_incident_payload(payload)
    if errors:
        raise _field_errors(errors)

    record = {field: str(payload[field]).strip() for field in REQUIRED_INCIDENT_FIELDS}
    record["department_id"] = department_id
    created = repo.create(record)
    cache.invalidate_prefix(SUMMARY_CACHE_KEY)
    return IncidentRead.model_validate(_with_department(created))


@router.get("", response_model=list[IncidentListItem])
def list_incidents(
    status_filter: str | None = Query(default=None, alias="status"),
    origin: str | None = Query(default=None),
    branch: str | None = Query(default=None),
    category: str | None = Query(default=None),
    user: dict[str, Any] = Depends(require_capability(CAP_RECORDS_READ)),
) -> list[IncidentListItem]:
    """Listado con proyeccion propia: sin `updated_at`, que la tabla no
    muestra. El detalle GET /{id} conserva IncidentRead completo."""
    filters = (
        ("status", status_filter, INCIDENT_STATUSES),
        ("origin", origin, INCIDENT_ORIGINS),
        ("branch", branch, INCIDENT_BRANCHES),
        ("category", category, INCIDENT_CATEGORIES),
    )
    errors = [
        {
            "field": field,
            "message": f"'{value}' is not a valid value for '{field}'. Allowed values: {', '.join(allowed)}.",
        }
        for field, value, allowed in filters
        if value is not None and value not in allowed
    ]
    if errors:
        raise _field_errors(errors)

    department_scope(user)  # 403 explícito si la cuenta no tiene departamento
    results = _in_scope(repo.list(status=status_filter, origin=origin, branch=branch, category=category), user)
    return [IncidentListItem.model_validate(item) for item in results]


@router.get("/summary", response_model=IncidentSummary)
def incident_summary(user: dict[str, Any] = Depends(require_capability(CAP_RECORDS_READ))) -> IncidentSummary:
    scope = department_scope(user)
    cached = cache.get(_summary_key(scope))
    if cached is not None:
        return cached

    counts = repo.summary(None if scope is None else _in_scope(repo.list(), user))

    def merged(allowed: tuple[str, ...], actual: dict[str, int]) -> dict[str, int]:
        base = {value: 0 for value in allowed}
        base.update(actual)
        return base

    result = IncidentSummary(
        total=counts["total"],
        by_status=merged(INCIDENT_STATUSES, counts["by_status"]),
        by_category=merged(INCIDENT_CATEGORIES, counts["by_category"]),
        by_origin=merged(INCIDENT_ORIGINS, counts["by_origin"]),
        by_branch=merged(INCIDENT_BRANCHES, counts["by_branch"]),
    )
    cache.set(_summary_key(scope), result, SUMMARY_CACHE_TTL_SECONDS)
    return result


@router.get("/{incident_id}", response_model=IncidentRead)
def get_incident(
    incident_id: int, user: dict[str, Any] = Depends(require_capability(CAP_RECORDS_READ))
) -> IncidentRead:
    found = repo.get_by_id(incident_id)
    if not found:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Incident not found")
    found = _with_department(found)
    ensure_in_scope(user, found["department_id"])
    return IncidentRead.model_validate(found)


@router.patch("/{incident_id}/status", response_model=IncidentRead)
def patch_incident_status(
    incident_id: int,
    payload: dict[str, Any] = Body(...),
    user: dict[str, Any] = Depends(require_capability(CAP_RECORDS_UPDATE)),
) -> IncidentRead:
    """Cambia el estado siguiendo el ciclo de vida.

    Clase NIST (BRIEFING de ciberseguridad §5, acciones irreversibles): pasar a
    un estado final (`resolved`/`discarded`) exige `"confirm": true`, y todo
    cambio deja una línea en `healthcore.audit` (quién en seudónimo, de qué
    estado a cuál; nunca título ni descripción). Se valida primero que la
    transición sea posible y después la confirmación: pedir algo imposible
    da su error de siempre, no un "falta confirmar".
    """
    new_status = payload.get("status")
    if not isinstance(new_status, str) or not new_status.strip():
        raise _field_errors([{"field": "status", "message": "The field 'status' is required."}])
    new_status = new_status.strip()

    found = repo.get_by_id(incident_id)
    if not found:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Incident not found")
    ensure_in_scope(user, access_repo.incident_department_id(found))

    transition_error = validate_status_transition(found["status"], new_status)
    if transition_error:
        raise _field_errors([{"field": "status", "message": transition_error}])

    confirmation_error = validate_status_confirmation(new_status, payload.get("confirm"))
    if confirmation_error:
        raise _field_errors([{"field": "confirm", "message": confirmation_error}])

    updated = repo.update_status(incident_id, new_status)
    cache.invalidate_prefix(SUMMARY_CACHE_KEY)
    audit_event(
        "incident_status_changed",
        actor_id=user.get("id"),
        role=user.get("role"),
        incident_id=incident_id,
        from_status=found["status"],
        to_status=new_status,
        confirmed=new_status in TERMINAL_STATUSES,
    )
    return IncidentRead.model_validate(_with_department(updated or found))
