"""POST /agent/query: la misma consulta que /knowledge/query, resuelta por el grafo.

Solo HTTP: autenticación, validación y códigos de estado. No decide nada del
flujo (eso son las aristas del grafo) ni recupera ni genera (eso es
data/pipelines/rag.py). Convive con POST /knowledge/query, que no cambia.

El grafo se compila al importar este módulo, es decir, al arrancar la API:
un error estructural impide arrancar en vez de aparecer en una petición.
"""

from __future__ import annotations

import logging
from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException, status

from data.process.rag import RagConfigError
from security import get_current_user
from services.agent.graph import OUTCOME_INVALID_QUESTION, compile_agent_graph
from services.agent.schemas import AgentQueryRequest, AgentQueryResponse
from services.agent.tracing import AgentRunError, run_agent

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/agent", tags=["agent"], dependencies=[Depends(get_current_user)])

_AGENT = compile_agent_graph()


def get_agent() -> Any:
    """Dependencia sustituible en tests por un grafo con dobles."""
    return _AGENT


@router.post("/query", response_model=AgentQueryResponse)
def query_agent(payload: AgentQueryRequest, agent: Any = Depends(get_agent)) -> Dict[str, Any]:
    try:
        result = run_agent(agent, payload.question)
    except AgentRunError as exc:
        if isinstance(exc.cause, RagConfigError):
            logger.error("Agent misconfigured at node %s: %s", exc.node, exc.cause)
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Support agent is not configured.",
            ) from exc
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Support agent is temporarily unavailable.",
        ) from exc

    # La validación del esquema ya rechaza preguntas en blanco; esta rama es la
    # red del grafo por si otro cliente lo invoca sin pasar por el esquema.
    if result.outcome == OUTCOME_INVALID_QUESTION or not result.answer:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Question must not be blank.")
    return {"answer": result.answer, "trace_id": result.trace_id, "outcome": result.outcome}
