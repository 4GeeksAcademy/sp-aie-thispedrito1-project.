"""Tool `lookup_incident`: estado en vivo de las incidencias, VÍA EL SERVIDOR MCP.

Migrada en el ticket del servidor MCP (rama `feature/mcp-oauth-tools`): el
agente ya no importa `IncidentRepository` ni toca la TinyDB. Llama a las
tools `incidents_get` / `incidents_search` del servidor MCP de HealthCore
(`mcps/healthcore`) como cliente autenticado con OAuth (ver
`services/agent/mcp_client.py`), y el servidor a su vez al Incidents Manager
por HTTP. Es el ÚNICO camino del agente hacia las incidencias: la versión en
proceso (`query_incidents` + `_default_repository`) se eliminó, no se dejó
desactivada, para que no existan dos rutas.

Lo que NO cambió, a propósito, para no romper el enrutamiento RAG/tools ni
los traces grabados: el nombre de la tool y del nodo (`lookup_incident`), el
contrato de entrada (`IncidentLookupInput`, que valida el planificador) y el
de salida (`IncidentLookupOutput`, que lee `evidence.py`).

Minimización de datos (HIPAA / UK GDPR): el servidor MCP ya no devuelve
`title` ni `description`, así que al modelo y al trace solo llegan estado,
categoría, origen, sede y fechas.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Callable, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, PositiveInt, field_validator, model_validator

from packages.shared.incidents_validation import (
    INCIDENT_BRANCHES,
    INCIDENT_CATEGORIES,
    INCIDENT_ORIGINS,
    INCIDENT_STATUSES,
)
from services.agent.tools.base import ToolNotFound, ToolResult, run_tool

TOOL_NAME = "lookup_incident"
# Antes 3 s (lectura en proceso). Ahora hay red: token de Logto (cacheado),
# tools/list + tools/call contra el MCP y la llamada del MCP a la API.
TIMEOUT_S = 5.0
MAX_RESULTS = 10
VIA = "mcp"
MCP_GET_TOOL = "incidents_get"
MCP_SEARCH_TOOL = "incidents_search"
FILTER_FIELDS = ("status", "category", "branch", "origin")
ALLOWED_VALUES = {
    "status": INCIDENT_STATUSES,
    "category": INCIDENT_CATEGORIES,
    "branch": INCIDENT_BRANCHES,
    "origin": INCIDENT_ORIGINS,
}
# Para que el modelo redacte en español sin inventar la traducción del estado.
STATUS_LABELS_ES = {"open": "abierta", "in_progress": "en curso", "resolved": "resuelta", "discarded": "descartada"}

McpCall = Callable[[str, Dict[str, Any]], Dict[str, Any]]


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
    """Los campos operativos de una incidencia, sin texto libre."""

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


def _default_mcp_call(name: str, args: Dict[str, Any]) -> Dict[str, Any]:
    from services.agent import mcp_client

    return mcp_client.call_tool(name, args)


def _to_record(entity: Dict[str, Any]) -> IncidentRecord:
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


def query_incidents_via_mcp(payload: IncidentLookupInput, mcp_call: McpCall) -> IncidentLookupOutput:
    """Traduce la consulta del planificador a la tool MCP que corresponde."""
    from services.agent.mcp_client import McpToolCallError

    if payload.ticket_id is not None:
        try:
            entity = mcp_call(MCP_GET_TOOL, {"incident_id": payload.ticket_id})
        except McpToolCallError as exc:
            if exc.code == "not_found":
                raise ToolNotFound(f"incident {payload.ticket_id}") from exc
            raise
        return IncidentLookupOutput(mode="by_id", total=1, incidents=[_to_record(entity)])

    filters = {field: getattr(payload, field) for field in FILTER_FIELDS if getattr(payload, field) is not None}
    found = mcp_call(MCP_SEARCH_TOOL, {**filters, "limit": MAX_RESULTS})
    return IncidentLookupOutput(
        mode="search",
        total=found["total"],
        incidents=[_to_record(entity) for entity in found["incidents"][:MAX_RESULTS]],
    )


def lookup_incident(
    payload: IncidentLookupInput,
    *,
    mcp_call: Optional[McpCall] = None,
    timeout_s: float = TIMEOUT_S,
) -> ToolResult:
    """Punto de entrada de la tool: nunca lanza, siempre devuelve un ToolResult.
    Un error del servidor MCP (sin permiso, caído, token rechazado) acaba en
    `unavailable` y el grafo responde con el fallback honesto."""
    call = mcp_call or _default_mcp_call
    args = payload.model_dump(exclude_none=True)
    return run_tool(TOOL_NAME, args, lambda: query_incidents_via_mcp(payload, call), timeout_s=timeout_s, via=VIA)
