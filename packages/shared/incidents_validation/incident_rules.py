"""Validation rules for the HealthCore centralized incident manager.

Shared between the API service and the seed script so both enforce the same
required fields, allowed values, and lifecycle transitions.
"""

from __future__ import annotations

from typing import Any, Dict, List, Mapping, Tuple

INCIDENT_STATUSES: Tuple[str, ...] = ("open", "in_progress", "resolved", "discarded")

INCIDENT_ORIGINS: Tuple[str, ...] = ("customer", "branch", "internal")

INCIDENT_CATEGORIES: Tuple[str, ...] = (
    "clinical_equipment",
    "it_system",
    "billing_error",
    "compliance_breach",
    "patient_experience",
    "staff_issue",
    "facility_issue",
    "referral_issue",
    "other",
)

INCIDENT_BRANCHES: Tuple[str, ...] = (
    "central",
    "austin_north",
    "dallas_uptown",
    "houston_med_center",
    "san_antonio_west",
    "miami_brickell",
    "miami_doral",
    "orlando_east",
    "tampa_bay",
    "atlanta_midtown",
    "savannah",
    "london_city",
    "london_west",
    "manchester_central",
)

TITLE_MAX_LENGTH = 120

STATUS_TRANSITIONS: Dict[str, Tuple[str, ...]] = {
    "open": ("in_progress", "discarded"),
    "in_progress": ("resolved", "discarded"),
    "resolved": (),
    "discarded": (),
}

REQUIRED_INCIDENT_FIELDS: Tuple[str, ...] = (
    "title",
    "description",
    "category",
    "status",
    "origin",
    "branch",
)

_ALLOWED_VALUES: Dict[str, Tuple[str, ...]] = {
    "category": INCIDENT_CATEGORIES,
    "status": INCIDENT_STATUSES,
    "origin": INCIDENT_ORIGINS,
    "branch": INCIDENT_BRANCHES,
}


def validate_incident_payload(payload: Mapping[str, Any]) -> List[Dict[str, str]]:
    """Validate an incident payload; return a list of {field, message} errors (empty if valid)."""
    errors: List[Dict[str, str]] = []

    for field in REQUIRED_INCIDENT_FIELDS:
        value = payload.get(field)
        if not isinstance(value, str) or not value.strip():
            errors.append({"field": field, "message": f"The field '{field}' is required."})

    failed_fields = {error["field"] for error in errors}

    title = payload.get("title")
    if "title" not in failed_fields and isinstance(title, str) and len(title.strip()) > TITLE_MAX_LENGTH:
        errors.append(
            {
                "field": "title",
                "message": f"The field 'title' must be at most {TITLE_MAX_LENGTH} characters long.",
            }
        )

    for field, allowed in _ALLOWED_VALUES.items():
        value = payload.get(field)
        if field in failed_fields or not isinstance(value, str):
            continue
        if value.strip() not in allowed:
            errors.append(
                {
                    "field": field,
                    "message": (
                        f"'{value}' is not a valid value for '{field}'. "
                        f"Allowed values: {', '.join(allowed)}."
                    ),
                }
            )

    return errors


def validate_status_transition(current_status: str, new_status: str) -> str | None:
    """Return an error message if the lifecycle transition is not allowed, else None."""
    if new_status not in INCIDENT_STATUSES:
        return (
            f"'{new_status}' is not a valid status. "
            f"Allowed values: {', '.join(INCIDENT_STATUSES)}."
        )

    if new_status == current_status:
        return f"The incident is already in status '{current_status}'."

    allowed = STATUS_TRANSITIONS.get(current_status, ())
    if new_status not in allowed:
        if not allowed:
            return f"Status '{current_status}' is final and cannot be changed."
        return (
            f"Cannot change status from '{current_status}' to '{new_status}'. "
            f"Allowed transitions: {', '.join(allowed)}."
        )

    return None
