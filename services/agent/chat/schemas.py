"""Contratos HTTP del chat (crear, listar y cerrar sesiones).

El de `ChatSession` lleva exactamente los campos del BRIEFING §3. Los
mensajes no tienen endpoint propio: viajan en `session_snapshot` por el
WebSocket, que es donde se rehidrata la conversación.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class ChatSessionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    session_id: str
    agent_id: str
    user_id: str
    status: str
    created_at: datetime
