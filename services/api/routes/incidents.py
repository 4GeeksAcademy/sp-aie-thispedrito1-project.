from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Query, status

from packages.shared.incidents_validation import (
    INCIDENT_BRANCHES,
    INCIDENT_CATEGORIES,
    INCIDENT_ORIGINS,
    INCIDENT_STATUSES,
    REQUIRED_INCIDENT_FIELDS,
    validate_incident_payload,
    validate_status_transition,
)

from cache import cache
from incident_repository import IncidentRepository
from models import IncidentRead, IncidentSummary
from security import get_current_user

router = APIRouter(prefix="/api/incidents", tags=["incidents"], dependencies=[Depends(get_current_user)])
repo = IncidentRepository()

SUMMARY_CACHE_KEY = "incidents_summary"
SUMMARY_CACHE_TTL_SECONDS = 60


def _field_errors(errors: list[dict[str, str]]) -> HTTPException:
    return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail={"errors": errors})


@router.post("", response_model=IncidentRead, status_code=status.HTTP_201_CREATED)
def create_incident(payload: dict[str, Any] = Body(...)) -> IncidentRead:
    errors = validate_incident_payload(payload)
    if errors:
        raise _field_errors(errors)

    record = {field: str(payload[field]).strip() for field in REQUIRED_INCIDENT_FIELDS}
    created = repo.create(record)
    cache.invalidate(SUMMARY_CACHE_KEY)
    return IncidentRead.model_validate(created)


@router.get("", response_model=list[IncidentRead])
def list_incidents(
    status_filter: str | None = Query(default=None, alias="status"),
    origin: str | None = Query(default=None),
    branch: str | None = Query(default=None),
    category: str | None = Query(default=None),
) -> list[IncidentRead]:
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

    results = repo.list(status=status_filter, origin=origin, branch=branch, category=category)
    return [IncidentRead.model_validate(item) for item in results]


@router.get("/summary", response_model=IncidentSummary)
def incident_summary() -> IncidentSummary:
    cached = cache.get(SUMMARY_CACHE_KEY)
    if cached is not None:
        return cached

    counts = repo.summary()

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
    cache.set(SUMMARY_CACHE_KEY, result, SUMMARY_CACHE_TTL_SECONDS)
    return result


@router.get("/{incident_id}", response_model=IncidentRead)
def get_incident(incident_id: int) -> IncidentRead:
    found = repo.get_by_id(incident_id)
    if not found:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Incident not found")
    return IncidentRead.model_validate(found)


@router.patch("/{incident_id}/status", response_model=IncidentRead)
def patch_incident_status(incident_id: int, payload: dict[str, Any] = Body(...)) -> IncidentRead:
    new_status = payload.get("status")
    if not isinstance(new_status, str) or not new_status.strip():
        raise _field_errors([{"field": "status", "message": "The field 'status' is required."}])

    found = repo.get_by_id(incident_id)
    if not found:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Incident not found")

    transition_error = validate_status_transition(found["status"], new_status.strip())
    if transition_error:
        raise _field_errors([{"field": "status", "message": transition_error}])

    updated = repo.update_status(incident_id, new_status.strip())
    cache.invalidate(SUMMARY_CACHE_KEY)
    return IncidentRead.model_validate(updated)
