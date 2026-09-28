"""Contratos de POST /agent/query y de los endpoints de memoria.

La petición reutiliza la validación de POST /knowledge/query (misma pregunta,
mismos límites). La respuesta añade `trace_id` para poder localizar el trace
de la corrida; nunca devuelve chunks, puntuaciones ni el recorrido.

`memory` (Ticket #MEM-092) dice qué pasó con la memoria en el turno: la
decisión sobre la propuesta que estaba pendiente (`resolved`) y la propuesta
nueva o su bloqueo (`offered`). `content` solo lleva textos ya validados sin
PHI. `trace_id` es null cuando el mensaje solo resolvía una propuesta y no
hizo falta ejecutar el grafo."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel

from services.knowledge.schemas import KnowledgeQueryRequest


class AgentQueryRequest(KnowledgeQueryRequest):
    pass


class MemoryEventOut(BaseModel):
    status: str
    proposal_id: Optional[str] = None
    content: Optional[str] = None


class MemoryTurnOut(BaseModel):
    resolved: Optional[MemoryEventOut] = None
    offered: Optional[MemoryEventOut] = None


class AgentQueryResponse(BaseModel):
    answer: str
    trace_id: Optional[str] = None
    outcome: Optional[str] = None
    memory: MemoryTurnOut = MemoryTurnOut()


class AgentMemoryOut(BaseModel):
    id: uuid.UUID
    kind: str
    clinic: Optional[str] = None
    content: str
    last_confirmed_at: datetime


class AgentMemoryAuditOut(BaseModel):
    """Una fila del registro de auditoría. Sin textos de los mensajes del
    usuario: solo sus huellas SHA-256."""

    id: uuid.UUID
    user_id: str
    status: str
    kind: Optional[str] = None
    clinic: Optional[str] = None
    proposed_content: Optional[str] = None
    final_content: Optional[str] = None
    phi_categories: List[str] = []
    origin_message_sha256: str
    origin_trace_id: Optional[str] = None
    decision_label: Optional[str] = None
    decision_confidence: Optional[float] = None
    decision_message_sha256: Optional[str] = None
    decision_note: Optional[str] = None
    created_at: datetime
    resolved_at: Optional[datetime] = None
    memory_id: Optional[uuid.UUID] = None
