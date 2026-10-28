"""Contrato de eventos del chat por WebSocket (BRIEFING Parte 2, §4).

Todo mensaje del cable, en las dos direcciones, es `{"event": <nombre>,
"data": {...}}`: nombres explícitos y payloads con campos fijos, nunca un
único tipo de mensaje genérico. Los seis del BRIEFING con sus campos
literales (`session_id`, `token`, `sequence`, `new_input`, `message_id`,
`status`, `messages`, `text`); campos añadidos solo donde el BRIEFING no
llega (p. ej. `message_id` en `token_chunk`, para saber a qué burbuja va).

No se mezcla nada del contrato SSE de RFPs de la Parte 1 (BRIEFING §6).

Cliente → servidor: `auth` (primer frame, si no vino `?token=`),
`user_message` e `interrupt_requested`.
Servidor → cliente: `session_snapshot`, `user_message` (eco del turno del
usuario, ya sin PHI), `token_chunk`, `generation_interrupted`,
`generation_completed`, y tres que el BRIEFING no cubre pero existen:
`generation_blocked` (el guardarraíl retuvo la respuesta a mitad),
`generation_failed` (el agente no pudo responder) y `chat_error`.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from services.knowledge.schemas import MAX_QUESTION_LENGTH

# --- Nombres de evento ---------------------------------------------------------
TOKEN_CHUNK = "token_chunk"
INTERRUPT_REQUESTED = "interrupt_requested"
GENERATION_INTERRUPTED = "generation_interrupted"
GENERATION_COMPLETED = "generation_completed"
SESSION_SNAPSHOT = "session_snapshot"
USER_MESSAGE = "user_message"
GENERATION_BLOCKED = "generation_blocked"
GENERATION_FAILED = "generation_failed"
CHAT_ERROR = "chat_error"
AUTH = "auth"

CLIENT_EVENTS = (AUTH, USER_MESSAGE, INTERRUPT_REQUESTED)
MAX_INPUT_CHARS = MAX_QUESTION_LENGTH  # mismo tope que la pregunta de POST /agent/query

# Códigos de cierre del WebSocket (rango 4000-4999, reservado a la aplicación).
CLOSE_UNAUTHENTICATED = 4401
CLOSE_FORBIDDEN = 4403
CLOSE_SESSION_NOT_FOUND = 4404
CLOSE_SESSION_CLOSED = 4409
CLOSE_SLOW_CONSUMER = 4429  # cola llena: el cliente reconecta y se rehidrata
CLOSE_UNAVAILABLE = 1011  # sin base de datos no hay historial que rehidratar


def envelope(event: str, data: Dict[str, Any]) -> Dict[str, Any]:
    return {"event": event, "data": data}


def token_chunk(session_id: str, message_id: str, token: str, sequence: int) -> Dict[str, Any]:
    return envelope(TOKEN_CHUNK, {"session_id": session_id, "message_id": message_id, "token": token, "sequence": sequence})


def generation_interrupted(session_id: str, message_id: str) -> Dict[str, Any]:
    return envelope(GENERATION_INTERRUPTED, {"session_id": session_id, "message_id": message_id, "status": "interrupted"})


def generation_completed(session_id: str, message_id: str, text: str, outcome: Optional[str]) -> Dict[str, Any]:
    # `text` es la versión final y persistida: puede llevar, además de lo
    # streameado, la nota de memoria que el agente añade al final.
    return envelope(
        GENERATION_COMPLETED,
        {"session_id": session_id, "message_id": message_id, "status": "completed", "text": text, "outcome": outcome},
    )


def generation_blocked(session_id: str, message_id: str, text: str) -> Dict[str, Any]:
    return envelope(GENERATION_BLOCKED, {"session_id": session_id, "message_id": message_id, "status": "blocked", "text": text})


def generation_failed(session_id: str, message_id: str, text: str) -> Dict[str, Any]:
    return envelope(GENERATION_FAILED, {"session_id": session_id, "message_id": message_id, "status": "failed", "text": text})


def user_message(session_id: str, message_id: str, text: Optional[str], status: str) -> Dict[str, Any]:
    return envelope(USER_MESSAGE, {"session_id": session_id, "message_id": message_id, "text": text, "status": status})


def session_snapshot(
    session_id: str, status: str, messages: List[Dict[str, Any]], in_progress: Optional[Dict[str, Any]]
) -> Dict[str, Any]:
    return envelope(
        SESSION_SNAPSHOT, {"session_id": session_id, "status": status, "messages": messages, "in_progress": in_progress}
    )


def chat_error(session_id: str, code: str, message: str) -> Dict[str, Any]:
    return envelope(CHAT_ERROR, {"session_id": session_id, "code": code, "message": message})


# --- Lo que manda el cliente ---------------------------------------------------


class ClientEventError(ValueError):
    """Frame del cliente que no cumple el contrato. `code` va en `chat_error`."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class ClientEvent:
    event: str
    text: str = ""  # user_message.text o interrupt_requested.new_input
    token: str = ""  # solo auth


def parse_client_event(raw: str, session_id: str) -> ClientEvent:
    """Frame de texto del navegador → ClientEvent validado, o ClientEventError."""
    try:
        message = json.loads(raw)
    except (TypeError, ValueError) as exc:
        raise ClientEventError("invalid_json", "El mensaje no es JSON válido.") from exc
    if not isinstance(message, dict) or not isinstance(message.get("data"), dict):
        raise ClientEventError("invalid_envelope", 'Formato esperado: {"event": ..., "data": {...}}.')
    event, data = message.get("event"), message["data"]
    if event not in CLIENT_EVENTS:
        raise ClientEventError("unknown_event", "Evento no reconocido.")
    if event == AUTH:
        token = data.get("token")
        if not isinstance(token, str) or not token:
            raise ClientEventError("invalid_auth", "Falta el token.")
        return ClientEvent(AUTH, token=token)
    # La sesión del frame tiene que ser la del socket: un socket, un hilo.
    if data.get("session_id") != session_id:
        raise ClientEventError("session_mismatch", "El session_id no corresponde a esta conexión.")
    field = "text" if event == USER_MESSAGE else "new_input"
    text = data.get(field)
    if not isinstance(text, str) or not text.strip():
        raise ClientEventError("empty_input", "El mensaje está vacío.")
    if len(text) > MAX_INPUT_CHARS:
        raise ClientEventError("input_too_long", f"El mensaje supera {MAX_INPUT_CHARS} caracteres.")
    return ClientEvent(event, text=text.strip())
