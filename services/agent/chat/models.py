"""Tablas del chat en tiempo real del Compliance assistant (Tiempo Real, Parte 2).

Nombres literales del BRIEFING §3 para la sesión (`ChatSession`:
`session_id`, `agent_id`, `user_id`, `status`, `created_at`). El mismo
`session_id` es el que el WebSocket exige en la URL para ligarse a un hilo.

Registradas en el mismo `SQLModel.metadata` que memoria, inventario y
telemetría: el `create_all` del arranque de la API las crea en Supabase.

- `chat_sessions`: una conversación entre UN miembro del staff y el agente.
  Nunca un paciente (BRIEFING §1: el chat es solo para personal interno).
- `chat_messages`: el historial con el que se rehidrata la conversación al
  reconectar. Un mensaje del asistente interrumpido conserva lo que ya se
  mostró con `status="interrupted"` y no se sobrescribe nunca: la respuesta
  redirigida es una fila nueva. Un mensaje del usuario con datos de un
  paciente se guarda SIN texto (`status="redacted"`): HIPAA / UK GDPR.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import Column, DateTime, Text
from sqlmodel import Field, SQLModel

AGENT_ID_COMPLIANCE_ASSISTANT = "compliance_assistant"

# chat_sessions.status (BRIEFING §3)
SESSION_ACTIVE = "active"
SESSION_INTERRUPTED = "interrupted"  # la última respuesta se cortó a petición del usuario
SESSION_CLOSED = "closed"
SESSION_STATUSES = (SESSION_ACTIVE, SESSION_INTERRUPTED, SESSION_CLOSED)

ROLE_USER = "user"
ROLE_ASSISTANT = "assistant"

# chat_messages.status
MESSAGE_COMPLETED = "completed"
MESSAGE_INTERRUPTED = "interrupted"  # abortada a mitad: queda el texto parcial
MESSAGE_BLOCKED = "blocked"  # el guardarraíl de salida retuvo la respuesta a mitad
MESSAGE_FAILED = "failed"  # el agente no pudo responder (proveedor caído...)
MESSAGE_REDACTED = "redacted"  # mensaje del usuario con datos de paciente: sin texto


def _utc_column() -> Column:
    return Column(DateTime(timezone=True), nullable=False)


class ChatSession(SQLModel, table=True):
    __tablename__ = "chat_sessions"

    session_id: str = Field(primary_key=True, max_length=40)
    agent_id: str = Field(default=AGENT_ID_COMPLIANCE_ASSISTANT, max_length=40)
    user_id: str = Field(max_length=64, index=True)  # el miembro del staff que chatea
    status: str = Field(default=SESSION_ACTIVE, max_length=16)
    created_at: datetime = Field(sa_column=_utc_column())


class ChatMessage(SQLModel, table=True):
    __tablename__ = "chat_messages"

    message_id: str = Field(primary_key=True, max_length=40)
    session_id: str = Field(foreign_key="chat_sessions.session_id", max_length=40, index=True)
    role: str = Field(max_length=16)  # user | assistant
    text: Optional[str] = Field(default=None, sa_column=Column(Text, nullable=True))
    status: str = Field(default=MESSAGE_COMPLETED, max_length=16)
    # Mensaje del usuario al que responde este turno del asistente.
    replies_to: Optional[str] = Field(default=None, max_length=40)
    trace_id: Optional[str] = Field(default=None, max_length=64)
    created_at: datetime = Field(sa_column=_utc_column())
