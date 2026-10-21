"""Trazabilidad del flujo de RFPs: qué agente hizo qué, en qué orden.

Cada ejecución de nodo produce un `TraceEntry` con agente, entrada, salida y
timestamp (README de la Parte 3). Va a dos sitios:
- al estado del grafo (clave `trace`, con reducer), que es lo que pide el
  README y queda en el checkpoint;
- a `rfp_trace_events` vía `on_trace`, para que la UI y una investigación en
  producción lo lean sin abrir el checkpoint. Las Partes 1 y 2 escriben ahí
  también sus pasos, así que una consulta cuenta el ticket entero.

Nada de texto libre (borradores, comentarios, RFP): solo resúmenes. Un
borrador se describe por su huella SHA-256 y su número de palabras; un
comentario humano, por si existe y su longitud. Es la misma regla que los
traces del agente (docs/agent/agent-design.md).

`event_id` es determinista: al reanudar, LangGraph vuelve a ejecutar los
nodos pausados, y el mismo paso no debe aparecer dos veces.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Optional

TRACE_NAMESPACE = uuid.UUID("6f1d0c3e-5d1b-4b5e-9a57-0c9b2b9d7a31")

TraceEntry = Dict[str, Any]
TraceSink = Callable[[TraceEntry], None]


def no_trace(_entry: TraceEntry) -> None:
    return None


def text_digest(text: Optional[str]) -> Dict[str, Any]:
    """Huella de un texto para el trace: identifica la versión sin guardarla."""
    if not text:
        return {"sha256": None, "words": 0}
    return {"sha256": hashlib.sha256(text.encode("utf-8")).hexdigest()[:16], "words": len(text.split())}


def event_id(*parts: Any) -> str:
    return str(uuid.uuid5(TRACE_NAMESPACE, "|".join(str(part) for part in parts)))


def entry(
    *,
    key: tuple,
    part: int,
    node: str,
    agent: str,
    department_id: Optional[str] = None,
    input: Optional[Dict[str, Any]] = None,
    output: Optional[Dict[str, Any]] = None,
    timestamp: Optional[datetime] = None,
) -> TraceEntry:
    """`key` identifica el paso de forma única dentro del ticket (hilo, nodo,
    departamento, ronda...); de ahí sale el `event_id`."""
    return {
        "event_id": event_id(*key),
        "part": part,
        "node": node,
        "agent": agent,
        "department_id": department_id,
        "input": input or {},
        "output": output or {},
        "timestamp": (timestamp or datetime.now(timezone.utc)).isoformat(),
    }
