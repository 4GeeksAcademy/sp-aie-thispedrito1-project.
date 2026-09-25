"""Contratos de POST /knowledge/query (README del Hito 7, Fase 3).

La respuesta es SOLO el texto generado por el modelo: nunca chunks, fuentes
en bruto ni puntuaciones de similitud (tests/test_serialization.py barre el
OpenAPI y test_knowledge.py lo fija para este endpoint)."""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator

MAX_QUESTION_LENGTH = 1000


class KnowledgeQueryRequest(BaseModel):
    question: str = Field(min_length=1, max_length=MAX_QUESTION_LENGTH)

    @field_validator("question")
    @classmethod
    def question_not_blank(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("question must not be blank")
        return stripped


class KnowledgeQueryResponse(BaseModel):
    answer: str
