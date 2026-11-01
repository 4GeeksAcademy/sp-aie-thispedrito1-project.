"""Rate limiting de las llamadas al modelo (clase NIST, función Protect).

Problema que resuelve: cada pregunta a `/agent/query`, `/knowledge/query` o al
chat WebSocket acaba en una o varias llamadas al proveedor de modelos (que se
paga por uso). Un bucle de un cliente roto, o alguien con una sesión robada,
podría disparar miles en minutos. Este límite lo corta antes de que llegue al
modelo: N peticiones por usuario en una ventana deslizante de W segundos.

Decisiones:

- **Un solo cupo por usuario para los tres canales** (`LLM_LIMITER`). Lo que se
  protege es el coste, y es el mismo entre por donde entre la pregunta.
- **Clave = id interno del usuario**: los tres canales exigen sesión. Nunca el
  email (no debe aparecer en logs ni en memoria de proceso sin necesidad).
- **En memoria del proceso** (decisión del usuario), como `cache.py` y el
  `MONITOR` de los guardarraíles: sin Redis ni dependencias nuevas. Con varios
  workers de uvicorn cada uno contaría por su lado (pendiente documentado en
  docs/security/nist-ai-security-report.md).
- **Cuenta toda petición que llega, la bloquee después un guardarraíl o no**:
  quien manda 100 jailbreaks por minuto también es un abuso, aunque ninguno
  llegue al modelo.
- **Cada rechazo pasa por el `MONITOR`**: una línea JSON con el seudónimo del
  usuario (nunca su id ni la pregunta) y un contador en
  `GET /agent/guardrails/summary`.

Configuración (opcional, `services/api/.env`): `LLM_RATE_LIMIT_MAX_REQUESTS`
(por defecto 10) y `LLM_RATE_LIMIT_WINDOW_S` (por defecto 60).
"""

from __future__ import annotations

import math
import os
import sys
import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Any, Callable, Deque, Dict, Optional

from fastapi import Depends, HTTPException, status

from security import get_current_user
from services.agent.guardrails.monitor import BLOCK, MONITOR, SECURITY, GuardEvent

DEFAULT_MAX_REQUESTS = 10
DEFAULT_WINDOW_S = 60.0

# Lo que el monitor registra en cada rechazo (nunca el texto de la petición).
RATE_LIMIT_EVENT = GuardEvent(guard="rate_limit", action=BLOCK, failure_type=SECURITY, reason="llm_requests_per_window")


@dataclass(frozen=True)
class RateDecision:
    allowed: bool
    # Segundos hasta que vuelva a haber hueco (0 si se permite). Va en la
    # cabecera `Retry-After` y en el mensaje al usuario.
    retry_after_s: int = 0


class SlidingWindowLimiter:
    """Como mucho `max_requests` por clave en cualquier tramo de `window_s` segundos.

    Guarda, por clave, la hora (reloj monotónico) de cada petición aceptada
    dentro de la ventana. `clock` es inyectable para que los tests avancen el
    tiempo sin dormir.
    """

    def __init__(
        self,
        max_requests: int,
        window_s: float,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if max_requests < 1:
            raise ValueError("max_requests debe ser al menos 1")
        if window_s <= 0:
            raise ValueError("window_s debe ser positivo")
        self.max_requests = max_requests
        self.window_s = window_s
        self._clock = clock
        self._hits: Dict[str, Deque[float]] = {}
        self._lock = threading.Lock()

    def check(self, key: str) -> RateDecision:
        """Decide si la petición de `key` entra y, si entra, la apunta."""
        now = self._clock()
        with self._lock:
            hits = self._hits.setdefault(key, deque())
            # Ventana deslizante (Learn by Doing delegado al asistente).
            # 1. Fuera las que ya cumplieron la ventana. `<=`: a los W segundos
            #    exactos de una petición, su hueco ya está libre.
            while hits and hits[0] <= now - self.window_s:
                hits.popleft()
            # 2. Lleno: NO se apunta el rechazo, o un cliente en bucle no
            #    recuperaría nunca el cupo. Se redondea hacia arriba (mínimo 1)
            #    para no decir "espera 0 s" y provocar un reintento inmediato.
            if len(hits) >= self.max_requests:
                wait = hits[0] + self.window_s - now
                return RateDecision(False, retry_after_s=max(1, math.ceil(wait)))
            # 3. Hay hueco: se apunta y entra.
            hits.append(now)
            return RateDecision(True)

    def reset(self) -> None:
        """Vacía todos los contadores (tests y reinicios manuales)."""
        with self._lock:
            self._hits.clear()


def _positive_number(name: str, default: float, cast: Callable[[str], float]) -> float:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = cast(raw)
    except ValueError:
        value = 0
    if value <= 0:
        # Un valor roto no puede desactivar la protección: se avisa y se usa el
        # valor por defecto, igual que el resto de avisos de arranque de main.py.
        print(f"[rate_limit] {name} no es un número positivo; se usa {default}", file=sys.stderr)
        return default
    return value


def build_llm_limiter() -> SlidingWindowLimiter:
    return SlidingWindowLimiter(
        max_requests=int(_positive_number("LLM_RATE_LIMIT_MAX_REQUESTS", DEFAULT_MAX_REQUESTS, int)),
        window_s=_positive_number("LLM_RATE_LIMIT_WINDOW_S", DEFAULT_WINDOW_S, float),
    )


# Singleton del proceso (como `cache` y `MONITOR`). Los tests lo vacían en una
# fixture autouse de conftest.py o lo sustituyen por uno con reloj falso.
LLM_LIMITER = build_llm_limiter()


def rate_limit_message(retry_after_s: int) -> str:
    return (
        "Has hecho demasiadas consultas al asistente en poco tiempo. "
        f"Espera {retry_after_s} s y vuelve a intentarlo."
    )


def check_llm_quota(user_id: str, limiter: Optional[SlidingWindowLimiter] = None) -> RateDecision:
    """Punto único para HTTP y WebSocket: decide y, si rechaza, lo registra."""
    decision = (limiter or LLM_LIMITER).check(str(user_id))
    if not decision.allowed:
        MONITOR.record(RATE_LIMIT_EVENT, user_id=str(user_id))
    return decision


def enforce_llm_rate_limit(user: Dict[str, Any] = Depends(get_current_user)) -> None:
    """Dependencia de FastAPI para los endpoints HTTP que llaman al modelo.

    Se ejecuta antes del handler: un 429 nunca llega a los guardarraíles, al
    RAG ni al proveedor. FastAPI cachea `get_current_user` dentro de la misma
    petición, así que el token no se valida dos veces.
    """
    decision = check_llm_quota(str(user["id"]))
    if not decision.allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=rate_limit_message(decision.retry_after_s),
            headers={"Retry-After": str(decision.retry_after_s)},
        )
