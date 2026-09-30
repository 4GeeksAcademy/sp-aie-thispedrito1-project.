"""Piezas comunes de las tools del agente: resultado tipado y timeout.

Una tool NUNCA lanza una excepción hacia el grafo: todo desenlace (datos,
recurso inexistente, servicio caído o lento) vuelve como un `ToolResult` con
su `status`, y es una arista condicional la que decide si hay respuesta o
fallback. Así un fallo de un servicio externo es una ruta del grafo, no una
corrida rota.

Timeout: la consulta corre en un hilo del pool y `future.result(timeout=...)`
deja de esperarla a los N segundos. Python no puede matar un hilo, así que
una consulta colgada sigue ocupando su hilo hasta que termina; el pool está
acotado (`MAX_TOOL_THREADS`) para que eso no crezca sin límite.
"""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from typing import Any, Callable, Dict, Literal, Optional

from pydantic import BaseModel

MAX_TOOL_THREADS = 4

ToolStatus = Literal["ok", "not_found", "unavailable"]

_EXECUTOR = ThreadPoolExecutor(max_workers=MAX_TOOL_THREADS, thread_name_prefix="agent-tool")


class ToolNotFound(LookupError):
    """El recurso pedido no existe (p. ej. un ticket_id sin incidencia)."""


class ToolResult(BaseModel):
    """Salida común de toda tool, tal como queda en el estado y en el trace.

    `data` es la salida tipada propia de cada tool, ya serializada; solo
    existe con `status="ok"`. `error_type` es el nombre de la excepción,
    nunca su mensaje (podría repetir datos del proveedor)."""

    tool: str
    status: ToolStatus
    args: Dict[str, Any]
    data: Optional[Dict[str, Any]] = None
    error_type: Optional[str] = None
    # Por dónde se obtuvo el dato: "mcp" = a través del servidor MCP de
    # HealthCore; None = en proceso (inventario). Queda en el trace.
    via: Optional[str] = None
    timeout_s: float
    duration_ms: float


def run_tool(
    tool: str,
    args: Dict[str, Any],
    call: Callable[[], BaseModel],
    *,
    timeout_s: float,
    via: Optional[str] = None,
) -> ToolResult:
    """Ejecuta `call` con timeout y convierte cualquier desenlace en ToolResult."""
    started = time.perf_counter()

    def result(status: ToolStatus, data: Optional[BaseModel] = None, error: Optional[BaseException] = None) -> ToolResult:
        return ToolResult(
            tool=tool,
            status=status,
            args=args,
            data=data.model_dump(mode="json") if data is not None else None,
            error_type=type(error).__name__ if error is not None else None,
            via=via,
            timeout_s=timeout_s,
            duration_ms=round((time.perf_counter() - started) * 1000, 2),
        )

    future = _EXECUTOR.submit(call)
    try:
        return result("ok", future.result(timeout=timeout_s))
    except FutureTimeout as exc:
        future.cancel()  # solo surte efecto si aún no había empezado
        return result("unavailable", error=exc)
    except ToolNotFound as exc:
        return result("not_found", error=exc)
    except Exception as exc:  # servicio caído, configuración ausente, bug
        return result("unavailable", error=exc)
