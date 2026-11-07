"""Aviso en tiempo real de una RFP nueva (Sistemas en Tiempo Real, Parte 1).

El worker de la Parte 1 publica `rfp_ticket_created` en un canal de Redis en
cuanto el clasificador acepta el documento y el orquestador extrae sus
metadatos (BRIEFING §2: "clasificado como una RFP válida y el flujo empieza a
procesarlo"). El ticket sigue `analizando` en ese momento. La API reenvía el
canal a cada navegador por SSE (services/rfp_intake/events.py).

Redis y no la base de datos porque el worker y la API son procesos distintos
y los metadatos aún no están guardados: la Parte 1 los escribe todos juntos
al final, en una sola transacción.

Entrega "como mucho una vez" por conexión y "al menos una vez" en total: un
reintento de Celery o `scripts/reprocess_rfp.py` vuelven a publicar el mismo
`ticket_id` (el cliente deduplica), y un navegador desconectado no recibe nada
(lo recupera al reconectar pidiendo la lista; ver docs/sse-notifications.md).

Sin PHI: el payload se construye con una lista cerrada de campos del ticket y
de los metadatos de la RFP (contratos institucionales, BRIEFING §2), nunca
con el texto del documento.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Optional

logger = logging.getLogger("healthcore.rfp_intake")

EVENT_RFP_TICKET_CREATED = "rfp_ticket_created"
# Un solo canal para todos los eventos de RFP: el mensaje lleva su nombre de
# evento, así un tipo nuevo no necesita otra suscripción en la API.
RFP_EVENTS_CHANNEL = "healthcore:rfp-events"
REDIS_TIMEOUT_S = 2.0

# Campos del BRIEFING §3, en su orden. Nada fuera de esta lista se emite.
TICKET_CREATED_FIELDS = (
    "ticket_id", "rfp_id", "client_name", "client_country", "program_type", "status", "created_at",
)

Publisher = Callable[[str, str], Any]


def _iso_utc(value: datetime) -> str:
    # SQLite (tests) devuelve fechas sin zona: se asumen UTC, como al guardarlas.
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def ticket_created_payload(ticket: Any, metadata: Dict[str, Any]) -> Dict[str, Any]:
    """`ticket` es la fila `rfp_tickets`; `metadata`, la salida del orquestador."""
    payload = {
        "ticket_id": str(ticket.ticket_id),
        "rfp_id": ticket.rfp_id,
        "client_name": metadata.get("client_name"),
        "client_country": metadata.get("client_country"),
        "program_type": list(metadata.get("program_type") or []),
        "status": ticket.status,
        "created_at": _iso_utc(ticket.created_at),
    }
    return {field: payload[field] for field in TICKET_CREATED_FIELDS}


def _redis_publisher() -> Publisher:
    import redis  # perezoso: los tests del grafo no necesitan Redis

    client = redis.Redis.from_url(
        os.getenv("REDIS_URL", "redis://localhost:6379/0"),
        socket_connect_timeout=REDIS_TIMEOUT_S,
        socket_timeout=REDIS_TIMEOUT_S,
    )
    return client.publish


def publish_event(event: str, data: Dict[str, Any], *, publisher: Optional[Publisher] = None) -> bool:
    """Nunca lanza: un aviso perdido no puede tumbar el análisis de la RFP."""
    message = json.dumps({"event": event, "data": data}, ensure_ascii=False)
    try:
        (publisher or _redis_publisher())(RFP_EVENTS_CHANNEL, message)
    except Exception as exc:  # noqa: BLE001 — Redis caído, timeout, URL mal escrita...
        logger.warning("rfp_event_publish_failed event=%s error_type=%s", event, type(exc).__name__)
        return False
    logger.info("rfp_event_published event=%s ticket=%s", event, data.get("ticket_id"))
    return True


def notify_ticket_created(ticket: Any, metadata: Dict[str, Any], *, publisher: Optional[Publisher] = None) -> bool:
    return publish_event(EVENT_RFP_TICKET_CREATED, ticket_created_payload(ticket, metadata), publisher=publisher)
