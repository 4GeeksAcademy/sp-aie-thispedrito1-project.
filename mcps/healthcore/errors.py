"""Errores de las tools, con código estable y mensaje legible.

Dos niveles, documentados en docs/mcp/mcp-server.md:

- HTTP (antes de MCP), los pone MCP Auth: 401 sin token / token inválido /
  emisor o audiencia incorrectos, siempre con cabecera `WWW-Authenticate`
  que apunta a la Protected Resource Metadata.
- Tool (dentro de MCP): `CallToolResult` con `isError=true` y como texto
  `Error executing tool <tool>: {"error": {"code", "message", "details"}}`.
  El prefijo lo añade SIEMPRE el FastMCP del SDK (mcp 1.30, tools/base.py),
  no se puede quitar; el JSON va a partir de la primera llave. El código es lo que un
  agente debe mirar; el mensaje es para personas.

Los mensajes nunca repiten datos de entrada de texto libre (título o
descripción de una incidencia podrían traer datos de pacientes).
"""

from __future__ import annotations

import json
from typing import Any, Dict, Optional

from mcp.server.fastmcp.exceptions import ToolError

INSUFFICIENT_SCOPE = "insufficient_scope"  # autorización: el token no trae el scope de la tool
READ_ONLY_RESOURCE = "read_only_resource"  # intento de escritura sobre el inventario
VALIDATION_ERROR = "validation_error"  # argumentos o transición de estado no válidos
NOT_FOUND = "not_found"  # el recurso pedido no existe
UPSTREAM_UNAVAILABLE = "upstream_unavailable"  # la API de HealthCore no respondió bien

ERROR_CODES = (INSUFFICIENT_SCOPE, READ_ONLY_RESOURCE, VALIDATION_ERROR, NOT_FOUND, UPSTREAM_UNAVAILABLE)


class McpToolError(ToolError):
    """Error de una tool con código. FastMCP lo convierte en `isError=true`
    y usa `str(error)` como texto del resultado: por eso `__str__` es JSON."""

    def __init__(self, code: str, message: str, details: Optional[Dict[str, Any]] = None) -> None:
        if code not in ERROR_CODES:
            raise ValueError(f"código de error desconocido: {code}")
        self.code = code
        self.message = message
        self.details = details or {}
        super().__init__(self.to_json())

    def to_json(self) -> str:
        return json.dumps({"error": {"code": self.code, "message": self.message, "details": self.details}}, ensure_ascii=False)

    def __str__(self) -> str:
        return self.to_json()


def parse_tool_error(text: str) -> Dict[str, Any]:
    """Inverso de `McpToolError.to_json` para clientes (el agente). Un texto
    que no es nuestro JSON (p. ej. un error del propio SDK al validar tipos)
    se devuelve como `validation_error` si lo parece, o sin código."""
    try:
        payload = json.loads(text[text.index("{"):])
        error = payload["error"]
        return {"code": error["code"], "message": error.get("message", ""), "details": error.get("details") or {}}
    except (ValueError, KeyError, TypeError):
        code = VALIDATION_ERROR if "validation error" in text.lower() else None
        return {"code": code, "message": text, "details": {}}
