"""Tool `lookup_incident`: estado en vivo de las incidencias del gestor de incidencias.

Lee del gestor que ya existe (`services/api/incident_repository.py`, el
mismo repositorio que sirve GET /api/incidents y GET /api/incidents/{id}) en
el propio proceso: el agente vive en la misma API, así que no hay token de
servicio que pasar y no hay riesgo de que la API se llame a sí misma. Nunca
datos simulados.

Solo lectura: únicamente `get_by_id()` y `list()`. Ni `create()` ni
`update_status()` (test_agent_tools.py lo fija con un repositorio que falla
ante cualquier otro método).

Minimización de datos (HIPAA / UK GDPR): la salida NO incluye `title` ni
`description`. Son texto libre que puede contener datos de pacientes, mismo
criterio que `incident_created` en telemetría; al modelo y al trace solo
llegan estado, categoría, origen, sede y fechas.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Callable, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, PositiveInt, field_validator, model_validator

from packages.shared.incidents_validation import (
    INCIDENT_BRANCHES,
    INCIDENT_CATEGORIES,
    INCIDENT_ORIGINS,
    INCIDENT_STATUSES,
)
from services.agent.tools.base import ToolNotFound, ToolResult, run_tool

TOOL_NAME = "lookup_incident"
TIMEOUT_S = 3.0
MAX_RESULTS = 10
FILTER_FIELDS = ("status", "category", "branch", "origin")
ALLOWED_VALUES = {
    "status": INCIDENT_STATUSES,
    "category": INCIDENT_CATEGORIES,
    "branch": INCIDENT_BRANCHES,
    "origin": INCIDENT_ORIGINS,
}
# Para que el modelo redacte en español sin inventar la traducción del estado.
STATUS_LABELS_ES = {"open": "abierta", "in_progress": "en curso", "resolved": "resuelta", "discarded": "descartada"}


class IncidentLookupInput(BaseModel):
    """Una incidencia concreta (`ticket_id`) O una búsqueda por filtros, nunca ambas."""

    model_config = ConfigDict(extra="forbid")

    ticket_id: Optional[PositiveInt] = None
    status: Optional[str] = None
    category: Optional[str] = None
    branch: Optional[str] = None
    origin: Optional[str] = None

    @field_validator(*FILTER_FIELDS)
    @classmethod
    def allowed_value(cls, value: Optional[str], info: Any) -> Optional[str]:
        if value is not None and value not in ALLOWED_VALUES[info.field_name]:
            raise ValueError(f"'{value}' no es un valor válido de {info.field_name}")
        return value

    @model_validator(mode="after")
    def one_mode(self) -> "IncidentLookupInput":
        has_filters = any(getattr(self, field) is not None for field in FILTER_FIELDS)
        if self.ticket_id is None and not has_filters:
            raise ValueError("indica un ticket_id o al menos un filtro")
        if self.ticket_id is not None and has_filters:
            raise ValueError("ticket_id y filtros no se combinan")
        return self


class IncidentRecord(BaseModel):
    """Los campos operativos de IncidentRead, sin texto libre."""

    id: int
    status: str
    status_label_es: str
    category: str
    origin: str
    branch: str
    created_at: datetime
    updated_at: Optional[datetime] = None


class IncidentLookupOutput(BaseModel):
    mode: Literal["by_id", "search"]
    total: int = Field(description="Incidencias que cumplen la consulta (puede superar las devueltas)")
    incidents: List[IncidentRecord]


def _default_repository() -> Any:
    from incident_repository import IncidentRepository

    return IncidentRepository()


def _to_record(entity: dict) -> IncidentRecord:
    return IncidentRecord(
        id=entity["id"],
        status=entity["status"],
        status_label_es=STATUS_LABELS_ES.get(entity["status"], entity["status"]),
        category=entity["category"],
        origin=entity["origin"],
        branch=entity["branch"],
        created_at=entity["created_at"],
        updated_at=entity.get("updated_at"),
    )


def query_incidents(payload: IncidentLookupInput, repository: Any) -> IncidentLookupOutput:
    """La consulta en sí, sin timeout: solo métodos de lectura del repositorio."""
    if payload.ticket_id is not None:
        entity = repository.get_by_id(payload.ticket_id)
        if entity is None:
            raise ToolNotFound(f"incident {payload.ticket_id}")
        return IncidentLookupOutput(mode="by_id", total=1, incidents=[_to_record(entity)])

    matches = repository.list(**{field: getattr(payload, field) for field in FILTER_FIELDS})
    return IncidentLookupOutput(
        mode="search",
        total=len(matches),
        incidents=[_to_record(entity) for entity in matches[:MAX_RESULTS]],
    )


def lookup_incident(
    payload: IncidentLookupInput,
    *,
    repository_factory: Callable[[], Any] = _default_repository,
    timeout_s: float = TIMEOUT_S,
) -> ToolResult:
    """Punto de entrada de la tool: nunca lanza, siempre devuelve un ToolResult."""
    args = payload.model_dump(exclude_none=True)
    return run_tool(TOOL_NAME, args, lambda: query_incidents(payload, repository_factory()), timeout_s=timeout_s)
