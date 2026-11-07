"""POST /knowledge/query: pregunta en lenguaje natural → respuesta generada.

Solo HTTP: autenticación, validación y códigos de estado. Recuperación y
generación se importan de data/pipelines/rag.py (`query()`), nunca se
duplican aquí. Handler síncrono a propósito: `query()` hace llamadas de red
bloqueantes y FastAPI lo ejecuta en su threadpool.
"""

from __future__ import annotations

import logging
from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException, status

from data.pipelines import rag
from data.process.rag import RagConfigError
from security import get_current_user
from services.agent.guardrails.input_guard import BLOCK, check_input
from services.agent.guardrails.isolation import isolate_evidence
from services.agent.guardrails.monitor import MONITOR
from services.agent.guardrails.output_guard import check_output
from services.knowledge.schemas import KnowledgeQueryRequest, KnowledgeQueryResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/knowledge", tags=["knowledge"], dependencies=[Depends(get_current_user)])


@router.post("/query", response_model=KnowledgeQueryResponse)
def query_knowledge_base(
    payload: KnowledgeQueryRequest, user: Dict[str, Any] = Depends(get_current_user)
) -> Dict[str, str]:
    """Consulta de los coordinadores de pacientes (CONTEXT del Hito 7).

    Auditoría OWASP (carril agéntico): este endpoint llegaba al modelo sin los
    guardarraíles del Ticket #SEC-114 que sí protegen /agent/query. Ahora usa
    los mismos tres puntos de control, sin tocar data/pipelines/rag.py:
    entrada (un BLOCK nunca llega al RAG ni al modelo), aislamiento de los
    chunks recuperados (inyección indirecta) y salida (PHI, filtraciones).
    Un REDIRECT (small talk) sigue al RAG como antes: aquí no hay agente
    general al que derivarlo, y el prompt del Hito 7 ya reconduce.
    """
    verdict = check_input(payload.question, user_id=user.get("id"))
    if verdict.action == BLOCK:
        MONITOR.record(verdict.event, user_id=user.get("id"))
        return {"answer": verdict.message or ""}
    try:
        context = isolate_evidence(rag.retrieve(payload.question, k=rag.DEFAULT_K, min_score=rag.get_min_score()))
        answer = rag.generate_answer(payload.question, context)
    except RagConfigError as exc:
        # El nombre de la variable que falta sí se loguea (no es sensible);
        # la pregunta nunca, podría contener datos de un paciente.
        logger.error("Knowledge assistant misconfigured: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Knowledge assistant is not configured.",
        ) from exc
    except Exception as exc:  # Qdrant o el proveedor de modelos caídos
        logger.error("Knowledge assistant unavailable: %s", type(exc).__name__)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Knowledge assistant is temporarily unavailable.",
        ) from exc
    output = check_output(answer)
    if output.blocked:
        MONITOR.record(output.event, user_id=user.get("id"))
    return {"answer": output.answer}
