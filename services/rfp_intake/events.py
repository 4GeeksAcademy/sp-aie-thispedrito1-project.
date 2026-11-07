"""Stream SSE de eventos de RFP (`GET /rfp-tickets/events`).

Reparto: el worker publica en Redis (data/pipelines/rfp_intake/notifications.py)
y cada proceso de la API mantiene UNA suscripción (`RfpEventHub`) que reparte
el mensaje a una cola por navegador conectado. 50 pestañas abiertas son 50
colas en memoria y una sola conexión a Redis, no 50.

Una cola llena (cliente que no lee) no bloquea a los demás ni crece sin
límite: se cierra ese stream y el cliente reconecta y recupera pidiendo la
lista (docs/sse-notifications.md). Perder avisos en silencio no es una opción.

Formato del cable (BRIEFING §3): `event:` con nombre y `data:` con el JSON
plano del ticket, sin sobre `{"event","data"}`. Cada 15 s sin eventos sale un
comentario `: keep-alive` para que ningún proxy cierre la conexión por inactiva.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
import threading
from typing import Any, AsyncIterator, Dict, Optional, Set

from data.pipelines.rfp_intake.notifications import (
    EVENT_RFP_TICKET_CREATED,
    RFP_EVENTS_CHANNEL,
    TICKET_CREATED_FIELDS,
)

logger = logging.getLogger("healthcore.rfp_intake")

KEEPALIVE_INTERVAL_S = 15.0
QUEUE_MAXSIZE = 100
READY_TIMEOUT_S = 2.0
MAX_REDIS_BACKOFF_S = 30.0

# Eventos que el stream acepta reenviar y los campos de cada uno. Segunda
# capa de la lista cerrada del emisor: un mensaje con campos de más en Redis
# no llega al navegador con ellos.
EVENT_FIELDS: Dict[str, tuple] = {EVENT_RFP_TICKET_CREATED: TICKET_CREATED_FIELDS}

SSE_HEADERS = {
    "Cache-Control": "no-cache, no-transform",
    "Connection": "keep-alive",
    # nginx y algunos proxies acumulan la respuesta entera si no se les dice.
    "X-Accel-Buffering": "no",
}


def format_sse(event: str, data: Dict[str, Any], *, event_id: Optional[str] = None) -> str:
    lines = []
    if event_id:
        lines.append(f"id: {event_id}")
    lines.append(f"event: {event}")
    # json.dumps sin indent no produce saltos de línea: un solo `data:` basta.
    lines.append(f"data: {json.dumps(data, ensure_ascii=False)}")
    return "\n".join(lines) + "\n\n"


def parse_message(raw: Any) -> Optional[Dict[str, Any]]:
    """Mensaje de Redis → `{event, data}` filtrado, o None si no es válido."""
    try:
        message = json.loads(raw)
    except (TypeError, ValueError):
        return None
    if not isinstance(message, dict):
        return None
    event, data = message.get("event"), message.get("data")
    fields = EVENT_FIELDS.get(event)
    if fields is None or not isinstance(data, dict) or not data.get("ticket_id"):
        return None
    return {"event": event, "data": {field: data.get(field) for field in fields}}


class StreamClosed(Exception):
    """La suscripción se cerró (cola desbordada o apagado de la API)."""


_CLOSED = object()


class Subscription:
    """Cola de un navegador conectado."""

    def __init__(self, maxsize: int = QUEUE_MAXSIZE) -> None:
        self.queue: asyncio.Queue = asyncio.Queue(maxsize=maxsize)
        self.closed = False

    def deliver(self, message: Dict[str, Any]) -> bool:
        if self.closed:
            return False
        try:
            self.queue.put_nowait(message)
        except asyncio.QueueFull:
            self.close()
            return False
        return True

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        # Hueco para la marca de cierre aunque la cola esté llena: el stream
        # termina en vez de seguir sirviendo una secuencia con agujeros.
        if self.queue.full():
            self.queue.get_nowait()
        self.queue.put_nowait(_CLOSED)

    async def next(self, timeout: float) -> Optional[Dict[str, Any]]:
        """Siguiente mensaje, None si vence `timeout` (toca keep-alive)."""
        try:
            item = await asyncio.wait_for(self.queue.get(), timeout)
        except asyncio.TimeoutError:
            return None
        if item is _CLOSED:
            raise StreamClosed()
        return item


class RfpEventHub:
    def __init__(self, redis_url: Optional[str] = None, channel: str = RFP_EVENTS_CHANNEL) -> None:
        self.redis_url = redis_url or os.getenv("REDIS_URL", "redis://localhost:6379/0")
        self.channel = channel
        self.subscriptions: Set[Subscription] = set()
        self._task: Optional[asyncio.Task] = None
        self._ready = asyncio.Event()
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    async def subscribe(self, *, ready_timeout: float = READY_TIMEOUT_S) -> Subscription:
        """Registra la cola y espera a que la escucha de Redis esté activa: si
        el cliente pide la lista justo al conectar, no queda un hueco entre esa
        lectura y el primer evento que recibe. Con Redis caído no se bloquea:
        el stream abre igual (keep-alive) y el hub sigue reintentando."""
        self._loop = asyncio.get_running_loop()
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._listen(), name="rfp-event-hub")
        subscription = Subscription()
        self.subscriptions.add(subscription)
        try:
            await asyncio.wait_for(self._ready.wait(), ready_timeout)
        except asyncio.TimeoutError:
            logger.warning("rfp_event_hub_not_ready subscribers=%d", len(self.subscriptions))
        return subscription

    def unsubscribe(self, subscription: Subscription) -> None:
        self.subscriptions.discard(subscription)

    def broadcast(self, message: Dict[str, Any]) -> int:
        delivered = 0
        for subscription in list(self.subscriptions):
            if subscription.deliver(message):
                delivered += 1
            else:
                self.subscriptions.discard(subscription)
                logger.warning("rfp_event_subscriber_dropped reason=queue_full")
        return delivered

    async def _listen(self) -> None:
        import redis.asyncio as aioredis

        backoff = 1.0
        while True:
            client = aioredis.Redis.from_url(self.redis_url)
            pubsub = client.pubsub()
            try:
                await pubsub.subscribe(self.channel)
                self._ready.set()
                backoff = 1.0
                async for raw in pubsub.listen():
                    if raw.get("type") != "message":
                        continue
                    message = parse_message(raw.get("data"))
                    if message is None:
                        logger.warning("rfp_event_ignored reason=invalid_message")
                        continue
                    self.broadcast(message)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 — Redis caído o reiniciado
                self._ready.clear()
                logger.warning("rfp_event_hub_redis_error error_type=%s retry_in_s=%.0f", type(exc).__name__, backoff)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, MAX_REDIS_BACKOFF_S)
            finally:
                try:
                    await pubsub.aclose()
                    await client.aclose()
                except Exception:  # noqa: BLE001 — la conexión ya estaba rota
                    pass

    def close_streams(self) -> None:
        """Termina todos los streams abiertos (los clientes reconectarán)."""
        for subscription in list(self.subscriptions):
            subscription.close()
        self.subscriptions.clear()

    def close_streams_threadsafe(self) -> None:
        """Desde fuera del bucle de asyncio (un manejador de señal)."""
        if self._loop is not None and not self._loop.is_closed():
            self._loop.call_soon_threadsafe(self.close_streams)

    async def close(self) -> None:
        self.close_streams()
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
            self._task = None
        self._ready.clear()


async def event_stream(
    subscription: Subscription, hub: Any, *, keepalive_s: float = KEEPALIVE_INTERVAL_S
) -> AsyncIterator[str]:
    """Generador del cuerpo SSE. El `finally` libera la cola también cuando
    Starlette lo cancela porque el navegador se fue."""
    try:
        # Primer fragmento inmediato: el navegador recibe las cabeceras ya y
        # sabe que está conectado (y suscrito) antes del primer evento real.
        yield ": connected\n\n"
        while True:
            try:
                message = await subscription.next(keepalive_s)
            except StreamClosed:
                return
            if message is None:
                yield ": keep-alive\n\n"
                continue
            yield format_sse(message["event"], message["data"], event_id=message["data"]["ticket_id"])
    finally:
        hub.unsubscribe(subscription)


_hub: Optional[RfpEventHub] = None


def get_rfp_event_hub() -> RfpEventHub:
    """Dependencia de FastAPI (los tests la sustituyen por un hub falso)."""
    global _hub
    if _hub is None:
        _hub = RfpEventHub()
    return _hub


def install_shutdown_signal_hook() -> None:
    """Gotcha real (encontrado al cortar la API en la verificación manual):
    al apagarse, uvicorn espera a que se cierren TODAS las conexiones antes de
    lanzar los eventos de shutdown. Un stream SSE no se cierra nunca solo, así
    que la API se quedaba colgada en "Waiting for connections to close" (y
    con ella cada recarga de `--reload` o `docker stop`).

    Se envuelve el manejador de SIGINT/SIGTERM que esté instalado: primero se
    cierran los streams (los navegadores reconectan con su backoff) y después
    actúa el de uvicorn. Se llama desde el startup de la app porque uvicorn
    instala sus manejadores ANTES de importar la app: parchear la clase
    `Server` al importar llega tarde (se probó y la API seguía colgada).
    """
    if threading.current_thread() is not threading.main_thread():
        return  # señales solo en el hilo principal (p. ej. TestClient corre en otro)
    for sig in (signal.SIGINT, signal.SIGTERM):
        previous = signal.getsignal(sig)
        if getattr(previous, "_closes_rfp_streams", False) or not callable(previous):
            continue

        def handler(signum, frame, previous=previous):  # type: ignore[no-untyped-def]
            if _hub is not None:
                _hub.close_streams_threadsafe()
            return previous(signum, frame)

        handler._closes_rfp_streams = True  # type: ignore[attr-defined]
        signal.signal(sig, handler)


async def shutdown_hub() -> None:
    global _hub
    if _hub is not None:
        await _hub.close()
        _hub = None
