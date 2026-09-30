"""Validador de PHI para la memoria del agente (Ticket #MEM-092, requisito de HealthCore).

Nada que parezca información de un paciente puede entrar en la memoria, ni
siquiera como propuesta pendiente. Este módulo es la única regla de "¿esto
parece PHI?" y se aplica en tres momentos (defensa en capas):

1. Antes de enseñar una propuesta al usuario (texto propuesto + mensaje que
   la originó: una propuesta derivada de un mensaje con PHI está contaminada).
2. Antes de guardar una edición del usuario ("sí, pero pon que ...").
3. En la consolidación, sobre la versión final y sobre todo lo ya guardado.

Reglas deterministas (expresiones regulares), no el modelo: la misma entrada
da siempre el mismo veredicto, es testeable y no depende del proveedor.
Deliberadamente conservador: un falso positivo solo cuesta que el usuario
reformule sin datos de paciente; un falso negativo es un incidente HIPAA.

Cobertura de los dos marcos:
- HIPAA (EE. UU.): los identificadores de la Safe Harbor list que pueden
  aparecer en una conversación de staff (nombres ligados a un paciente,
  fechas de nacimiento, SSN, historia clínica, número de seguro/póliza,
  teléfono, email).
- UK GDPR (R. U.): los mismos datos personales más la categoría especial de
  datos de salud (art. 9): diagnósticos, notas clínicas, resultados, dosis.
  El NHS number (10 dígitos) es el identificador propio del R. U.

Los nombres de personas SIN relación con un paciente no se bloquean: "el
reporte para Diane Foster" (staff) es memorizable según el CONTEXT.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Pattern, Tuple

# Palabras con mayúscula que siguen a "pacientes" sin ser un nombre de
# persona: programas, aseguradoras y las sedes de HealthCore. Sin esta lista,
# "los pacientes Medicare no pagan el no-show" se bloquearía como PHI.
_NOT_A_NAME = (
    r"(?:Medicare|Medicaid|NHS|Bupa|AXA|Aetna|Cigna|Humana|Vitality|Aviva|UnitedHealthcare|Blue"
    r"|Texas|Florida|Georgia|Austin|Houston|Dallas|Miami|Orlando|Tampa|Atlanta|Savannah"
    r"|Londres|London|Manchester|UK|US|EE)\b"
)
_NAME = rf"(?!{_NOT_A_NAME})[A-ZÁÉÍÓÚÑ][a-záéíóúñ'\-]+"

# (categoría, patrón). La categoría es lo único que se guarda en auditoría:
# nunca el fragmento que ha coincidido (sería guardar el propio dato).
_RULES: Tuple[Tuple[str, Pattern[str]], ...] = (
    # "el paciente Johnson", "patient Smith", "la Sra. García", "Mr Jones"
    (
        "patient_name",
        re.compile(
            rf"\b(?:[Pp]acientes?|[Pp]atients?|[Ss]eñor[a]?|[Ss]ra?\.|[Mm]rs?\.?|[Mm]s\.?|[Mm]iss)\s+{_NAME}"
        ),
    ),
    # Identificadores de historia clínica / hospital
    (
        "medical_record_number",
        re.compile(
            r"\b(?:MRN|NHC|historia\s+cl[ií]nica|medical\s+record|hospital\s+number)\b\s*(?:n[º°o.]*|#|:)?\s*\w*\d",
            re.IGNORECASE,
        ),
    ),
    # NHS number (R. U.): 10 dígitos, habitualmente 3-3-4
    ("nhs_number", re.compile(r"\b\d{3}[\s-]?\d{3}[\s-]?\d{4}\b")),
    # SSN (EE. UU.)
    ("ssn", re.compile(r"\b\d{3}-\d{2}-\d{4}\b")),
    # Número de seguro, póliza o afiliado seguido de un código
    (
        "insurance_number",
        re.compile(
            r"\b(?:p[óo]liza|seguro|afiliad[oa]|member|policy|insurance|subscriber)\b[^.\n]{0,20}?"
            r"(?:n[º°o.]*|n[úu]mero|number|id|#|:)\s*[A-Z0-9][A-Z0-9-]{4,}",
            re.IGNORECASE,
        ),
    ),
    # Fecha de nacimiento o edad concreta
    (
        "date_of_birth",
        re.compile(
            r"\b(?:fecha\s+de\s+nacimiento|naci[óo]|nacid[oa]|DOB|date\s+of\s+birth|born\s+on)\b"
            r"|\b\d{1,3}\s+(?:años|years?\s+old)\b",
            re.IGNORECASE,
        ),
    ),
    # Contenido clínico (categoría especial UK GDPR art. 9)
    (
        "clinical_content",
        re.compile(
            r"\b(?:diagn[óo]stic\w*|diagnos\w*|nota\s+cl[ií]nica|notas\s+cl[ií]nicas|clinical\s+notes?"
            r"|resultados?\s+de\s+(?:laboratorio|anal[ií]tica|la\s+prueba)|lab\s+results?|biopsia|biopsy"
            r"|hba1c|glucosa|glucose|colesterol|cholesterol|tensi[óo]n\s+arterial|blood\s+pressure"
            r"|receta\s+de|prescri\w*|s[ií]ntomas?|symptoms?|embaraz\w*|pregnan\w*|VIH|HIV)\b"
            r"|\b\d+(?:[.,]\d+)?\s?(?:mg|ml|mcg|µg)\b",
            re.IGNORECASE,
        ),
    ),
    ("email", re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")),
    # Teléfono: 9+ dígitos con separadores opcionales (no fechas ni horas)
    ("phone", re.compile(r"(?<![\d/:])\+?\d(?:[\s-]?\d){8,}(?![\d/:])")),
    # Código postal del R. U. (dirección = identificador)
    ("uk_postcode", re.compile(r"\b[A-Z]{1,2}\d[A-Z\d]?\s*\d[A-Z]{2}\b")),
)

PHI_CATEGORIES = tuple(category for category, _ in _RULES)


@dataclass(frozen=True)
class PhiVerdict:
    categories: Tuple[str, ...]

    @property
    def contains_phi(self) -> bool:
        return bool(self.categories)


def scan(*texts: str) -> PhiVerdict:
    """Categorías de PHI encontradas en cualquiera de los textos (sin repetir,
    en el orden de las reglas). Un texto vacío o None no aporta nada."""
    found: List[str] = []
    for text in texts:
        if not text:
            continue
        for category, pattern in _RULES:
            if category not in found and pattern.search(text):
                found.append(category)
    ordered = tuple(category for category in PHI_CATEGORIES if category in found)
    return PhiVerdict(categories=ordered)


# Motivo en lenguaje llano para el usuario, por categoría.
_PLAIN_REASONS = {
    "patient_name": "el nombre de un paciente",
    "medical_record_number": "un número de historia clínica",
    "nhs_number": "lo que parece un NHS number u otro identificador numérico",
    "ssn": "un número de la seguridad social",
    "insurance_number": "un número de seguro o póliza",
    "date_of_birth": "una fecha de nacimiento o una edad",
    "clinical_content": "información clínica (diagnóstico, resultados, síntomas o medicación)",
    "email": "una dirección de correo",
    "phone": "un número de teléfono",
    "uk_postcode": "un código postal",
}


def exclusion_notice(verdict: PhiVerdict) -> str:
    """Aviso cuando el mensaje trae PHI aunque nadie haya pedido recordarlo:
    el usuario debe saber que nada de ese mensaje entra en la memoria."""
    reasons = ", ".join(_PLAIN_REASONS[category] for category in verdict.categories)
    return (
        "Nota de privacidad: no guardo en mi memoria nada de este mensaje porque contiene " + reasons + " "
        "(HIPAA en EE. UU. y UK GDPR en R. U.). Si hay que dejar constancia, regístralo en el sistema "
        "clínico correspondiente."
    )


def refusal_message(verdict: PhiVerdict) -> str:
    """Explicación explícita (el CONTEXT exige no ignorarlo en silencio)."""
    reasons = ", ".join(_PLAIN_REASONS[category] for category in verdict.categories)
    return (
        "No puedo guardar esto en mi memoria: contiene " + reasons + ". "
        "Por HIPAA (EE. UU.) y UK GDPR (R. U.), la memoria del asistente nunca guarda datos que "
        "identifiquen a un paciente ni información clínica. Regístralo en el sistema clínico "
        "correspondiente. Si lo que quieres que recuerde es un patrón operativo, escríbemelo sin "
        "datos del paciente."
    )
