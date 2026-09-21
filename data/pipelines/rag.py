"""Recuperación y generación de la base de conocimiento RAG de HealthCore (Hito 7).

- `retrieve()`: embebe la pregunta con el MISMO `embed()` que la indexación,
  pide a Qdrant los k vecinos más cercanos y descarta los que no llegan a
  `min_score`. Puede devolver menos de k resultados, o ninguno.
- `generate_answer()`: arma el prompt con el contexto ya recuperado y llama
  al modelo de GENERACIÓN (`GENERATION_MODEL`), nunca al de embeddings.
- `query()`: la única función que deben llamar los consumidores externos
  (POST /knowledge/query). Es literalmente `retrieve()` + `generate_answer()`,
  separadas para que el agente LangGraph del hito siguiente pueda llamarlas
  como pasos independientes sin recuperar dos veces.

La respuesta siempre la redacta el modelo: ni siquiera sin contexto se
devuelve un texto fijo ni un chunk crudo. En ese caso el prompt le indica
que no hay información suficiente y que no debe inventar nada.

Privacidad (HIPAA / UK GDPR): la pregunta del coordinador nunca se escribe
en los logs, podría contener datos de un paciente. Solo se registran las
fuentes y las puntuaciones recuperadas.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Callable, Dict, List, Optional, Sequence

from data.process.rag import COLLECTION_NAME, RagConfigError, embed, get_llm_client, get_qdrant_client

logger = logging.getLogger(__name__)

DEFAULT_K = 5
# Similitud coseno mínima. Afinada con data/eval/test-queries.json
# (scripts/evaluate_rag_retrieval.py); justificación en docs/rag/rag-design.md.
DEFAULT_MIN_SCORE = 0.38

NO_CONTEXT_MARKER = "(ninguno: ningún fragmento de la base de conocimiento superó el umbral de similitud)"

ASSISTANT_ROLE = """Eres el asistente de los coordinadores de pacientes de HealthCore, una red \
de 12 clínicas en Estados Unidos (Texas, Florida, Georgia) y Reino Unido (Londres y Manchester). \
El coordinador te consulta en el mostrador o al teléfono mientras atiende a un paciente. \
Responde como lo haría el mejor vendedor de servicios de la clínica: claro, cercano y empático, \
con frases cortas que el coordinador pueda repetir al paciente, y siempre en español. \
Escribe en texto plano: sin Markdown (ni asteriscos, ni almohadillas, ni negritas); para \
enumerar, usa líneas que empiecen por "- "."""

GROUNDING_RULES = """Reglas de veracidad (obligatorias):
- Usa EXCLUSIVAMENTE la información del bloque CONTEXTO. No uses conocimiento general sobre \
seguros, clínicas ni regulación.
- Nunca inventes ni redondees coberturas, tarifas, plazos, porcentajes ni nombres: cópialos tal \
como aparecen en el CONTEXTO.
- Si el CONTEXTO está vacío o no responde a la pregunta, dilo explícitamente ("No tengo \
información suficiente en la base de conocimiento para responder a eso") y sugiere consultarlo \
con el responsable correspondiente. No completes la respuesta con suposiciones.
- No pidas ni repitas datos personales o clínicos de pacientes.
- Termina con una línea "Fuente:" que nombre el documento y la sección usados."""

# Reglas de negocio del memo de Priya Nair y de la sección 6 del CONTEXT.
# Una viñeta por regla, dirigida al modelo. Apuntan al CONTEXTO en vez de
# copiar cifras o plazos: si un documento cambia, la regla no lo contradice.
BUSINESS_RULES = """Reglas de negocio de HealthCore:
- Seguros: si preguntan por una aseguradora o cobertura que no aparece en el CONTEXTO, no la \
confirmes ni la descartes; indica que debe verificarse con el equipo de facturación antes de \
comprometer nada con el paciente (menciona a la persona responsable solo si el CONTEXTO la nombra).
- País: si la pregunta sobre seguros, tarifas o cargos no dice si es Estados Unidos o Reino Unido, \
responde separando lo que aplica en cada país según el CONTEXTO.
- Importes: da cada importe en la moneda del país al que corresponde, tal como aparece en el \
CONTEXTO; nunca conviertas ni mezcles monedas.
- Medicare y Medicaid: nunca indiques que a estos pacientes se les cobra un cargo por no-show o por \
cancelación tardía; aplica literalmente lo que dice la política de citas del CONTEXTO.
- Plazos internos: si el CONTEXTO presenta un plazo como objetivo o promedio interno, no lo \
presentes al paciente como un compromiso garantizado."""


def get_generation_model() -> str:
    model = os.getenv("GENERATION_MODEL", "").strip()
    if not model:
        raise RagConfigError("Falta la variable de entorno GENERATION_MODEL (ver services/api/.env.example)")
    if model == os.getenv("EMBEDDING_MODEL", "").strip():
        raise RagConfigError("EMBEDDING_MODEL y GENERATION_MODEL deben ser modelos distintos")
    return model


def get_min_score() -> float:
    """`RAG_MIN_SCORE` del entorno si existe; si no, el valor afinado."""
    raw = os.getenv("RAG_MIN_SCORE", "").strip()
    if not raw:
        return DEFAULT_MIN_SCORE
    try:
        value = float(raw)
    except ValueError as exc:
        raise RagConfigError(f"RAG_MIN_SCORE debe ser un número entre 0 y 1, no {raw!r}") from exc
    if not 0.0 <= value <= 1.0:
        raise RagConfigError(f"RAG_MIN_SCORE debe estar entre 0 y 1, no {value}")
    return value


def retrieve(
    query: str,
    *,
    k: int = DEFAULT_K,
    min_score: float,
    client: Optional[Any] = None,
    embed_fn: Callable[[str], List[float]] = embed,
) -> List[Dict[str, Any]]:
    """Payloads de los chunks más parecidos a `query` que superan `min_score`.

    Devuelve diccionarios planos (payload del CONTEXT + `score`), nunca
    objetos del SDK de Qdrant. El filtro por umbral se hace aquí, a la
    vista, y no con `score_threshold` de Qdrant, para que sea testeable con
    un cliente simulado."""
    if k < 1:
        raise ValueError("k debe ser al menos 1")
    qdrant = client or get_qdrant_client()
    response = qdrant.query_points(
        collection_name=COLLECTION_NAME,
        query=embed_fn(query),
        limit=k,
        with_payload=True,
    )
    results = [
        {**(point.payload or {}), "score": float(point.score)}
        for point in response.points
        if point.score is not None and point.score >= min_score
    ]
    logger.info(
        "RAG retrieve: %d/%d chunks sobre min_score=%.2f %s",
        len(results),
        len(response.points),
        min_score,
        [(r.get("source_document"), r.get("chunk_index"), round(r["score"], 3)) for r in results],
    )
    return results


def format_context(context: Sequence[Dict[str, Any]]) -> str:
    if not context:
        return NO_CONTEXT_MARKER
    return "\n\n".join(
        f"[{i}] Documento: {item.get('source_document')} · Sección: {item.get('section')}\n{item.get('text', '')}"
        for i, item in enumerate(context, start=1)
    )


def build_messages(question: str, context: Sequence[Dict[str, Any]]) -> List[Dict[str, str]]:
    system = "\n\n".join((ASSISTANT_ROLE, GROUNDING_RULES, BUSINESS_RULES))
    user = f"CONTEXTO:\n{format_context(context)}\n\nPREGUNTA DEL COORDINADOR:\n{question.strip()}"
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def generate_answer(
    question: str,
    context: Sequence[Dict[str, Any]],
    *,
    client: Optional[Any] = None,
    model: Optional[str] = None,
) -> str:
    """Redacta la respuesta final con el modelo de generación a partir del
    contexto recuperado. Una respuesta vacía del modelo es un error, nunca
    un string vacío que la UI confundiría con "sin respuesta"."""
    llm = client or get_llm_client()
    completion = llm.chat.completions.create(
        model=model or get_generation_model(),
        messages=build_messages(question, context),
        temperature=0.2,
    )
    answer = (completion.choices[0].message.content or "").strip()
    if not answer:
        raise RuntimeError("El modelo de generación devolvió una respuesta vacía")
    return answer


def query(question: str) -> str:
    """Punto de entrada único: pregunta en lenguaje natural → respuesta generada."""
    if not question or not question.strip():
        raise ValueError("La pregunta no puede estar vacía")
    context = retrieve(question, k=DEFAULT_K, min_score=get_min_score())
    return generate_answer(question, context)
