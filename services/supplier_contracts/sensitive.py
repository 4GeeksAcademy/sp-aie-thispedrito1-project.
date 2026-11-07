"""Datos sensibles del BRIEFING: qué se enmascara y qué peticiones se rechazan.

Tres tipos (BRIEFING, "Datos sensibles"):
1. Datos bancarios de los proveedores (cláusula de facturación).
2. Datos personales de sus personas de contacto.
3. Cualquier dato de pacientes: el sistema trabaja solo con agregados, y una
   petición de uso POR PACIENTE se rechaza.

La primera defensa es estructural: la ingesta nunca cita la cláusula de
facturación (`ingestion.EXCLUDED_CLAUSE_HEADINGS`). `redact()` es la segunda:
se aplica a todo texto de contrato que se guarda, a todo prompt y a todo
borrador antes de que salga. Si ambas fallaran, los tests de
`test_sensitive_data.py` buscan los valores ficticios del catálogo en base de
datos, logs, prompts y respuestas.
"""

from __future__ import annotations

import re
from typing import List

MASK = "[REDACTED]"

_PATTERNS = [
    # IBAN (GB29TMBK40116231926819) — antes que las cuentas para no partirlo.
    re.compile(r"\b[A-Z]{2}\d{2}[A-Z]{4}\d{6,}\b"),
    # Email
    re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"),
    # Teléfono internacional: +1 512 555 0142 / +44 20 7946 0331
    re.compile(r"\+\d{1,3}(?:[\s.-]?\(?\d{2,4}\)?){2,5}"),
    # Datos bancarios etiquetados: "Account number: 4410-2297-1186", "Routing number: 111904215",
    # "Sort code: 40-11-62", "Bank: ..."
    re.compile(r"(?i)\b(account(?:\s+(?:number|no\.?))?|routing(?:\s+number)?|aba|sort\s+code|iban|bank(?:\s+name)?)\s*[:#]\s*[^\n;,]+"),
    # Sort code suelto (40-11-62)
    re.compile(r"\b\d{2}-\d{2}-\d{2}\b"),
    # Persona de contacto etiquetada: "Contact person: Rebecca Tolliver", "Attn: ..."
    re.compile(r"(?i)\b(contact(?:\s+person)?|attn\.?|attention)\s*:\s*[^\n;,]+"),
]


def redact(text: str) -> str:
    """Enmascara datos bancarios y de contacto. Idempotente."""
    if not text:
        return text
    out = text
    for pattern in _PATTERNS:
        out = pattern.sub(MASK, out)
    return out


def contains_sensitive(text: str) -> bool:
    return bool(text) and redact(text) != text


# --- Peticiones de datos por paciente ------------------------------------------

_PATIENT_LEVEL = [
    r"\bpor\s+paciente\b",
    r"\bcada\s+paciente\b",
    r"\bde\s+(?:un|el|la|este|ese|aquel)\s+paciente\b",
    r"\b(?:qu[eé]|cu[aá]les)\s+pacientes\b",
    r"\blista(?:do)?\s+de\s+(?:los\s+)?pacientes\b",
    r"\bnombres?\s+(?:de\s+)?(?:los\s+|del\s+)?pacientes?\b",
    r"\bpaciente\s+[A-ZÁÉÍÓÚÑ][a-záéíóúñ]+",  # "paciente Johnson"
    r"\bper[-\s]patient\b",
    r"\bpatient[-\s]level\b",
    r"\b(?:which|what)\s+patients\b",
    r"\bpatient\s+(?:names?|ids?|records?|list)\b",
    r"\bfor\s+(?:each|every|a|this)\s+patient\b",
    r"\b(?:mrn|nhs\s+number|historia\s+cl[ií]nica)\b",
]
_PATIENT_RE = [re.compile(p, re.IGNORECASE) for p in _PATIENT_LEVEL]
# "paciente Johnson" exige mayúscula: ese patrón no puede ir en IGNORECASE.
_PATIENT_RE[6] = re.compile(_PATIENT_LEVEL[6])


def patient_level_reasons(text: str) -> List[str]:
    return [p.pattern for p in _PATIENT_RE if p.search(text or "")]


def is_patient_level_request(text: str) -> bool:
    """True si la petición pide datos de uso por paciente.

    "¿Qué proveedores procesan datos de pacientes sin acuerdo?" NO lo es: habla
    de proveedores. "¿Cuántas pruebas hizo ClearPath por paciente?" sí.
    """
    return bool(patient_level_reasons(text))


PATIENT_REQUEST_REFUSAL = (
    "No puedo responder a esa petición: este sistema trabaja solo con precios y volúmenes "
    "agregados de proveedores y nunca con datos de pacientes ni de uso por paciente. "
    "Si necesitas ese dato, pídelo por el canal de Cumplimiento (Claire Whitfield)."
)
