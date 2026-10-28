"""Pub/sub en memoria del chat: un canal por sesión (`chat.<session_id>`, BRIEFING §5).

Productor y consumidores no se conocen:

- El productor (el turno del agente, en un hilo aparte: el grafo es
  síncrono) llama a `publish(session_id, evento)` sin saber cuántos sockets
  hay. Puede no haber ninguno (el usuario perdió la conexión) y la
  generación sigue: al reconectar, el snapshot recoge lo que se generó.
- Cada consumidor (un WebSocket) tiene su `Subscription` con su propia cola.
  Un supervisor observando la misma sesión es otra suscripción al mismo
  canal: recibe los mismos eventos sin una segunda llamada al agente.

`publish` es seguro desde cualquier hilo: entrega con `call_soon_threadsafe`
al bucle de asyncio del consumidor, SIEMPRE (también desde el propio bucle),
para que el orden de entrega sea el orden de publicación. Eso es lo que
garantiza que ningún `token_chunk` llegue detrás de su `generation_interrupted`.

En memoria y en un solo proceso (el BRIEFING lo permite). Con varios workers
de uvicorn haría falta un backplane (Redis, como el SSE de la Parte 1).
"""

from __future__ import annotations

import asyncio
import logging
import threading
from typing import Any, Dict, Optional, Set

logger = logging.getLogger("healthcore.agent.chat")

QUEUE_MAXSIZE = 2000  # una respuesta larga son unos cientos de token_chunk


def channel_name(session_id: str) -> str:
    return f"chat.{session_id}"


class SlowConsumer(Exception):
    """La cola del consumidor se llenó: se cierra su socket y reconecta."""


class Subscription:
    def __init__(self, hub: "ChatHub", channel: str, loop: asyncio.AbstractEventLoop) -> None:
        self.hub = hub
        self.channel = channel
        self.loop = loop
        self.queue: "asyncio.Queue[Dict[str, Any]]" = asyncio.Queue(maxsize=QUEUE_MAXSIZE)
        self.overflowed = False

    def deliver(self, event: Dict[str, Any]) -> None:
        """Desde cualquier hilo."""
        try:
            self.loop.call_soon_threadsafe(self._put, event)
        except RuntimeError:  # bucle ya cerrado (apagado de la API)
            pass

    def _put(self, event: Dict[str, Any]) -> None:
        if self.overflowed:
            return
        try:
            self.queue.put_nowait(event)
        except asyncio.QueueFull:
            # Nunca se descartan eventos en silencio: se corta este consumidor.
            self.overflowed = True
            logger.warning("Chat subscriber on %s overflowed; closing it", self.channel)

    async def get(self, timeout: Optional[float] = None) -> Dict[str, Any]:
        if self.overflowed and self.queue.empty():
            raise SlowConsumer()
        event = await asyncio.wait_for(self.queue.get(), timeout)
        return event

    def close(self) -> None:
        self.hub.unsubscribe(self)


class ChatHub:
    def __init__(self) -> None:
        self._channels: Dict[str, Set[Subscription]] = {}
        self._lock = threading.Lock()

    def subscribe(self, session_id: str) -> Subscription:
        """Desde el bucle de asyncio (la conexión WebSocket)."""
        channel = channel_name(session_id)
        subscription = Subscription(self, channel, asyncio.get_running_loop())
        with self._lock:
            self._channels.setdefault(channel, set()).add(subscription)
        return subscription

    def unsubscribe(self, subscription: Subscription) -> None:
        with self._lock:
            subscribers = self._channels.get(subscription.channel)
            if subscribers is None:
                return
            subscribers.discard(subscription)
            if not subscribers:
                del self._channels[subscription.channel]

    def publish(self, session_id: str, event: Dict[str, Any]) -> int:
        """Entrega el evento a cada suscriptor del canal. Devuelve cuántos había."""
        with self._lock:
            subscribers = list(self._channels.get(channel_name(session_id), ()))
        for subscription in subscribers:
            subscription.deliver(event)
        return len(subscribers)

    def subscriber_count(self, session_id: str) -> int:
        with self._lock:
            return len(self._channels.get(channel_name(session_id), ()))


HUB = ChatHub()
