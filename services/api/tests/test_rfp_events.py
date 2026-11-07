"""Stream SSE `GET /rfp-tickets/events` (Sistemas en Tiempo Real, Parte 1).

Sin Redis: el hub se sustituye por uno falso que entrega unos mensajes y
cierra la suscripción, así el stream termina y el test lee la respuesta
HTTP real (cabeceras + framing `event:`/`data:`). El hub de verdad se prueba
aparte con asyncio, sin pasar por HTTP.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, Dict, List

import pytest

from main import app
from services.rfp_intake.events import (
    RfpEventHub,
    Subscription,
    event_stream,
    format_sse,
    get_rfp_event_hub,
    parse_message,
)

TICKET = {
    "ticket_id": "6f1c1e5e-2d0b-4d7e-9a43-0d6c2a7c1b11",
    "rfp_id": "RFP-20261027-0001",
    "client_name": "Westbrook Manufacturing",
    "client_country": "US",
    "program_type": ["occupational_health"],
    "status": "analizando",
    "created_at": "2026-10-27T14:32:00Z",
}
BRIEFING_FIELDS = ["ticket_id", "rfp_id", "client_name", "client_country", "program_type", "status", "created_at"]


class FakeHub:
    def __init__(self, messages: List[Dict[str, Any]]) -> None:
        self.messages = messages
        self.subscribed = 0
        self.unsubscribed = 0

    async def subscribe(self) -> Subscription:
        self.subscribed += 1
        subscription = Subscription()
        for message in self.messages:
            subscription.deliver(message)
        subscription.close()
        return subscription

    def unsubscribe(self, subscription: Subscription) -> None:
        self.unsubscribed += 1


@pytest.fixture()
def hub(client):
    fake = FakeHub([{"event": "rfp_ticket_created", "data": dict(TICKET)}])
    app.dependency_overrides[get_rfp_event_hub] = lambda: fake
    yield fake
    app.dependency_overrides.pop(get_rfp_event_hub, None)


def sse_frames(body: str) -> List[Dict[str, Any]]:
    """Trocea el cuerpo como lo haría un navegador: un frame por línea en
    blanco; los comentarios (`: ...`) se guardan aparte."""
    frames = []
    for block in body.split("\n\n"):
        if not block.strip():
            continue
        frame: Dict[str, Any] = {"comments": []}
        for line in block.split("\n"):
            if line.startswith(":"):
                frame["comments"].append(line[1:].strip())
            else:
                field, _, value = line.partition(": ")
                frame[field] = value
        frames.append(frame)
    return frames


def test_stream_is_text_event_stream_with_named_event_and_briefing_payload(client, auth_headers, hub):
    response = client.get("/rfp-tickets/events", headers=auth_headers)

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert "no-cache" in response.headers["cache-control"]
    assert response.headers["x-accel-buffering"] == "no"

    frames = sse_frames(response.text)
    assert frames[0]["comments"] == ["connected"]
    events = [f for f in frames if "event" in f]
    assert len(events) == 1
    event = events[0]
    # Evento con nombre propio, no el genérico "message".
    assert event["event"] == "rfp_ticket_created"
    assert event["id"] == TICKET["ticket_id"]
    data = json.loads(event["data"])
    # JSON plano del ticket (sin sobre {"event","data"}) con los campos del BRIEFING.
    assert list(data) == BRIEFING_FIELDS
    assert data == TICKET


def test_stream_requires_the_backoffice_jwt(client, hub):
    assert client.get("/rfp-tickets/events").status_code == 401
    bad = client.get("/rfp-tickets/events", headers={"Authorization": "Bearer no-es-un-jwt"})
    assert bad.status_code == 401
    # Sin autenticar no llega a suscribirse: ni un evento.
    assert hub.subscribed == 0


def test_stream_releases_the_subscription_when_it_ends(client, auth_headers, hub):
    client.get("/rfp-tickets/events", headers=auth_headers)
    assert (hub.subscribed, hub.unsubscribed) == (1, 1)


def test_events_route_is_not_shadowed_by_ticket_detail(client, auth_headers, hub):
    # Si `/{ticket_id}` fuera antes, "events" daría 422 (no es un UUID).
    response = client.get("/rfp-tickets/events", headers=auth_headers)
    assert response.status_code == 200


# --------------------------------------------------------------------------
# Framing, filtro de mensajes y hub (sin HTTP)
# --------------------------------------------------------------------------


def test_format_sse_has_id_event_data_and_blank_line():
    frame = format_sse("rfp_ticket_created", {"ticket_id": "t1", "client_name": "Ñandú S.A."}, event_id="t1")
    assert frame == 'id: t1\nevent: rfp_ticket_created\ndata: {"ticket_id": "t1", "client_name": "Ñandú S.A."}\n\n'


def test_parse_message_drops_fields_outside_the_allowlist_and_unknown_events():
    raw = json.dumps({"event": "rfp_ticket_created", "data": {**TICKET, "patient_name": "Jane Doe", "summary": "..."}})
    parsed = parse_message(raw)
    assert list(parsed["data"]) == BRIEFING_FIELDS
    assert "Jane Doe" not in json.dumps(parsed)

    assert parse_message(json.dumps({"event": "message", "data": TICKET})) is None
    assert parse_message(json.dumps({"event": "rfp_ticket_created", "data": {"rfp_id": "x"}})) is None  # sin ticket_id
    assert parse_message(b"no es json") is None


async def _collect(stream, limit: int) -> List[str]:
    chunks = []
    async for chunk in stream:
        chunks.append(chunk)
        if len(chunks) == limit:
            break
    await stream.aclose()
    return chunks


def test_stream_sends_keepalive_comments_when_idle():
    async def scenario():
        hub = RfpEventHub()
        subscription = Subscription()
        hub.subscriptions.add(subscription)
        chunks = await _collect(event_stream(subscription, hub, keepalive_s=0.01), limit=3)
        return chunks, hub

    chunks, hub = asyncio.run(scenario())
    assert chunks == [": connected\n\n", ": keep-alive\n\n", ": keep-alive\n\n"]
    assert hub.subscriptions == set()  # el `finally` libera la cola al cortar


def test_hub_broadcasts_to_every_connection():
    async def scenario():
        hub = RfpEventHub()
        a, b = Subscription(), Subscription()
        hub.subscriptions.update({a, b})
        delivered = hub.broadcast({"event": "rfp_ticket_created", "data": TICKET})
        return delivered, await a.next(0.1), await b.next(0.1)

    delivered, got_a, got_b = asyncio.run(scenario())
    assert delivered == 2 and got_a == got_b


def test_slow_client_is_disconnected_instead_of_silently_losing_events():
    async def scenario():
        hub = RfpEventHub()
        slow = Subscription(maxsize=2)
        hub.subscriptions.add(slow)
        for n in range(3):
            hub.broadcast({"event": "rfp_ticket_created", "data": {**TICKET, "ticket_id": f"t{n}"}})
        # El stream termina por sí solo: el cliente reconectará y recuperará.
        chunks = [chunk async for chunk in event_stream(slow, hub, keepalive_s=5)]
        return chunks, hub, slow

    chunks, hub, slow = asyncio.run(scenario())
    assert chunks[0] == ": connected\n\n" and len(chunks) == 2
    assert slow not in hub.subscriptions


def test_subscribe_does_not_hang_when_redis_is_down():
    async def scenario():
        hub = RfpEventHub(redis_url="redis://127.0.0.1:1/0")  # puerto sin servidor
        subscription = await hub.subscribe(ready_timeout=0.2)
        await hub.close()
        return subscription

    subscription = asyncio.run(asyncio.wait_for(scenario(), timeout=5))
    assert subscription.closed  # close() cierra los streams abiertos


def test_shutdown_signal_ends_open_streams_before_uvicorn_handles_it():
    """Uvicorn espera a que se cierren las conexiones antes del shutdown: si
    la señal no terminara los streams, la API no se apagaría nunca. Mismo
    orden que en la vida real: uvicorn ya tiene su manejador instalado
    cuando arranca la app y se instala el gancho."""
    import signal

    from services.rfp_intake import events

    uvicorn_calls = []
    original = signal.getsignal(signal.SIGTERM)
    signal.signal(signal.SIGTERM, lambda signum, frame: uvicorn_calls.append(signum))
    try:
        events.install_shutdown_signal_hook()

        async def scenario():
            hub = events.get_rfp_event_hub()
            hub._ready.set()  # sin Redis: el test no necesita la escucha
            subscription = await hub.subscribe(ready_timeout=0)
            stream = event_stream(subscription, hub, keepalive_s=5)
            first = await stream.__anext__()
            signal.getsignal(signal.SIGTERM)(signal.SIGTERM, None)  # llega la señal
            rest = [chunk async for chunk in stream]
            await events.shutdown_hub()
            return first, rest

        first, rest = asyncio.run(asyncio.wait_for(scenario(), timeout=5))
    finally:
        signal.signal(signal.SIGTERM, original)

    assert first == ": connected\n\n" and rest == []
    assert uvicorn_calls == [signal.SIGTERM]  # el manejador de uvicorn sigue actuando
