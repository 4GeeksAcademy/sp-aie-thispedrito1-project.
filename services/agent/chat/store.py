"""Lectura y escritura de sesiones y mensajes del chat (patrón repositorio).

Única puerta a `chat_sessions` / `chat_messages`, como `memory/store.py` lo
es de la memoria. No decide nada del flujo: eso es `service.py`.

Privacidad: `redact_user_text` decide si el texto del usuario se puede
guardar y retransmitir. Reutiliza los MISMOS detectores que el guardarraíl
de entrada y la memoria (`input_guard.patient_signals` +
`is_identifiable_patient_case`, `phi_guard.scan`); si hay datos de un
paciente, el mensaje queda sin texto. El guardarraíl de entrada seguirá
rechazando el turno igual que en POST /agent/query; esto solo evita que el
historial, el eco a otros sockets o un supervisor vean ese texto.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from sqlmodel import Session, select

from services.agent.chat.models import (
    MESSAGE_COMPLETED,
    MESSAGE_REDACTED,
    ROLE_USER,
    SESSION_ACTIVE,
    ChatMessage,
    ChatSession,
)
from services.agent.guardrails import patterns
from services.agent.guardrails.input_guard import is_identifiable_patient_case, patient_signals
from services.agent.memory import phi_guard

HISTORY_LIMIT = 200  # mensajes que se rehidratan como máximo


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def new_session_id() -> str:
    return f"chat_{uuid.uuid4().hex[:16]}"


def new_message_id() -> str:
    return f"msg_{uuid.uuid4().hex[:16]}"


def redact_user_text(text: str) -> Tuple[Optional[str], str]:
    """(texto a guardar o None, status). None + `redacted` si hay datos de un paciente."""
    signals = patient_signals(text, patterns.normalize(text))
    if is_identifiable_patient_case(signals) or phi_guard.scan(text).contains_phi:
        return None, MESSAGE_REDACTED
    return text, MESSAGE_COMPLETED


def message_out(message: ChatMessage) -> Dict[str, Any]:
    """Forma de un mensaje en `session_snapshot`."""
    created = message.created_at
    if created.tzinfo is None:  # SQLite (tests) devuelve fechas sin zona
        created = created.replace(tzinfo=timezone.utc)
    return {
        "message_id": message.message_id,
        "role": message.role,
        "text": message.text,
        "status": message.status,
        "replies_to": message.replies_to,
        "created_at": created.isoformat(),
    }


class ChatStore:
    def __init__(self, session: Session) -> None:
        self.session = session

    def create_session(self, user_id: str) -> ChatSession:
        chat = ChatSession(session_id=new_session_id(), user_id=user_id, status=SESSION_ACTIVE, created_at=utcnow())
        self.session.add(chat)
        self.session.commit()
        self.session.refresh(chat)
        return chat

    def get_session(self, session_id: str) -> Optional[ChatSession]:
        return self.session.get(ChatSession, session_id)

    def list_sessions(self, user_id: str, limit: int = 20) -> List[ChatSession]:
        statement = (
            select(ChatSession)
            .where(ChatSession.user_id == user_id)
            .order_by(ChatSession.created_at.desc())  # type: ignore[attr-defined]
            .limit(limit)
        )
        return list(self.session.exec(statement))

    def set_status(self, session_id: str, status: str) -> None:
        chat = self.get_session(session_id)
        if chat is not None and chat.status != status:
            chat.status = status
            self.session.add(chat)
            self.session.commit()

    def add_message(
        self,
        *,
        session_id: str,
        role: str,
        text: Optional[str],
        status: str,
        message_id: Optional[str] = None,
        replies_to: Optional[str] = None,
        trace_id: Optional[str] = None,
        created_at: Optional[datetime] = None,
    ) -> ChatMessage:
        message = ChatMessage(
            message_id=message_id or new_message_id(),
            session_id=session_id,
            role=role,
            text=text,
            status=status,
            replies_to=replies_to,
            trace_id=trace_id,
            created_at=created_at or utcnow(),
        )
        self.session.add(message)
        self.session.commit()
        self.session.refresh(message)
        return message

    def add_user_message(self, session_id: str, text: str) -> ChatMessage:
        stored, status = redact_user_text(text)
        return self.add_message(session_id=session_id, role=ROLE_USER, text=stored, status=status)

    def history(self, session_id: str) -> List[ChatMessage]:
        statement = (
            select(ChatMessage)
            .where(ChatMessage.session_id == session_id)
            .order_by(ChatMessage.created_at.desc())  # type: ignore[attr-defined]
            .limit(HISTORY_LIMIT)
        )
        return list(reversed(list(self.session.exec(statement))))
