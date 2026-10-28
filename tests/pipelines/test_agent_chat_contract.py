"""Contrato de eventos del chat por WebSocket y piezas puras del streaming.

Sin red, sin base de datos y sin FastAPI: fijan los nombres y campos de
cada evento (BRIEFING Parte 2, §4), la validación de lo que manda el
navegador, la extracción de `answer` del JSON del modelo a trozos y la
regla de qué texto se puede publicar ya. La integración (socket real,
abort, rehidratación) está en services/api/tests/test_agent_chat.py.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from services.agent.chat import events
from services.agent.chat.events import ClientEventError, parse_client_event
from services.agent.chat.service import split_ready_text, word_pieces
from services.agent.memory.reply import AnswerDeltaExtractor, generate_reply
from services.agent.streaming import AnswerStream, GenerationAborted

SID = "chat_0076"


# --- Eventos servidor → cliente -------------------------------------------------


def test_token_chunk_carries_the_briefing_fields() -> None:
    assert events.token_chunk(SID, "msg_0163", "Según", 9) == {
        "event": "token_chunk",
        "data": {"session_id": SID, "message_id": "msg_0163", "token": "Según", "sequence": 9},
    }


def test_generation_interrupted_marks_the_message_as_interrupted() -> None:
    assert events.generation_interrupted(SID, "msg_0162") == {
        "event": "generation_interrupted",
        "data": {"session_id": SID, "message_id": "msg_0162", "status": "interrupted"},
    }


def test_generation_completed_names_the_message_and_its_final_text() -> None:
    frame = events.generation_completed(SID, "msg_0163", "Bajo UK GDPR…", "answered")
    assert frame["event"] == "generation_completed"
    assert frame["data"] == {
        "session_id": SID,
        "message_id": "msg_0163",
        "status": "completed",
        "text": "Bajo UK GDPR…",
        "outcome": "answered",
    }


def test_snapshot_and_user_message_follow_the_briefing() -> None:
    assert events.session_snapshot(SID, "active", [], None) == {
        "event": "session_snapshot",
        "data": {"session_id": SID, "status": "active", "messages": [], "in_progress": None},
    }
    assert events.user_message(SID, "msg_1", "hola", "completed")["data"] == {
        "session_id": SID,
        "message_id": "msg_1",
        "text": "hola",
        "status": "completed",
    }


def test_every_server_event_has_its_own_name_and_a_session_id() -> None:
    frames = [
        events.token_chunk(SID, "m", "t", 1),
        events.generation_interrupted(SID, "m"),
        events.generation_completed(SID, "m", "x", None),
        events.generation_blocked(SID, "m", "x"),
        events.generation_failed(SID, "m", "x"),
        events.session_snapshot(SID, "active", [], None),
        events.user_message(SID, "m", None, "redacted"),
        events.chat_error(SID, "code", "x"),
    ]
    names = [frame["event"] for frame in frames]
    assert len(set(names)) == len(names)  # no hay un único tipo de mensaje genérico
    assert all(set(frame) == {"event", "data"} and frame["data"]["session_id"] == SID for frame in frames)
    json.dumps(frames)  # todo serializable tal cual


# --- Eventos cliente → servidor -------------------------------------------------


def frame(event: str, **data: object) -> str:
    return json.dumps({"event": event, "data": data})


def test_interrupt_requested_carries_the_new_input() -> None:
    raw = frame("interrupt_requested", session_id=SID, new_input="  espera, mi pregunta era sobre UK GDPR, no HIPAA ")
    event = parse_client_event(raw, SID)
    assert event.event == "interrupt_requested"
    assert event.text == "espera, mi pregunta era sobre UK GDPR, no HIPAA"


def test_user_message_and_auth_frames() -> None:
    assert parse_client_event(frame("user_message", session_id=SID, text="¿BAA?"), SID).text == "¿BAA?"
    assert parse_client_event(frame("auth", token="jwt"), SID).token == "jwt"


@pytest.mark.parametrize(
    "raw, code",
    [
        ("no json", "invalid_json"),
        (json.dumps({"event": "user_message"}), "invalid_envelope"),
        (frame("token_chunk", session_id=SID), "unknown_event"),  # el cliente no puede emitir eventos del servidor
        (frame("user_message", session_id="chat_otra", text="hola"), "session_mismatch"),
        (frame("interrupt_requested", session_id=SID, new_input=" "), "empty_input"),
        (frame("user_message", session_id=SID, text="x" * (events.MAX_INPUT_CHARS + 1)), "input_too_long"),
        (frame("auth", token=""), "invalid_auth"),
    ],
)
def test_frames_outside_the_contract_are_rejected_with_a_code(raw: str, code: str) -> None:
    with pytest.raises(ClientEventError) as error:
        parse_client_event(raw, SID)
    assert error.value.code == code


# --- Extracción de `answer` del JSON a trozos ------------------------------------

REPLY = {
    "answer": 'Según "UK GDPR":\nsí — 72 h. ✅',
    "memory_proposal": None,
    "user_requested_memory": False,
}


def test_extractor_rebuilds_the_answer_wherever_the_provider_splits_the_json() -> None:
    raw = json.dumps(REPLY)  # con escapes \" \n — ✅
    for cut in range(1, len(raw)):
        extractor = AnswerDeltaExtractor()
        assert extractor.feed(raw[:cut]) + extractor.feed(raw[cut:]) == REPLY["answer"], cut


def test_extractor_streams_character_by_character_and_ignores_other_fields() -> None:
    raw = json.dumps({"memory_proposal": {"content": "x"}, **REPLY}, ensure_ascii=False)
    extractor = AnswerDeltaExtractor()
    assert "".join(extractor.feed(char) for char in raw) == REPLY["answer"]


def test_extractor_emits_nothing_when_the_model_does_not_answer_in_json() -> None:
    assert AnswerDeltaExtractor().feed("Respuesta en texto plano") == ""


# --- Streaming de generate_reply contra un proveedor simulado --------------------


class FakeStream:
    def __init__(self, pieces):
        self.pieces = pieces
        self.closed = False
        self.consumed = 0

    def __iter__(self):
        for piece in self.pieces:
            self.consumed += 1
            yield SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content=piece))])

    def close(self):
        self.closed = True


def fake_client(stream, calls=None):
    def create(**kwargs):
        if calls is not None:
            calls.append(kwargs)
        return stream if kwargs.get("stream") else None

    return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))


def test_streaming_does_not_ask_for_json_mode() -> None:
    # Medido con deepseek por el proxy: en modo JSON el proveedor retiene la
    # salida y la suelta toda al final. El prompt sigue pidiendo el JSON.
    calls = []
    raw = json.dumps(REPLY)
    generate_reply("q", [], client=fake_client(FakeStream([raw]), calls), model="m", answer_stream=AnswerStream(lambda d: None, lambda: False))
    assert calls[0]["stream"] is True and "response_format" not in calls[0]
    assert "JSON" in calls[0]["messages"][0]["content"]


def test_generate_reply_streams_the_answer_and_parses_the_same_reply() -> None:
    raw = json.dumps(REPLY)
    stream = FakeStream([raw[i : i + 7] for i in range(0, len(raw), 7)])
    seen = []
    reply = generate_reply("q", [], client=fake_client(stream), model="m", answer_stream=AnswerStream(seen.append, lambda: False))
    assert "".join(seen) == REPLY["answer"] == reply.answer
    assert len(seen) > 1 and stream.closed


def test_stop_closes_the_provider_stream_without_reading_the_rest() -> None:
    raw = json.dumps(REPLY)
    stream = FakeStream([raw[i : i + 5] for i in range(0, len(raw), 5)])
    seen = []
    stop = AnswerStream(seen.append, lambda: len(seen) >= 2)
    with pytest.raises(GenerationAborted):
        generate_reply("q", [], client=fake_client(stream), model="m", answer_stream=stop)
    assert stream.closed  # se cortó la conexión con el modelo
    assert stream.consumed < len(stream.pieces)  # y no se siguió leyendo
    assert len(seen) == 2


# --- Qué texto se puede publicar ya ---------------------------------------------


@pytest.mark.parametrize(
    "pending, ready, rest",
    [
        ("Según ", "Según ", ""),
        ("Según la pol", "Según la ", "pol"),
        ("Joh", "", "Joh"),  # palabra a medias: todavía no se puede juzgar
        ("línea\nsigu", "línea\n", "sigu"),
        ("", "", ""),
    ],
)
def test_split_ready_text_only_releases_complete_words(pending: str, ready: str, rest: str) -> None:
    assert split_ready_text(pending) == (ready, rest)
    assert ready + rest == pending  # nunca se pierde ni se duplica texto


def test_word_pieces_keep_the_text_intact() -> None:
    text = "No tengo información suficiente.\n\nConsúltalo."
    assert "".join(word_pieces(text)) == text and len(word_pieces(text)) == 5
