"""Guardarraíl de SALIDA (Ticket #SEC-114): valida la respuesta del modelo
antes de devolverla.

El CONTEXT de HealthCore lo exige explícitamente: "no basta con instruir al
modelo a no compartir PHI; se probó reiteradamente que la sola instrucción no
es suficiente". Por eso se comprueba en código, en tres familias:

- Estructural: vacía, demasiado larga, restos del JSON de `memory/reply.py`
  o de los delimitadores del prompt (`<fuente_externa>`...).
- Seguridad: la marca `CANARY` o frases literales del system prompt → el
  modelo está filtrando sus instrucciones internas.
- Contenido: PHI (identificadores directos, o edad/fecha de nacimiento junto
  a información clínica) y cifras de una brecha de seguridad.

Ante cualquier fallo se sustituye la respuesta ENTERA por un texto fijo: no
se recorta el fragmento, porque lo que queda alrededor puede seguir
identificando a la persona o delatar la regla.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

from services.agent.guardrails import patterns
from services.agent.guardrails.input_guard import DIRECT_IDENTIFIERS
from services.agent.guardrails.monitor import BLOCK, CONTENT, SECURITY, STRUCTURAL, GuardEvent
from services.agent.guardrails.prompt import CANARY
from services.agent.memory import phi_guard

MAX_ANSWER_CHARS = 4000

# Frases del system prompt que nunca aparecen en una respuesta legítima.
_PROMPT_FRAGMENTS = (
    "jerarquia de instrucciones",
    "reglas de veracidad (obligatorias)",
    "memoria del asistente (autoevaluacion",
    "datos que nunca revelas",
    "responde solo con un objeto json",
    "fuera de dominio pero permitido",
)
_JSON_RESIDUE = re.compile(r'"(answer|memory_proposal|user_requested_memory)"\s*:')
_DELIMITERS = re.compile(r"</?\s*(fuente_externa|mensaje_usuario)\b", re.IGNORECASE)
_BREACH_FIGURES = re.compile(
    r"\b\d[\d.,]*\s+(registros|expedientes|historiales|pacientes|personas|records|patients)\b"
    r"|\b(descubiert[oa]|detectad[oa]|discovered|detected)\s+(el|on|the)\s+\d"
)

STRUCTURAL_FALLBACK = (
    "No he podido preparar una respuesta válida a tu consulta. Inténtalo de nuevo en unos segundos; "
    "si se repite, formúlala de otra manera."
)
LEAK_FALLBACK = (
    "No puedo compartir mis instrucciones internas. Sigo aquí para las políticas y procedimientos de "
    "HealthCore bajo HIPAA y UK GDPR: ¿en qué te ayudo?"
)
PHI_FALLBACK = (
    "He retenido la respuesta porque incluía datos que podrían identificar a un paciente, y eso no "
    "puedo mostrarlo (HIPAA y UK GDPR). Reformula la consulta sin datos del paciente y te respondo "
    "con la política aplicable."
)
BREACH_FALLBACK = (
    "He retenido la respuesta porque incluía detalles de una brecha de seguridad, que solo gestiona el "
    "equipo de Compliance. Puedo explicarte el procedimiento general de notificación de brechas."
)


@dataclass(frozen=True)
class OutputVerdict:
    answer: str  # la original si pasa; el texto fijo si no
    event: Optional[GuardEvent] = None

    @property
    def blocked(self) -> bool:
        return self.event is not None


def _blocked(guard: str, failure_type: str, reason: str, fallback: str) -> OutputVerdict:
    return OutputVerdict(fallback, GuardEvent(guard, BLOCK, failure_type, reason))


def check_output(answer: Optional[str]) -> OutputVerdict:
    text = (answer or "").strip()
    if not text:
        return _blocked("output_structure", STRUCTURAL, "empty_answer", STRUCTURAL_FALLBACK)
    if len(text) > MAX_ANSWER_CHARS:
        return _blocked("output_structure", STRUCTURAL, "answer_too_long", STRUCTURAL_FALLBACK)

    normalized = patterns.normalize(text)
    # El canario se busca en el texto original: la normalización deshace el
    # leetspeak y "SEC114" dejaría de coincidir.
    if CANARY.lower() in text.lower() or any(fragment in normalized for fragment in _PROMPT_FRAGMENTS):
        return _blocked("output_prompt_leak", SECURITY, "system_prompt_leak", LEAK_FALLBACK)

    if _JSON_RESIDUE.search(text) or _DELIMITERS.search(text):
        return _blocked("output_structure", STRUCTURAL, "raw_structure_in_answer", STRUCTURAL_FALLBACK)

    categories = set(phi_guard.scan(text).categories)
    if categories & DIRECT_IDENTIFIERS:
        return _blocked("output_phi", CONTENT, "direct_identifier", PHI_FALLBACK)
    if "date_of_birth" in categories and "clinical_content" in categories:
        return _blocked("output_phi", CONTENT, "age_with_clinical_content", PHI_FALLBACK)

    if patterns.BREACH_TOPIC.search(normalized) and _BREACH_FIGURES.search(normalized):
        return _blocked("output_breach_details", CONTENT, "breach_figures", BREACH_FALLBACK)

    return OutputVerdict(text)
