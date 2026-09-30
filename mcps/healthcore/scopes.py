"""Scopes OAuth del servidor MCP y qué exige cada tool (mínimo privilegio).

Los scopes son permisos que Logto mete en el access token (claim `scope`).
MCP Auth ya garantiza, antes de llegar aquí, que el token es auténtico, de
nuestro emisor y para nuestra API (401 si no). Este módulo decide lo
siguiente: si ESE token puede usar ESA tool.

Deliberadamente NO existe un scope de escritura de inventario: aunque un
token trajera algo como `inventory:write`, ninguna tool lo reconoce.
"""

from __future__ import annotations

from typing import Dict, FrozenSet, Iterable, Optional

from mcps.healthcore.errors import INSUFFICIENT_SCOPE, McpToolError

INCIDENTS_READ = "incidents:read"
INCIDENTS_WRITE = "incidents:write"
INVENTORY_READ = "inventory:read"

ALL_SCOPES = (INCIDENTS_READ, INCIDENTS_WRITE, INVENTORY_READ)

# Qué scopes exige cada tool (hay que tenerlos TODOS). Una tool que no esté
# aquí no se puede invocar: `required_scopes` falla cerrado, nunca abierto.
TOOL_SCOPES: Dict[str, FrozenSet[str]] = {
    # Un scope por tool, el de su operación. Escribir NO exige además leer:
    # un integrador que solo abre tickets (p. ej. un formulario de otra área)
    # no necesita poder consultar toda la base de incidencias. La respuesta de
    # una escritura solo devuelve la incidencia que acaba de tocar.
    "incidents_get": frozenset({INCIDENTS_READ}),
    "incidents_search": frozenset({INCIDENTS_READ}),
    "incidents_create": frozenset({INCIDENTS_WRITE}),
    "incidents_update_status": frozenset({INCIDENTS_WRITE}),
    "inventory_query": frozenset({INVENTORY_READ}),
}


def required_scopes(tool: str) -> FrozenSet[str]:
    try:
        return TOOL_SCOPES[tool]
    except KeyError:
        raise McpToolError(INSUFFICIENT_SCOPE, f"La tool '{tool}' no tiene política de permisos: acceso denegado.") from None


def ensure_scopes(tool: str, granted: Optional[Iterable[str]]) -> None:
    """Lanza `insufficient_scope` si al token le falta algún scope de la tool."""
    needed = required_scopes(tool)
    missing = sorted(needed - set(granted or ()))
    if missing:
        raise McpToolError(
            INSUFFICIENT_SCOPE,
            f"El access token no tiene permiso para '{tool}'.",
            {"required_scopes": sorted(needed), "missing_scopes": missing},
        )
