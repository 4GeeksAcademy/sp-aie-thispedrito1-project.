"""Observabilidad mínima del harness (Ticket #SEC-114).

Cada vez que un guardarraíl bloquea, redirige o sanea algo:

1. Se escribe UNA línea JSON en el logger `healthcore.agent.guardrails` con la
   guardia, la acción, el tipo de fallo (estructural / contenido / seguridad)
   y el motivo (el nombre de la regla, nunca el texto que coincidió).
2. Se suma a un contador en memoria del proceso. `summary()` lo devuelve y
   `GET /agent/guardrails/summary` lo expone: "cuántas veces se activó cada
   guardarraíl durante una sesión de pruebas" = desde que arrancó la API
   (o desde el último `reset()`).

Nunca se registra el mensaje del usuario ni la respuesta: podrían traer PHI
(CONTEXT §1, restricción no negociable). El usuario va como seudónimo
(SHA-256 truncado de su id interno), suficiente para agrupar intentos
repetidos sin exponer quién es.
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Optional

logger = logging.getLogger("healthcore.agent.guardrails")

# Tipos de fallo del README de la clase.
STRUCTURAL = "structural"
CONTENT = "content"
SECURITY = "security"
FAILURE_TYPES = (STRUCTURAL, CONTENT, SECURITY)

# Acciones.
BLOCK = "block"  # no se cumple la petición; respuesta fija
REDIRECT = "redirect"  # se responde brevemente y se reconduce al dominio
SANITIZE = "sanitize"  # se neutraliza un fragmento (contenido externo, salida)


@dataclass(frozen=True)
class GuardEvent:
    guard: str
    action: str
    failure_type: str
    reason: str


def pseudonym(user_id: Optional[str]) -> Optional[str]:
    if not user_id:
        return None
    return hashlib.sha256(user_id.encode("utf-8")).hexdigest()[:12]


class GuardrailMonitor:
    """Contadores thread-safe: los handlers de FastAPI son `def` síncronos y
    corren en el threadpool, igual que el TTLCache de `cache.py`."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._counts: Counter = Counter()
        self._started_at = datetime.now(timezone.utc)

    def record(
        self,
        event: GuardEvent,
        *,
        user_id: Optional[str] = None,
        trace_id: Optional[str] = None,
    ) -> None:
        if event.failure_type not in FAILURE_TYPES:
            raise ValueError(f"failure_type desconocido: {event.failure_type}")
        with self._lock:
            self._counts[(event.guard, event.action, event.failure_type, event.reason)] += 1
        logger.warning(
            json.dumps(
                {
                    "event": "guardrail_triggered",
                    "guard": event.guard,
                    "action": event.action,
                    "failure_type": event.failure_type,
                    "reason": event.reason,
                    "user": pseudonym(user_id),
                    "trace_id": trace_id,
                },
                ensure_ascii=False,
            )
        )

    def reset(self) -> None:
        with self._lock:
            self._counts.clear()
            self._started_at = datetime.now(timezone.utc)

    def summary(self) -> Dict[str, Any]:
        with self._lock:
            counts = dict(self._counts)
            started_at = self._started_at
        by_guard: Counter = Counter()
        by_failure_type: Counter = Counter({failure_type: 0 for failure_type in FAILURE_TYPES})
        by_action: Counter = Counter()
        rows = []
        for (guard, action, failure_type, reason), count in sorted(counts.items()):
            by_guard[guard] += count
            by_failure_type[failure_type] += count
            by_action[action] += count
            rows.append(
                {"guard": guard, "action": action, "failure_type": failure_type, "reason": reason, "count": count}
            )
        return {
            "since": started_at.isoformat(),
            "total": sum(counts.values()),
            "by_guard": dict(by_guard),
            "by_failure_type": dict(by_failure_type),
            "by_action": dict(by_action),
            "detail": rows,
        }


# Singleton del proceso, como la caché de `cache.py`.
MONITOR = GuardrailMonitor()
