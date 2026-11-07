"""A quién se deriva una duda que el material oficial no cubre.

BRIEFING: "Las dudas de cumplimiento no cubiertas por el material se derivan
a Claire. El resto de dudas no cubiertas, a Diane." (Diane, en su correo:
"Las dudas sobre cumplimiento que no estén cubiertas van a Claire, no a mí.")

La función recibe la duda YA redactada (sin datos sensibles) y dos pistas:
- `nearest_stage`: etapa de la sección del material más parecida, aunque no
  llegara al umbral (None si el buscador no respondió);
- `current_stage`: etapa en la que está la persona ahora.
"""

from __future__ import annotations

import re
from typing import Optional

from services.onboarding.catalog import COMPLIANCE_STAGE, RECIPIENT_COMPLIANCE, RECIPIENT_VP_PEOPLE

# Términos que delatan una duda de cumplimiento, en inglés y español (sin
# tildes: la comparación se hace sobre el texto en minúsculas y normalizado).
COMPLIANCE_TERMS = (
    "hipaa", "gdpr", "phi", "patient data", "patient information", "health data", "datos de pacientes",
    "datos de salud", "informacion de pacientes", "minimum necessary", "minimo necesario", "breach", "brecha",
    "incident", "incidente", "ico", "baa", "dpa", "business associate", "data processing agreement",
    "privacy", "privacidad", "consent", "consentimiento", "access log", "registro de accesos",
    "subject access", "data protection", "proteccion de datos", "compliance", "cumplimiento",
)


def normalize(text: str) -> str:
    import unicodedata

    decomposed = unicodedata.normalize("NFKD", text.lower())
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


def route_uncovered_question(question: str, *, nearest_stage: Optional[int], current_stage: int) -> str:
    """RECIPIENT_COMPLIANCE (Claire) o RECIPIENT_VP_PEOPLE (Diane)."""
    text = normalize(question)
    # Delegado por el usuario (Learn by Doing, 2026-11-07). Ante la duda, a
    # Claire: una duda de cumplimiento en manos de Diane incumple su
    # instrucción explícita; una duda normal en manos de Claire solo cuesta
    # reenviarla.
    if _COMPLIANCE_PATTERN.search(text):
        return RECIPIENT_COMPLIANCE
    if nearest_stage == COMPLIANCE_STAGE:
        return RECIPIENT_COMPLIANCE
    # La etapa actual solo decide si el buscador no dio pista: estar en la
    # etapa 4 no convierte "¿hay parking?" en una duda de cumplimiento.
    if nearest_stage is None and current_stage == COMPLIANCE_STAGE:
        return RECIPIENT_COMPLIANCE
    return RECIPIENT_VP_PEOPLE


# Límites de palabra: "ico" no debe saltar con "politico" ni "phi" con "graphic".
_COMPLIANCE_PATTERN = re.compile(r"\b(?:" + "|".join(re.escape(term) for term in COMPLIANCE_TERMS) + r")\b")
