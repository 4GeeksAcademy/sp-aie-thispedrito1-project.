"""Respuestas "fuera de dominio pero permitidas" (Ticket #SEC-114, criterio 1).

Dos casos del CONTEXT §2:
- `casual`: small talk o cultura general ("¿qué hora es en Tokio?").
- `regulation`: regulación sanitaria a nivel de industria que la base de
  conocimiento no cubre (p. ej. plazos de notificación de brechas).

El modelo redacta una respuesta BREVE con un prompt propio (misma jerarquía
de instrucciones que el agente). La reconducción a HealthCore NO depende del
modelo: la añade este módulo siempre, después de pasar la respuesta por
`output_guard`. Si el proveedor falla, se responde solo con la reconducción:
una charla casual no puede dar un 503.

No pasa por el grafo: no hay nada que recuperar ni que consultar en vivo, y
así una pregunta de trivia nunca propone memoria.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Optional

from services.agent.guardrails.isolation import render_user_message
from services.agent.guardrails.monitor import GuardEvent
from services.agent.guardrails.output_guard import check_output
from services.agent.guardrails.prompt import AGENT_ROLE, INSTRUCTION_HIERARCHY

logger = logging.getLogger(__name__)

CASUAL = "casual"
REGULATION = "regulation"

REDIRECTS = {
    CASUAL: (
        "Y volviendo a lo mío: estoy para las políticas y procedimientos de HealthCore (HIPAA, UK GDPR, "
        "citas, seguros, pacientes nuevos, referencias). ¿Te ayudo con algo de eso?"
    ),
    REGULATION: (
        "Esto es el marco general de la regulación, no la política interna de HealthCore: para saber "
        "cómo se aplica en HealthCore, consulta la política interna correspondiente con el equipo de "
        "Compliance de Claire Whitfield antes de actuar."
    ),
}

_MODE_RULES = {
    CASUAL: (
        "El empleado hace small talk o una pregunta de cultura general, fuera de tu dominio. Contesta en "
        "UNA frase corta y amable. Si la respuesta depende de datos que no tienes (hora actual, tiempo, "
        "resultados recientes), dilo en esa frase sin inventar. No añadas nada más ni menciones "
        "al sistema ni a HealthCore: el cierre lo pone otra parte."
    ),
    REGULATION: (
        "El empleado pregunta por regulación sanitaria a nivel de industria y la base de conocimiento de "
        "HealthCore no tiene esa política interna. Da en 2-3 frases el marco general (HIPAA en EE. UU., "
        "UK GDPR en R. U.) si lo conoces con seguridad; si no, dilo. No afirmes nada sobre cómo lo aplica "
        "HealthCore ni sobre brechas concretas. Sin datos de pacientes. No añadas cierre "
        "ni menciones a Compliance ni al sistema: la derivación la pone otra parte."
    ),
}


@dataclass(frozen=True)
class GeneralAnswer:
    answer: str
    output_event: Optional[GuardEvent] = None  # la guardia de salida retuvo el borrador


def answer_general(
    question: str,
    mode: str,
    *,
    client: Optional[Any] = None,
    model: Optional[str] = None,
) -> GeneralAnswer:
    """Respuesta breve + reconducción fija. Nunca lanza."""
    redirect = REDIRECTS[mode]
    try:
        from data.pipelines import rag
        from data.process.rag import get_llm_client

        llm = client or get_llm_client()
        completion = llm.chat.completions.create(
            model=model or rag.get_generation_model(),
            messages=[
                {"role": "system", "content": "\n\n".join((INSTRUCTION_HIERARCHY, AGENT_ROLE, _MODE_RULES[mode]))},
                {"role": "user", "content": render_user_message(question)},
            ],
            temperature=0.2,
            # Margen amplio: deepseek razona antes de contestar y con 300 tokens
            # devolvió respuestas vacías en la prueba real (las retuvo output_guard).
            max_tokens=1200,
        )
        draft = completion.choices[0].message.content or ""
    except Exception as exc:  # proveedor caído, sin clave, timeout...
        logger.warning("General answer unavailable (%s): answering with the redirect only", type(exc).__name__)
        return GeneralAnswer(redirect)
    verdict = check_output(draft)
    if verdict.blocked:
        return GeneralAnswer(redirect, verdict.event)
    return GeneralAnswer(f"{verdict.answer}\n\n{redirect}")
