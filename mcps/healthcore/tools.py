"""Tools del servidor MCP: Incidents Manager (gestión) e inventario (solo lectura).

Todo lo que un cliente necesita saber para usarlas va en el discovery
(`tools/list`): nombre, título, descripción, esquema de entrada con los
valores permitidos, esquema de salida y anotaciones (`readOnlyHint`, ...).
Los valores de dominio salen de `packages/shared/incidents_validation`, la
misma fuente que usa la API: si allí cambia una categoría, aquí también.

Cada invocación pasa por `_invoke`, que:
1. lee la identidad que dejó MCP Auth (cliente, sujeto, scopes),
2. comprueba el scope de la tool (`scopes.ensure_scopes`),
3. ejecuta la tool,
4. escribe UNA línea de auditoría JSON con tool, cliente y resultado.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Annotated, Any, Awaitable, Callable, Dict, List, Optional

from mcp.server.fastmcp import Context, FastMCP
from mcp.types import ToolAnnotations
from pydantic import BaseModel, Field

from mcps.healthcore.api_client import HealthCoreApi, InventoryReader, ReadOnlyViolation
from mcps.healthcore.errors import READ_ONLY_RESOURCE, UPSTREAM_UNAVAILABLE, VALIDATION_ERROR, McpToolError
from mcps.healthcore.scopes import ensure_scopes, required_scopes
from packages.shared.incidents_validation import (
    INCIDENT_BRANCHES,
    INCIDENT_CATEGORIES,
    INCIDENT_ORIGINS,
    INCIDENT_STATUSES,
)
from packages.shared.incidents_validation.incident_rules import STATUS_TRANSITIONS

audit_logger = logging.getLogger("healthcore.mcp.audit")

MAX_SEARCH_RESULTS = 50
DEFAULT_SEARCH_RESULTS = 20
MAX_SUPPLIES = 50

# --- Inventario: acciones permitidas y acciones de escritura rechazadas --------

READ_ACTIONS = ("list_supplies", "get_supply")
# Escrituras que la API de inventario SÍ tiene (POST /inventory/products,
# POST /inventory/orders/inbound|outbound, PATCH .../stock) y que este
# servidor rechaza a propósito, con su propio código de error.
WRITE_ACTIONS = (
    "create_supply",
    "update_supply",
    "delete_supply",
    "register_inbound",
    "register_outbound",
    "adjust_stock",
    "set_stock",
)
WRITE_VERB_PREFIXES = ("create", "update", "delete", "remove", "add", "set", "adjust", "register", "patch", "put", "post", "edit", "modify", "write", "consume")


def classify_inventory_action(action: str) -> str:
    """'read' | 'write' | 'unknown'. Cualquier cosa con forma de escritura
    cuenta como escritura aunque no esté en la lista: mejor un rechazo
    explícito de más que dejar pasar un verbo nuevo como "desconocido"."""
    normalized = (action or "").strip().lower()
    if normalized in READ_ACTIONS:
        return "read"
    if normalized in WRITE_ACTIONS or normalized.startswith(WRITE_VERB_PREFIXES):
        return "write"
    return "unknown"


# --- Contratos de salida (aparecen como outputSchema en el discovery) --------


class IncidentRecord(BaseModel):
    """Campos operativos de una incidencia. SIN `title` ni `description`:
    son texto libre que puede contener datos de pacientes (HIPAA / UK GDPR),
    mismo criterio que el agente y la telemetría."""

    id: int = Field(description="Número de la incidencia (ticket) en el Incidents Manager")
    status: str = Field(description=f"Estado actual: {', '.join(INCIDENT_STATUSES)}")
    category: str
    origin: str
    branch: str = Field(description="Sede de HealthCore")
    created_at: str = Field(description="Fecha de creación, ISO 8601")
    updated_at: Optional[str] = Field(default=None, description="Última actualización, ISO 8601 (no viene en búsquedas)")
    allowed_next_statuses: List[str] = Field(description="Estados a los que puede pasar con incidents_update_status")


class IncidentSearchResult(BaseModel):
    total: int = Field(description="Incidencias que cumplen los filtros (puede superar las devueltas)")
    returned: int
    incidents: List[IncidentRecord]


class SupplyRecord(BaseModel):
    id: int = Field(description="supply_id del insumo")
    name: str
    sku: str
    category: str
    unit: str
    country: str = Field(description="US o UK")
    current_stock: int = Field(description="Stock actual calculado (entradas - salidas) en toda la red")
    expiry_date: Optional[str] = None


class InventoryQueryResult(BaseModel):
    action: str
    total: int = Field(description="Insumos que cumplen la consulta (puede superar los devueltos)")
    supplies: List[SupplyRecord]


def _incident(payload: Dict[str, Any]) -> IncidentRecord:
    return IncidentRecord(
        id=payload["id"],
        status=payload["status"],
        category=payload["category"],
        origin=payload["origin"],
        branch=payload["branch"],
        created_at=str(payload["created_at"]),
        updated_at=str(payload["updated_at"]) if payload.get("updated_at") else None,
        allowed_next_statuses=list(STATUS_TRANSITIONS.get(payload["status"], ())),
    )


def _supply(payload: Dict[str, Any]) -> SupplyRecord:
    return SupplyRecord(
        id=payload["id"],
        name=payload["name"],
        sku=payload["sku"],
        category=payload["category"],
        unit=payload["unit"],
        country=payload["country"],
        current_stock=payload["current_stock"],
        expiry_date=str(payload["expiry_date"]) if payload.get("expiry_date") else None,
    )


# --- Identidad, permisos y auditoría ------------------------------------------


def _auth_info(ctx: Context) -> Any:
    """AuthInfo que dejó `AuthInfoToScope` (server.py) en el scope ASGI.
    None si la petición no pasó por MCP Auth: entonces no hay scopes y toda
    tool falla cerrada con `insufficient_scope`."""
    request = getattr(ctx.request_context, "request", None)
    scope = getattr(request, "scope", None) or {}
    return (scope.get("state") or {}).get("auth_info")


def _audit(tool: str, auth: Any, outcome: str, started: float, args: Dict[str, Any]) -> None:
    audit_logger.info(
        json.dumps(
            {
                "event": "mcp_tool_call",
                "tool": tool,
                "client_id": getattr(auth, "client_id", None),
                "subject": getattr(auth, "subject", None),
                "outcome": outcome,
                "duration_ms": round((time.perf_counter() - started) * 1000, 1),
                "args": args,
            },
            ensure_ascii=False,
        )
    )


async def _invoke(tool: str, ctx: Context, log_args: Dict[str, Any], action: Callable[[], Awaitable[Any]]) -> Any:
    started = time.perf_counter()
    auth = _auth_info(ctx)
    outcome = "ok"
    try:
        ensure_scopes(tool, getattr(auth, "scopes", None))
        return await action()
    except McpToolError as exc:
        outcome = exc.code
        raise
    except ReadOnlyViolation as exc:
        outcome = READ_ONLY_RESOURCE
        raise McpToolError(READ_ONLY_RESOURCE, str(exc)) from exc
    except Exception as exc:  # bug o fallo imprevisto: nunca un traceback al cliente
        outcome = UPSTREAM_UNAVAILABLE
        audit_logger.error("Fallo inesperado en la tool %s: %s", tool, type(exc).__name__)
        raise McpToolError(UPSTREAM_UNAVAILABLE, "Error interno del servidor MCP.") from exc
    finally:
        _audit(tool, auth, outcome, started, log_args)


def _scopes_note(tool: str) -> str:
    return f" Requiere el scope OAuth: {', '.join(sorted(required_scopes(tool)))}."


def _enum(values: Any) -> Dict[str, Any]:
    return {"enum": list(values)}


def _blank_to_none(value: Optional[str]) -> Optional[str]:
    """Un filtro opcional vacío significa "sin filtro". Algunos clientes MCP
    (MCP Playground, comprobado) envían `""` en vez de omitir el campo; sin
    esto, la API lo rechazaba como categoría/origen no válidos."""
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


# --- Registro de tools --------------------------------------------------------


def register_tools(mcp: FastMCP, api: HealthCoreApi) -> None:
    inventory = InventoryReader(api)

    @mcp.tool(
        name="incidents_get",
        title="Consultar una incidencia",
        description=(
            "Devuelve el estado actual y los datos operativos de UNA incidencia (ticket) del Incidents Manager "
            "de HealthCore por su número, incluidos los estados a los que puede pasar. No devuelve título ni "
            "descripción (texto libre con posibles datos de pacientes). Error `not_found` si no existe."
            + _scopes_note("incidents_get")
        ),
        annotations=ToolAnnotations(readOnlyHint=True, idempotentHint=True, openWorldHint=False),
    )
    async def incidents_get(
        incident_id: Annotated[int, Field(ge=1, description="Número de la incidencia (ticket)")],
        ctx: Context,
    ) -> IncidentRecord:
        async def run() -> IncidentRecord:
            return _incident(await api.request("GET", f"/api/incidents/{incident_id}"))

        return await _invoke("incidents_get", ctx, {"incident_id": incident_id}, run)

    @mcp.tool(
        name="incidents_search",
        title="Buscar incidencias",
        description=(
            "Lista incidencias del Incidents Manager filtrando por estado, categoría, sede y/o origen (todos "
            "opcionales y combinables). Devuelve el total y hasta `limit` incidencias, sin título ni descripción. "
            "Un valor fuera de los permitidos da `validation_error`." + _scopes_note("incidents_search")
        ),
        annotations=ToolAnnotations(readOnlyHint=True, idempotentHint=True, openWorldHint=False),
    )
    async def incidents_search(
        ctx: Context,
        status: Annotated[Optional[str], Field(description="Estado", json_schema_extra=_enum(INCIDENT_STATUSES))] = None,
        category: Annotated[Optional[str], Field(description="Categoría", json_schema_extra=_enum(INCIDENT_CATEGORIES))] = None,
        branch: Annotated[Optional[str], Field(description="Sede", json_schema_extra=_enum(INCIDENT_BRANCHES))] = None,
        origin: Annotated[Optional[str], Field(description="Origen", json_schema_extra=_enum(INCIDENT_ORIGINS))] = None,
        limit: Annotated[int, Field(ge=1, le=MAX_SEARCH_RESULTS, description="Máximo de incidencias a devolver")] = DEFAULT_SEARCH_RESULTS,
    ) -> IncidentSearchResult:
        raw = {"status": status, "category": category, "branch": branch, "origin": origin}
        filters = {k: v for k, v in ((k, _blank_to_none(v)) for k, v in raw.items()) if v is not None}

        async def run() -> IncidentSearchResult:
            rows = await api.request("GET", "/api/incidents", params=filters)
            shown = [_incident(row) for row in rows[:limit]]
            return IncidentSearchResult(total=len(rows), returned=len(shown), incidents=shown)

        return await _invoke("incidents_search", ctx, {**filters, "limit": limit}, run)

    @mcp.tool(
        name="incidents_create",
        title="Crear una incidencia",
        description=(
            "Abre una incidencia nueva en el Incidents Manager (POST /api/incidents). Siempre nace en estado "
            "`open`; para avanzarla usa incidents_update_status. NO incluyas datos de pacientes (nombres, "
            "historias clínicas, identificadores) en `title` ni en `description`. Devuelve la incidencia creada "
            "(sin su texto libre). Datos no válidos dan `validation_error` con el detalle por campo."
            + _scopes_note("incidents_create")
        ),
        annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=False),
    )
    async def incidents_create(
        title: Annotated[str, Field(min_length=1, max_length=120, description="Resumen corto, sin datos de pacientes")],
        description: Annotated[str, Field(min_length=1, description="Detalle de la incidencia, sin datos de pacientes")],
        category: Annotated[str, Field(description="Categoría", json_schema_extra=_enum(INCIDENT_CATEGORIES))],
        origin: Annotated[str, Field(description="Quién la reporta", json_schema_extra=_enum(INCIDENT_ORIGINS))],
        branch: Annotated[str, Field(description="Sede afectada", json_schema_extra=_enum(INCIDENT_BRANCHES))],
        ctx: Context,
    ) -> IncidentRecord:
        payload = {"title": title, "description": description, "category": category, "status": "open", "origin": origin, "branch": branch}

        async def run() -> IncidentRecord:
            return _incident(await api.request("POST", "/api/incidents", json=payload))

        # Al log solo van los campos de catálogo, nunca el texto libre.
        return await _invoke("incidents_create", ctx, {"category": category, "origin": origin, "branch": branch}, run)

    @mcp.tool(
        name="incidents_update_status",
        title="Cambiar el estado de una incidencia",
        description=(
            "Mueve una incidencia por su ciclo de vida usando el endpoint del Incidents Manager "
            "PATCH /api/incidents/{id}/status. Transiciones válidas: open → in_progress | discarded; "
            "in_progress → resolved | discarded; resolved y discarded son finales. Una transición no permitida "
            "da `validation_error`; una incidencia inexistente, `not_found`." + _scopes_note("incidents_update_status")
        ),
        annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=False),
    )
    async def incidents_update_status(
        incident_id: Annotated[int, Field(ge=1, description="Número de la incidencia (ticket)")],
        status: Annotated[str, Field(description="Estado nuevo", json_schema_extra=_enum(INCIDENT_STATUSES))],
        ctx: Context,
    ) -> IncidentRecord:
        async def run() -> IncidentRecord:
            return _incident(await api.request("PATCH", f"/api/incidents/{incident_id}/status", json={"status": status}))

        return await _invoke("incidents_update_status", ctx, {"incident_id": incident_id, "status": status}, run)

    @mcp.tool(
        name="inventory_query",
        title="Consultar el inventario (solo lectura)",
        description=(
            "Consulta de SOLO LECTURA del inventario de insumos médicos, con el stock actual calculado. "
            "Acciones: `list_supplies` (lista, filtrable con `search` por nombre o SKU y con `country` US/UK) y "
            "`get_supply` (un insumo por `supply_id`). Este servidor NUNCA modifica el inventario: cualquier acción "
            "de escritura (create_supply, register_inbound, register_outbound, adjust_stock, ...) se rechaza con el "
            "error `read_only_resource`; una acción desconocida da `validation_error`." + _scopes_note("inventory_query")
        ),
        annotations=ToolAnnotations(readOnlyHint=True, idempotentHint=True, openWorldHint=False),
    )
    async def inventory_query(
        ctx: Context,
        action: Annotated[str, Field(description="list_supplies | get_supply (solo lectura)", examples=list(READ_ACTIONS))] = "list_supplies",
        supply_id: Annotated[Optional[int], Field(ge=1, description="Obligatorio con get_supply")] = None,
        search: Annotated[Optional[str], Field(max_length=100, description="Texto a buscar en nombre o SKU (list_supplies)")] = None,
        country: Annotated[Optional[str], Field(description="País del insumo", json_schema_extra=_enum(("US", "UK")))] = None,
    ) -> InventoryQueryResult:
        search_text, country_code = _blank_to_none(search), _blank_to_none(country)

        async def run() -> InventoryQueryResult:
            kind = classify_inventory_action(action)
            if kind == "write":
                raise McpToolError(
                    READ_ONLY_RESOURCE,
                    "El inventario es de solo lectura en este servidor MCP: las escrituras se hacen en el backoffice.",
                    {"action": action, "allowed_actions": list(READ_ACTIONS)},
                )
            if kind == "unknown":
                raise McpToolError(VALIDATION_ERROR, "Acción de inventario desconocida.", {"action": action, "allowed_actions": list(READ_ACTIONS)})
            if action == "get_supply":
                if supply_id is None:
                    raise McpToolError(VALIDATION_ERROR, "get_supply necesita `supply_id`.", {"fields": [{"field": "supply_id", "message": "required"}]})
                supply = _supply(await inventory.get(f"/inventory/products/{supply_id}"))
                return InventoryQueryResult(action=action, total=1, supplies=[supply])
            rows = [_supply(row) for row in await inventory.get("/inventory/products")]
            if search_text:
                needle = search_text.lower()
                rows = [row for row in rows if needle in row.name.lower() or needle in row.sku.lower()]
            if country_code:
                rows = [row for row in rows if row.country == country_code]
            return InventoryQueryResult(action=action, total=len(rows), supplies=rows[:MAX_SUPPLIES])

        log_args = {"action": action, "supply_id": supply_id, "country": country_code, "search": bool(search_text)}
        return await _invoke("inventory_query", ctx, log_args, run)
