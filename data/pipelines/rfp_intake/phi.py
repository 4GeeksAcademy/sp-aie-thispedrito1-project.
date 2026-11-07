"""Detección y redacción de PHI en una RFP, antes de que la lea ningún agente.

El BRIEFING (§4, caso 4) exige que una "RFP" que adjunte datos de un paciente
nunca llegue tal cual a modelos, logs ni UI: se detecta, se redacta y el
ticket se para y se marca para revisión de Compliance (decisión del usuario,
2026-10-19: "redactar y parar", la regla del árbitro `phi-detected`).

Las expresiones regulares son las de `services/agent/memory/phi_guard.py`
(una sola definición de "esto parece PHI" en todo el proyecto). Lo que cambia
es la DECISIÓN: una RFP legítima trae datos de contacto de una empresa (el
email de compras de Meridian), que el validador de la memoria trata como PHI
y aquí no lo son por sí solos. `is_patient_phi` decide qué combinación de
categorías significa "hay un paciente en este documento".

Determinista a propósito, igual que phi_guard: misma entrada, mismo
veredicto, testeable y sin depender del proveedor del modelo.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import FrozenSet, List, Tuple

from services.agent.memory import phi_guard

# Categorías que, en una RFP, identifican a una persona como paciente.
STRONG_IDENTIFIERS: FrozenSet[str] = frozenset(
    {"patient_name", "medical_record_number", "ssn", "nhs_number", "insurance_number", "date_of_birth"}
)
# Contenido clínico: categoría especial UK GDPR, pero un RFP describe
# servicios ("chronic condition management") sin que haya un paciente.
CLINICAL: FrozenSet[str] = frozenset({"clinical_content"})
# Datos de contacto: en una RFP son de la empresa cliente (compras, RR. HH.).
CONTACT: FrozenSet[str] = frozenset({"email", "phone", "uk_postcode"})

# Campos etiquetados de un resumen clínico ("Patient: …", "DOB: …"). Las
# reglas de phi_guard esperan frases de conversación ("el paciente Johnson")
# y no veían "Patient: Daniel …" (lo detectó el PDF del caso 4, no los tests).
# Se añaden aquí y no en phi_guard para no cambiar el veredicto de la memoria.
_LABELED_FIELDS: Tuple[Tuple[str, "re.Pattern[str]"], ...] = (
    ("patient_name", re.compile(r"\b(?:patient(?:\s+name)?|nombre\s+del\s+paciente|paciente)\s*:\s*\S", re.I)),
    ("date_of_birth", re.compile(r"\b(?:DOB|date\s+of\s+birth|fecha\s+de\s+nacimiento)\s*:\s*\S", re.I)),
    ("medical_record_number", re.compile(r"\b(?:MRN|NHS\s+(?:no|number)|historia\s+cl[ií]nica)\s*[:#]\s*\S", re.I)),
    ("clinical_content", re.compile(r"\b(?:diagnosis|diagn[óo]stico|medication|medicaci[óo]n)\s*:\s*\S", re.I)),
)

PARAGRAPH = re.compile(r"[^\n]+(?:\n(?!\n)[^\n]+)*")


def is_patient_phi(categories: FrozenSet[str]) -> bool:
    """¿Las categorías encontradas indican datos de un paciente concreto?

    `categories` es el conjunto de categorías de phi_guard que coincidieron
    en el documento (subconjunto de STRONG_IDENTIFIERS | CLINICAL | CONTACT).
    """
    # Implementado por el asistente (Learn by Doing delegado por el usuario).
    # 1. Un identificador fuerte basta: un nombre ligado a "paciente", una
    #    historia clínica o una fecha de nacimiento ya identifican a alguien.
    if categories & STRONG_IDENTIFIERS:
        return True
    # 2. Clínico + contacto juntos: un diagnóstico al lado de un teléfono o un
    #    email puede ser el resumen de una persona. Conservador a propósito: un
    #    falso positivo cuesta una revisión manual; un falso negativo es un
    #    incidente HIPAA.
    # 3. Clínico solo ("seguimiento de diabetes") o contacto solo (el email de
    #    compras) describen el servicio o a la empresa, no a un paciente.
    return bool(categories & CLINICAL) and bool(categories & CONTACT)


@dataclass(frozen=True)
class RfpPhiVerdict:
    contains_phi: bool
    categories: Tuple[str, ...]  # solo las que justifican la parada; nunca el texto
    redacted_markdown: str


def find_matches(markdown: str) -> List[Tuple[str, int, int]]:
    """Coincidencias de phi_guard + campos etiquetados de resumen clínico."""
    matches = phi_guard.find_matches(markdown)
    for category, pattern in _LABELED_FIELDS:
        matches.extend((category, m.start(), m.end()) for m in pattern.finditer(markdown))
    return matches


def _redact_paragraphs(markdown: str, matches: List[Tuple[str, int, int]]) -> str:
    """Sustituye el PÁRRAFO entero que contiene cada coincidencia. Tachar solo
    lo que casa con una regex deja a la vista el resto del resumen clínico
    ("04/12/1979", "type 2 diabetes"); el bloque completo no."""
    patient_categories = STRONG_IDENTIFIERS | CLINICAL
    hits = [(start, category) for category, start, _ in matches if category in patient_categories]
    pieces: List[str] = []
    cursor = 0
    for paragraph in PARAGRAPH.finditer(markdown):
        found = sorted({c for start, c in hits if paragraph.start() <= start < paragraph.end()})
        if not found:
            continue
        pieces.append(markdown[cursor : paragraph.start()])
        pieces.append(f"[REDACTADO: bloque con datos de paciente ({', '.join(found)})]")
        cursor = paragraph.end()
    pieces.append(markdown[cursor:])
    return "".join(pieces)


def screen(markdown: str) -> RfpPhiVerdict:
    """Veredicto + Markdown redactado. Si hay PHI de paciente se redactan los
    párrafos con identificadores o contenido clínico; los contactos de
    empresa que no estén en esos párrafos se dejan (no son PHI)."""
    matches = find_matches(markdown)
    found = frozenset(category for category, _, _ in matches)
    if not is_patient_phi(found):
        return RfpPhiVerdict(contains_phi=False, categories=(), redacted_markdown=markdown)
    patient_categories = STRONG_IDENTIFIERS | CLINICAL
    ordered = tuple(category for category in phi_guard.PHI_CATEGORIES if category in found & patient_categories)
    return RfpPhiVerdict(contains_phi=True, categories=ordered, redacted_markdown=_redact_paragraphs(markdown, matches))
