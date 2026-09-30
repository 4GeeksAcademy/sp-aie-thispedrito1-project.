"""POST /agent/query: la misma consulta que /knowledge/query, resuelta por el grafo.

Solo HTTP: autenticación, validación y códigos de estado. No decide nada del
flujo (eso son las aristas del grafo) ni recupera ni genera (eso es
data/pipelines/rag.py). Convive con POST /knowledge/query, que no cambia.

Memoria (Ticket #MEM-092): cada consulta pasa por
`memory.conversation.handle_turn`, que resuelve primero una propuesta de
memoria pendiente y después ejecuta el grafo. La sesión de Supabase es la
opcional: sin base de datos el agente responde igual, solo que sin memoria.
`GET /agent/memory` muestra lo que el agente recuerda para quien pregunta;
`GET /agent/memory/audit` y `DELETE /agent/memory/{id}` son solo de admin.

Harness (Ticket #SEC-114): los guardarraíles actúan dentro de `handle_turn`;
aquí solo se expone qué intervino (`guardrail` en la respuesta) y el resumen
de activaciones de la sesión (`GET /agent/guardrails/summary`, solo admin).

El grafo se compila al importar este módulo, es decir, al arrancar la API:
un error estructural impide arrancar en vez de aparecer en una petición.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlmodel import Session

from data.process.rag import RagConfigError
from database import get_inventory_db, get_inventory_db_optional
from security import get_current_user, require_admin
from services.agent.graph import OUTCOME_INVALID_QUESTION, compile_agent_graph
from services.agent.guardrails.monitor import MONITOR, GuardEvent
from services.agent.memory.conversation import MemoryEvent, handle_turn
from services.agent.memory.decision import classify_decision
from services.agent.memory.store import MemoryStore
from services.agent.schemas import (
    AgentMemoryAuditOut,
    AgentMemoryOut,
    AgentQueryRequest,
    AgentQueryResponse,
    GuardrailSummaryOut,
)
from services.agent.tracing import AgentRunError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/agent", tags=["agent"], dependencies=[Depends(get_current_user)])

_AGENT = compile_agent_graph()


def get_agent() -> Any:
    """Dependencia sustituible en tests por un grafo con dobles."""
    return _AGENT


def get_decision_classifier() -> Any:
    """Dependencia sustituible en tests por un clasificador determinista."""
    return classify_decision


def _event(event: Optional[MemoryEvent]) -> Optional[Dict[str, Any]]:
    if event is None:
        return None
    return {"status": event.status, "proposal_id": event.proposal_id, "content": event.content}


def _guardrail(event: Optional[GuardEvent]) -> Optional[Dict[str, Any]]:
    if event is None:
        return None
    return {"guard": event.guard, "action": event.action, "failure_type": event.failure_type, "reason": event.reason}


@router.post("/query", response_model=AgentQueryResponse)
def query_agent(
    payload: AgentQueryRequest,
    agent: Any = Depends(get_agent),
    user: Dict[str, Any] = Depends(get_current_user),
    session: Optional[Session] = Depends(get_inventory_db_optional),
    classify_fn: Any = Depends(get_decision_classifier),
) -> Dict[str, Any]:
    store = MemoryStore(session) if session is not None else None
    try:
        result = handle_turn(agent, payload.question, user_id=str(user["id"]), store=store, classify_fn=classify_fn)
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
    return {
        "answer": result.answer,
        "trace_id": result.trace_id,
        "outcome": result.outcome,
        "memory": {"resolved": _event(result.resolved), "offered": _event(result.offered)},
        "guardrail": _guardrail(result.guardrail),
    }


@router.get("/guardrails/summary", response_model=GuardrailSummaryOut)
def guardrails_summary(user: Dict[str, Any] = Depends(get_current_user)) -> Dict[str, Any]:
    """Cuántas veces se activó cada guardarraíl desde que arrancó la API
    (la "sesión de pruebas"), por guardia, tipo de fallo y acción. Solo admin."""
    require_admin(user)
    return MONITOR.summary()


@router.get("/memory", response_model=List[AgentMemoryOut])
def list_my_memory(
    user: Dict[str, Any] = Depends(get_current_user), session: Session = Depends(get_inventory_db)
) -> List[Any]:
    """Lo que el agente recuerda para quien pregunta (hechos compartidos + sus preferencias)."""
    return MemoryStore(session).recall(str(user["id"]))


@router.get("/memory/audit", response_model=List[AgentMemoryAuditOut])
def memory_audit(
    user: Dict[str, Any] = Depends(get_current_user), session: Session = Depends(get_inventory_db)
) -> List[Any]:
    """Registro de auditoría: toda propuesta y su decisión, aprobada o no."""
    require_admin(user)
    return MemoryStore(session).audit()


@router.delete("/memory/{memory_id}", status_code=status.HTTP_204_NO_CONTENT, response_class=Response)
def revoke_memory(
    memory_id: uuid.UUID,
    user: Dict[str, Any] = Depends(get_current_user),
    session: Session = Depends(get_inventory_db),
) -> Response:
    """Retira un recuerdo (queda como `revoked`, no se borra). Solo admin."""
    require_admin(user)
    if MemoryStore(session).revoke(memory_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Memory not found.")
    return Response(status_code=status.HTTP_204_NO_CONTENT)
