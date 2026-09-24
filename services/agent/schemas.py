"""Contratos de POST /agent/query.

La petición reutiliza la validación de POST /knowledge/query (misma pregunta,
mismos límites). La respuesta añade `trace_id` para poder localizar el trace
de la corrida; nunca devuelve chunks, puntuaciones ni el recorrido."""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel

from services.knowledge.schemas import KnowledgeQueryRequest


class AgentQueryRequest(KnowledgeQueryRequest):
    pass


class AgentQueryResponse(BaseModel):
    answer: str
    trace_id: str
    outcome: Optional[str] = None
