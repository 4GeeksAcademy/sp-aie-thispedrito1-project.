"""Quién ve qué parte de la ficha de un paciente.

- **Contenido clínico** (diagnóstico, notas, borrador de documentación): solo
  el equipo de cuidado, es decir, el departamento del paciente. Ser Admin NO
  da acceso clínico a otros departamentos: el BRIEFING de auditoría §3 dice
  que el Admin ve "quién accedió a qué, no el contenido clínico".
- **Facturación y reclamaciones**: el equipo de cuidado (solo lectura) y
  Revenue Cycle (lectura y escritura), para cualquier paciente.

El rol (capacidad) ya lo comprobó `require_capability` en la ruta; esto
decide solo el eje de datos.
"""

from __future__ import annotations

from typing import Any

from access_models import department_id_for

SECTION_CLINICAL = "clinical"
SECTION_BILLING = "billing"

REVENUE_CYCLE_ID = department_id_for("revenue_cycle")

CLAIM_REASON_CODES = {
    "approve": ("covered_service", "medically_necessary"),
    "deny": ("not_covered", "missing_documentation", "duplicate_claim", "eligibility"),
}


def is_care_team(user: dict[str, Any], patient_department_id: str) -> bool:
    own = user.get("department_id")
    return bool(own) and own == patient_department_id


def is_revenue_cycle(user: dict[str, Any]) -> bool:
    return user.get("department_id") == REVENUE_CYCLE_ID


def visible_sections(user: dict[str, Any], patient_department_id: str) -> list[str]:
    sections = []
    if is_care_team(user, patient_department_id):
        sections.append(SECTION_CLINICAL)
    if is_care_team(user, patient_department_id) or is_revenue_cycle(user):
        sections.append(SECTION_BILLING)
    return sections
