"""POST /knowledge/query: pregunta en lenguaje natural → respuesta generada.

Solo HTTP: autenticación, validación y códigos de estado. Recuperación y
generación se importan de data/pipelines/rag.py (`query()`), nunca se
duplican aquí. Handler síncrono a propósito: `query()` hace llamadas de red
bloqueantes y FastAPI lo ejecuta en su threadpool.
"""

from __future__ import annotations

import logging
from typing import Dict

from fastapi import APIRouter, Depends, HTTPException, status

from data.pipelines import rag
from data.process.rag import RagConfigError
from security import get_current_user
from services.knowledge.schemas import KnowledgeQueryRequest, KnowledgeQueryResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/knowledge", tags=["knowledge"], dependencies=[Depends(get_current_user)])


@router.post("/query", response_model=KnowledgeQueryResponse)
def query_knowledge_base(payload: KnowledgeQueryRequest) -> Dict[str, str]:
    """Consulta de los coordinadores de pacientes (CONTEXT del Hito 7)."""
    try:
        answer = rag.query(payload.question)
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
    return {"answer": answer}
