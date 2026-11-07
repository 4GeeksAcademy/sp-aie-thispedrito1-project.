"""Chat en tiempo real por WebSocket (Sistemas en Tiempo Real, Parte 2).

El grafo real del agente (el que compila el router) se ejecuta de verdad:
solo se sustituyen la recuperación, el planificador y el modelo. El doble
del modelo "escribe" palabra a palabra por el `AnswerStream` del nodo
`generate`, igual que el streaming real, y puede quedarse esperando a mitad
de la respuesta para interrumpirlo en un punto exacto. Si detecta la orden
de parar, lo apunta: así se mide que el abort corta la generación en origen,
no que el cliente ignore lo que siga llegando.

Fijan: autenticación con el mismo JWT (`?token=` y primer frame), socket
ligado a la sesión, contrato de eventos, abort sin un solo token más,
mensaje parcial `interrupted` + turno nuevo, rehidratación al reconectar
(también a mitad de una respuesta), supervisor de solo lectura y
guardarraíl de salida palabra a palabra.
"""

from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, List, Optional

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select
from starlette.websockets import WebSocketDisconnect

from data.pipelines import rag
from main import app
from services.agent import planner
from services.agent.chat import events
from services.agent.chat.models import ChatMessage, ChatSession
from services.agent.chat.router import get_chat_session_factory
from services.agent.chat.service import CHAT
from services.agent.memory import reply
from services.agent.memory.reply import AgentReply
from services.agent.planner import Plan, PlannedCall
from services.agent.streaming import GenerationAborted

CHUNK = {
    "source_document": "appointment-policy",
    "section": "Política de cancelación",
    "chunk_index": 1,
    "text": "Cancelar con más de 24 horas de anticipación: sin cargo.",
    "score": 0.8,
}
FIRST_QUESTION = "¿Qué dice la política sobre cancelar una cita con 30 horas de antelación?"
REDIRECT = "espera, mi pregunta era sobre UK GDPR, no HIPAA"
RECEIVE_TIMEOUT_S = 5.0
# Un modelo real tarda decenas de ms por trozo. Sin pausa, el doble terminaría
# antes de que la orden de parar (que viaja por otro hilo) le llegue.
WORD_DELAY_S = 0.02

_reader = ThreadPoolExecutor(max_workers=4)


class FakeModel:
    """Doble del modelo de generación con streaming controlable."""

    def __init__(self) -> None:
        self.scripts: Dict[str, List[str]] = {}
        self.pause_after: Dict[str, int] = {}  # pregunta → palabras antes de quedarse esperando
        self.release = threading.Event()
        self.aborted: List[str] = []
        self.emitted_after_stop = 0

    def script(self, marker: str, words: List[str], pause_after: Optional[int] = None) -> None:
        self.scripts[marker] = words
        if pause_after is not None:
            self.pause_after[marker] = pause_after

    def __call__(self, question: str, context: Any, *, answer_stream: Any = None) -> AgentReply:
        marker = next(m for m in self.scripts if m in question)
        words = self.scripts[marker]
        pause = self.pause_after.get(marker)
        if answer_stream is None:
            return AgentReply(answer="".join(words))
        try:
            for index, word in enumerate(words):
                if pause is not None and index == pause:
                    deadline = time.monotonic() + RECEIVE_TIMEOUT_S
                    # Espera a que el test lo suelte, comprobando la orden de
                    # parar como hace el streaming real entre trozos del modelo.
                    while not self.release.is_set() and time.monotonic() < deadline:
                        answer_stream.check()
                        time.sleep(0.01)
                answer_stream.check()
                if answer_stream.should_stop():
                    self.emitted_after_stop += 1
                answer_stream.emit(word)
                time.sleep(WORD_DELAY_S)
        except GenerationAborted:
            self.aborted.append(marker)
            raise
        return AgentReply(answer="".join(words))


@pytest.fixture()
def model(monkeypatch, tmp_path, client, inventory_engine):
    monkeypatch.setenv("AGENT_TRACE_DIR", str(tmp_path))
    monkeypatch.setattr(rag, "get_min_score", lambda: 0.38)
    monkeypatch.setattr(rag, "retrieve", lambda q, *, k, min_score: [CHUNK])
    monkeypatch.setattr(planner, "plan_sources", lambda question: Plan(calls=[PlannedCall(source="knowledge_base")], status="model"))
    fake = FakeModel()
    monkeypatch.setattr(reply, "generate_reply", fake)
    app.dependency_overrides[get_chat_session_factory] = lambda: (lambda: Session(inventory_engine))
    yield fake
    fake.release.set()  # ningún hilo de turno queda esperando al acabar el test


def receive(ws: Any) -> Dict[str, Any]:
    return _reader.submit(ws.receive_json).result(timeout=RECEIVE_TIMEOUT_S)


def receive_until(ws: Any, event: str, limit: int = 100) -> List[Dict[str, Any]]:
    """Frames hasta (incluido) el primero de tipo `event`."""
    frames = []
    for _ in range(limit):
        frame = receive(ws)
        frames.append(frame)
        if frame["event"] == event:
            return frames
    raise AssertionError(f"No llegó {event}: {[f['event'] for f in frames]}")


def new_session(client: TestClient, headers: Dict[str, str]) -> str:
    response = client.post("/agent/chat/sessions", headers=headers)
    assert response.status_code == 201
    body = response.json()
    assert set(body) == {"session_id", "agent_id", "user_id", "status", "created_at"}
    assert body["agent_id"] == "compliance_assistant" and body["status"] == "active"
    return body["session_id"]


def token_of(headers: Dict[str, str]) -> str:
    return headers["Authorization"].removeprefix("Bearer ")


def send(ws: Any, event: str, session_id: str, **data: Any) -> None:
    ws.send_json({"event": event, "data": {"session_id": session_id, **data}})


def tokens(frames: List[Dict[str, Any]], message_id: Optional[str] = None) -> List[Dict[str, Any]]:
    return [
        f["data"] for f in frames if f["event"] == events.TOKEN_CHUNK and (message_id is None or f["data"]["message_id"] == message_id)
    ]


def wait_idle(session_id: str) -> None:
    deadline = time.monotonic() + RECEIVE_TIMEOUT_S
    while CHAT.runtime(session_id).tasks and time.monotonic() < deadline:
        time.sleep(0.01)


def stored_messages(engine: Any, session_id: str) -> List[ChatMessage]:
    with Session(engine) as db:
        statement = select(ChatMessage).where(ChatMessage.session_id == session_id).order_by(ChatMessage.created_at)
        return list(db.exec(statement))


# --- Autenticación y enlace a la sesión ------------------------------------------


def test_socket_without_token_is_closed_before_any_chat_event(client, auth_headers, model) -> None:
    session_id = new_session(client, auth_headers)
    with client.websocket_connect(f"/agent/chat/ws/{session_id}") as ws:
        send(ws, events.USER_MESSAGE, session_id, text=FIRST_QUESTION)  # sin auth previa
        with pytest.raises(WebSocketDisconnect) as closed:
            receive(ws)
    assert closed.value.code == events.CLOSE_UNAUTHENTICATED


def test_invalid_query_token_rejects_the_handshake(client, auth_headers, model) -> None:
    session_id = new_session(client, auth_headers)
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect(f"/agent/chat/ws/{session_id}?token=no-es-un-jwt"):
            pass


def test_first_frame_auth_with_the_backoffice_jwt_opens_the_session(client, auth_headers, model) -> None:
    session_id = new_session(client, auth_headers)
    with client.websocket_connect(f"/agent/chat/ws/{session_id}") as ws:
        ws.send_json({"event": "auth", "data": {"token": token_of(auth_headers)}})
        snapshot = receive(ws)
    assert snapshot == {
        "event": "session_snapshot",
        "data": {"session_id": session_id, "status": "active", "messages": [], "in_progress": None},
    }


def test_socket_is_bound_to_an_existing_session_of_its_owner(client, auth_headers, model) -> None:
    token = token_of(auth_headers)
    with client.websocket_connect(f"/agent/chat/ws/chat_no_existe?token={token}") as ws:
        with pytest.raises(WebSocketDisconnect) as closed:
            receive(ws)
    assert closed.value.code == events.CLOSE_SESSION_NOT_FOUND

    from conftest import TEST_PASSWORD

    other = client.post("/users", json={"name": "Otra Persona", "email": "otra@healthcore.com", "password": TEST_PASSWORD})
    assert other.status_code == 201, other.text
    login = client.post("/auth/login", json={"email": "otra@healthcore.com", "password": TEST_PASSWORD})
    session_id = new_session(client, {"Authorization": f"Bearer {login.json()['access_token']}"})
    with client.websocket_connect(f"/agent/chat/ws/{session_id}?token={token}") as ws:
        with pytest.raises(WebSocketDisconnect) as closed:
            receive(ws)
    assert closed.value.code == events.CLOSE_FORBIDDEN


# --- Streaming, abort y turno nuevo -------------------------------------------------


def test_answer_streams_token_by_token_and_completes(client, auth_headers, model, inventory_engine) -> None:
    model.script("30 horas", ["Sin ", "cargo ", "si ", "cancelas ", "con ", "más ", "de ", "24 ", "horas."])
    session_id = new_session(client, auth_headers)
    with client.websocket_connect(f"/agent/chat/ws/{session_id}?token={token_of(auth_headers)}") as ws:
        receive(ws)  # snapshot
        send(ws, events.USER_MESSAGE, session_id, text=FIRST_QUESTION)
        frames = receive_until(ws, events.GENERATION_COMPLETED)

    echo = frames[0]
    assert echo["event"] == "user_message"
    assert echo["data"] == {"session_id": session_id, "message_id": echo["data"]["message_id"], "text": FIRST_QUESTION, "status": "completed"}
    chunks = tokens(frames)
    # Varios token_chunk con sequence consecutivo: no una respuesta de golpe.
    assert len(chunks) >= 8
    assert [c["sequence"] for c in chunks] == list(range(1, len(chunks) + 1))
    assert set(chunks[0]) == {"session_id", "message_id", "token", "sequence"}
    done = frames[-1]["data"]
    assert done["text"] == "".join(c["token"] for c in chunks) == "Sin cargo si cancelas con más de 24 horas."
    assert done["message_id"] == chunks[0]["message_id"] and done["status"] == "completed"
    wait_idle(session_id)
    stored = stored_messages(inventory_engine, session_id)
    assert [(m.role, m.status) for m in stored] == [("user", "completed"), ("assistant", "completed")]


def test_interrupt_aborts_the_generation_keeps_the_partial_and_starts_a_new_turn(
    client, auth_headers, model, inventory_engine
) -> None:
    model.script("30 horas", ["Según ", "HIPAA, ", "el ", "plazo ", "es ", "de ", "60 ", "días."], pause_after=3)
    model.script("UK GDPR", ["Bajo ", "UK ", "GDPR ", "el ", "plazo ", "es ", "de ", "72 ", "horas."])
    session_id = new_session(client, auth_headers)
    with client.websocket_connect(f"/agent/chat/ws/{session_id}?token={token_of(auth_headers)}") as ws:
        receive(ws)
        send(ws, events.USER_MESSAGE, session_id, text=FIRST_QUESTION)
        first = [receive(ws) for _ in range(4)]  # eco + 3 tokens; el modelo queda a mitad
        original_id = tokens(first)[0]["message_id"]

        send(ws, events.INTERRUPT_REQUESTED, session_id, new_input=REDIRECT)
        rest = receive_until(ws, events.GENERATION_COMPLETED)

    interrupted_at = next(i for i, f in enumerate(rest) if f["event"] == events.GENERATION_INTERRUPTED)
    assert rest[interrupted_at]["data"] == {"session_id": session_id, "message_id": original_id, "status": "interrupted"}
    # Ni un token más de la generación original, ni antes ni después del aviso.
    assert tokens(rest, original_id) == []
    # El abort cortó la generación en origen: el modelo vio la orden y paró.
    assert model.aborted == ["30 horas"] and model.emitted_after_stop == 0

    # Turno NUEVO: eco de la nueva entrada y respuesta con otro message_id.
    after = rest[interrupted_at + 1 :]
    assert after[0]["event"] == "user_message" and after[0]["data"]["text"] == REDIRECT
    new_tokens = tokens(after)
    assert new_tokens and {t["message_id"] for t in new_tokens} != {original_id}
    assert after[-1]["data"]["text"] == "Bajo UK GDPR el plazo es de 72 horas."

    wait_idle(session_id)
    stored = stored_messages(inventory_engine, session_id)
    assert [(m.role, m.status) for m in stored] == [
        ("user", "completed"),
        ("assistant", "interrupted"),
        ("user", "completed"),
        ("assistant", "completed"),
    ]
    partial = stored[1]
    assert partial.message_id == original_id
    assert partial.text == "Según HIPAA, el "  # lo que se llegó a mostrar, sin sobrescribir
    assert stored[3].replies_to == stored[2].message_id


def test_reconnecting_with_the_same_session_rehydrates_the_thread(client, auth_headers, model) -> None:
    model.script("30 horas", ["Según ", "HIPAA ", "sesenta ", "días."], pause_after=2)
    model.script("UK GDPR", ["Bajo ", "UK ", "GDPR, ", "72 ", "horas."])
    session_id = new_session(client, auth_headers)
    url = f"/agent/chat/ws/{session_id}?token={token_of(auth_headers)}"
    with client.websocket_connect(url) as ws:
        receive(ws)
        send(ws, events.USER_MESSAGE, session_id, text=FIRST_QUESTION)
        [receive(ws) for _ in range(3)]
        send(ws, events.INTERRUPT_REQUESTED, session_id, new_input=REDIRECT)
        receive_until(ws, events.GENERATION_COMPLETED)
    wait_idle(session_id)

    with client.websocket_connect(url) as ws:  # "se cayó la conexión" y vuelve
        snapshot = receive(ws)
    assert snapshot["event"] == "session_snapshot"
    messages = snapshot["data"]["messages"]
    assert [(m["role"], m["status"], m["text"]) for m in messages] == [
        ("user", "completed", FIRST_QUESTION),
        ("assistant", "interrupted", "Según HIPAA "),
        ("user", "completed", REDIRECT),
        ("assistant", "completed", "Bajo UK GDPR, 72 horas."),
    ]
    assert set(messages[0]) == {"message_id", "role", "text", "status", "replies_to", "created_at"}
    assert snapshot["data"]["status"] == "active" and snapshot["data"]["in_progress"] is None


def test_reconnecting_mid_answer_resumes_from_the_snapshot_without_repeating_tokens(client, auth_headers, model) -> None:
    model.script("30 horas", ["Sin ", "cargo ", "con ", "más ", "de ", "24 ", "horas."], pause_after=2)
    session_id = new_session(client, auth_headers)
    url = f"/agent/chat/ws/{session_id}?token={token_of(auth_headers)}"
    # Gotcha del TestClient de Starlette: cada websocket_connect tiene SU
    # event loop, y al cerrarlo espera a los hilos que se lanzaron desde él
    # (aquí, el turno del agente). Por eso el socket nuevo entra antes de que
    # caiga el viejo. Con uvicorn hay un solo loop y no pasa.
    first = client.websocket_connect(url)
    old = first.__enter__()
    receive(old)
    send(old, events.USER_MESSAGE, session_id, text=FIRST_QUESTION)
    [receive(old) for _ in range(3)]  # eco + 2 tokens; el modelo queda a mitad
    second = client.websocket_connect(url)
    ws = second.__enter__()
    try:
        snapshot = receive(ws)["data"]
        # La conexión vieja cae mientras el modelo sigue escribiendo: la
        # generación no depende de ningún socket (productor desacoplado).
        threading.Timer(0.1, model.release.set).start()
        first.__exit__(None, None, None)
        frames = receive_until(ws, events.GENERATION_COMPLETED)
    finally:
        second.__exit__(None, None, None)

    in_progress = snapshot["in_progress"]
    assert in_progress["text"] == "Sin cargo " and in_progress["sequence"] == 2
    assert [m["role"] for m in snapshot["messages"]] == ["user"]
    resumed = tokens(frames)
    assert resumed[0]["sequence"] == 3  # continúa donde lo dejó el snapshot
    assert in_progress["text"] + "".join(t["token"] for t in resumed) == frames[-1]["data"]["text"]
    wait_idle(session_id)


# --- Pub/sub: supervisor ------------------------------------------------------------


def test_supervisor_receives_the_same_events_without_a_second_agent_run(
    client, auth_headers, admin_headers, model, monkeypatch
) -> None:
    calls = []
    model.script("30 horas", ["Sin ", "cargo."])

    def counting(question, context, *, answer_stream=None):
        calls.append(question)
        return model(question, context, answer_stream=answer_stream)

    monkeypatch.setattr(reply, "generate_reply", counting)
    session_id = new_session(client, auth_headers)
    with client.websocket_connect(f"/agent/chat/ws/{session_id}?token={token_of(auth_headers)}") as owner:
        receive(owner)
        with client.websocket_connect(f"/agent/chat/ws/{session_id}?token={token_of(admin_headers)}") as supervisor:
            receive(supervisor)
            send(owner, events.USER_MESSAGE, session_id, text=FIRST_QUESTION)
            seen_by_owner = receive_until(owner, events.GENERATION_COMPLETED)
            seen_by_supervisor = receive_until(supervisor, events.GENERATION_COMPLETED)
            send(supervisor, events.USER_MESSAGE, session_id, text="Lo cambio yo")
            denied = receive(supervisor)
    assert seen_by_supervisor == seen_by_owner
    assert len(calls) == 1
    assert denied["event"] == "chat_error" and denied["data"]["code"] == "read_only"
    wait_idle(session_id)


# --- Privacidad y guardarraíl -------------------------------------------------------


def test_output_guard_checks_every_word_and_blocks_before_phi_is_shown(client, auth_headers, model, inventory_engine) -> None:
    model.script("30 horas", ["Escribe ", "a ", "ana.lopez@example.com ", "para ", "confirmarlo."])
    session_id = new_session(client, auth_headers)
    with client.websocket_connect(f"/agent/chat/ws/{session_id}?token={token_of(auth_headers)}") as ws:
        receive(ws)
        send(ws, events.USER_MESSAGE, session_id, text=FIRST_QUESTION)
        frames = receive_until(ws, events.GENERATION_BLOCKED)

    assert "example.com" not in str(frames)
    assert "".join(t["token"] for t in tokens(frames)) == "Escribe a "
    blocked = frames[-1]["data"]
    assert blocked["status"] == "blocked" and "paciente" in blocked["text"]
    assert model.aborted == ["30 horas"]  # el guardarraíl también corta la generación
    wait_idle(session_id)
    assert stored_messages(inventory_engine, session_id)[-1].status == "blocked"


def test_patient_data_is_never_echoed_nor_stored(client, auth_headers, model, inventory_engine) -> None:
    session_id = new_session(client, auth_headers)
    with client.websocket_connect(f"/agent/chat/ws/{session_id}?token={token_of(auth_headers)}") as ws:
        receive(ws)
        send(ws, events.USER_MESSAGE, session_id, text="El paciente John Smith, MRN 12345678, pregunta por su factura")
        frames = receive_until(ws, events.GENERATION_COMPLETED)

    assert "Smith" not in str(frames) and "12345678" not in str(frames)
    assert frames[0]["data"]["text"] is None and frames[0]["data"]["status"] == "redacted"
    # El guardarraíl de entrada responde con su texto fijo, también por tokens.
    assert tokens(frames) and frames[-1]["data"]["outcome"] == "guardrail_blocked"
    wait_idle(session_id)
    user = stored_messages(inventory_engine, session_id)[0]
    assert user.text is None and user.status == "redacted"


# --- Cierre y entrada inválida ------------------------------------------------------


def test_invalid_frames_get_a_named_error_and_the_socket_stays_open(client, auth_headers, model) -> None:
    session_id = new_session(client, auth_headers)
    with client.websocket_connect(f"/agent/chat/ws/{session_id}?token={token_of(auth_headers)}") as ws:
        receive(ws)
        ws.send_text("no es json")
        assert receive(ws)["data"]["code"] == "invalid_json"
        send(ws, events.USER_MESSAGE, "chat_de_otra_sesion", text="hola")
        assert receive(ws)["data"]["code"] == "session_mismatch"
        send(ws, events.USER_MESSAGE, session_id, text="   ")
        assert receive(ws)["data"]["code"] == "empty_input"


def test_closed_session_rejects_new_connections(client, auth_headers, model, inventory_engine) -> None:
    session_id = new_session(client, auth_headers)
    response = client.post(f"/agent/chat/sessions/{session_id}/close", headers=auth_headers)
    assert response.status_code == 200 and response.json()["status"] == "closed"
    with client.websocket_connect(f"/agent/chat/ws/{session_id}?token={token_of(auth_headers)}") as ws:
        with pytest.raises(WebSocketDisconnect) as closed:
            receive(ws)
    assert closed.value.code == events.CLOSE_SESSION_CLOSED
    with Session(inventory_engine) as db:
        assert db.get(ChatSession, session_id).status == "closed"


def test_sessions_are_listed_only_for_their_owner(client, auth_headers, admin_headers, model) -> None:
    mine = new_session(client, auth_headers)
    new_session(client, admin_headers)
    listed = client.get("/agent/chat/sessions", headers=auth_headers).json()
    assert [s["session_id"] for s in listed] == [mine]
    assert client.post("/agent/chat/sessions").status_code == 401
