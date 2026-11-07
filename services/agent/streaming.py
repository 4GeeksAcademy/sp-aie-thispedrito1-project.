"""Streaming de la respuesta del agente (Sistemas en Tiempo Real, Parte 2).

Puente entre el nodo `generate` y quien consume la corrida (el chat por
WebSocket). Va por los canales propios de LangGraph, sin tocar el estado:

- Los trozos de la respuesta salen con `get_stream_writer()` (modo
  `custom`), así que `graph.stream(..., stream_mode=["updates", "custom"])`
  los entrega según se generan. Comprobado en LangGraph 0.6.11: llegan en
  tiempo real, no acumulados al terminar el nodo.
- La orden de parar llega como una función en `configurable`
  (`STOP_CONFIG_KEY`). No es parte de `AgentState`, así que no viaja en los
  checkpoints (comprobado: la metadata del checkpoint no la incluye).

Sin consumidor (POST /agent/query), `current_answer_stream()` devuelve None
y la generación es la de siempre: una sola respuesta completa.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional

STOP_CONFIG_KEY = "answer_stream_should_stop"
ANSWER_DELTA = "answer_delta"


class GenerationAborted(RuntimeError):
    """El consumidor pidió parar: la generación se cortó a propósito, no falló."""


@dataclass(frozen=True)
class AnswerStream:
    emit: Callable[[str], None]  # un trozo nuevo del texto de la respuesta
    should_stop: Callable[[], bool]

    def check(self) -> None:
        if self.should_stop():
            raise GenerationAborted("Answer generation aborted by the client")


def current_answer_stream() -> Optional[AnswerStream]:
    """El stream de la corrida en curso, o None si nadie lo está escuchando
    (o si se llama fuera de un nodo de LangGraph, p. ej. en un test unitario)."""
    from langgraph.config import get_config, get_stream_writer

    try:
        config = get_config()
    except RuntimeError:
        return None
    should_stop = (config.get("configurable") or {}).get(STOP_CONFIG_KEY)
    if should_stop is None:
        return None
    writer = get_stream_writer()
    return AnswerStream(emit=lambda delta: writer({ANSWER_DELTA: delta}), should_stop=should_stop)
