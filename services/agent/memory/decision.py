"""Clasificación explícita de la decisión del usuario sobre una propuesta pendiente.

Cuando hay una propuesta de memoria pendiente, el siguiente mensaje del
usuario se evalúa PRIMERO contra ella. No se busca "sí" en el texto ("sí,
pero no lo guardes" contiene "sí"): un clasificador devuelve una etiqueta
estructurada, una confianza y, si aplica, el texto editado y la parte del
mensaje que es otra pregunta. `resolve_decision()` convierte eso en lo que
hará el sistema, con una regla fija: ante la duda, se descarta.

El clasificador es una llamada al modelo con salida JSON validada por
Pydantic. Solo ocurre en los turnos con propuesta pendiente (la mayoría no
la tienen). Si falla, tarda o devuelve algo inválido, la etiqueta es
`unclear` con confianza 0: nunca se asume aprobación por un fallo.
"""

from __future__ import annotations

import json
import logging
from enum import Enum
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, ValidationError

logger = logging.getLogger(__name__)

CLASSIFIER_TIMEOUT_S = 10.0
# Confianza mínima para que una aprobación o edición cuente como tal.
MIN_CONFIDENCE = 0.75

DecisionLabel = Literal["approve", "reject", "edit", "unrelated", "unclear"]

CLASSIFIER_PROMPT = """Eres un clasificador. El asistente de HealthCore preguntó al usuario si \
quería que recordara un dato (la PROPUESTA). Clasifica el MENSAJE del usuario respecto a esa \
propuesta, sin responder a nada más:
- approve: acepta de forma explícita que se recuerde tal cual.
- reject: rechaza que se recuerde.
- edit: acepta, pero cambiando el contenido. En edited_content escribe el texto final completo \
que debe recordarse, ya con el cambio aplicado.
- unrelated: no responde a la propuesta (cambia de tema o hace otra pregunta).
- unclear: parece responder, pero no se puede saber si acepta o rechaza.
remaining_message: si además de decidir el mensaje contiene otra pregunta o petición, cópiala \
literal; si no, null. Con unrelated, remaining_message es el mensaje completo.
confidence: de 0 a 1, lo seguro que estás de la etiqueta.
Responde SOLO con JSON: {"label": "...", "confidence": 0.0, "edited_content": null, \
"remaining_message": null}"""


class DecisionClassification(BaseModel):
    label: DecisionLabel
    confidence: float = Field(ge=0.0, le=1.0)
    edited_content: Optional[str] = Field(default=None, max_length=500)
    remaining_message: Optional[str] = Field(default=None, max_length=2000)


UNCLEAR = DecisionClassification(label="unclear", confidence=0.0)


class Resolution(str, Enum):
    SAVE = "save"  # guardar el texto propuesto
    SAVE_EDITED = "save_edited"  # guardar el texto editado por el usuario
    REJECT = "reject"  # el usuario dijo que no
    DISCARD = "discard"  # no hubo decisión clara: se descarta por defecto


def resolve_decision(classification: DecisionClassification) -> Resolution:
    """Qué hace el sistema con la clasificación. Regla del ticket: nunca se
    asume aprobación por silencio o ambigüedad."""
    # Por debajo del umbral nada cuenta como decisión, tampoco un "no": la
    # auditoría registra "no quedó claro" en vez de atribuirle una negativa.
    if classification.confidence < MIN_CONFIDENCE:
        return Resolution.DISCARD
    if classification.label == "approve":
        return Resolution.SAVE
    if classification.label == "edit":
        edited = (classification.edited_content or "").strip()
        return Resolution.SAVE_EDITED if edited else Resolution.DISCARD
    if classification.label == "reject":
        return Resolution.REJECT
    return Resolution.DISCARD  # unrelated, unclear


def classify_decision(
    proposal_content: str,
    message: str,
    *,
    client: Optional[Any] = None,
    model: Optional[str] = None,
) -> DecisionClassification:
    """Nunca lanza: cualquier fallo es UNCLEAR (y por tanto descarte)."""
    from data.pipelines import rag
    from data.process.rag import get_llm_client

    try:
        llm = client or get_llm_client()
        if hasattr(llm, "with_options"):
            llm = llm.with_options(timeout=CLASSIFIER_TIMEOUT_S, max_retries=0)
        completion = llm.chat.completions.create(
            model=model or rag.get_generation_model(),
            messages=[
                {"role": "system", "content": CLASSIFIER_PROMPT},
                {"role": "user", "content": f"PROPUESTA:\n{proposal_content}\n\nMENSAJE:\n{message}"},
            ],
            temperature=0,
            response_format={"type": "json_object"},
        )
        raw = completion.choices[0].message.content or ""
        return DecisionClassification.model_validate(json.loads(raw))
    except (ValueError, ValidationError) as exc:
        logger.warning("Memory decision classifier returned invalid output: %s", type(exc).__name__)
    except Exception as exc:  # proveedor caído, timeout, configuración ausente
        logger.warning("Memory decision classifier unavailable: %s", type(exc).__name__)
    return UNCLEAR
