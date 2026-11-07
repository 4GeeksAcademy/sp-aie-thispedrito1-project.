"""Datos sensibles del cierre (BRIEFING, "Datos sensibles" y "Restricción específica").

Tres tipos que nunca pueden aparecer en logs, prompts almacenados, mensajes
del agente ni respuestas de la API:

1. Cualquier dato de pacientes. La regla es la MISMA que usa la memoria del
   agente (`services.agent.memory.phi_guard`): una sola definición de "esto
   parece un paciente" en todo el proyecto, determinista y testeada.
2. Salarios individuales (la nómina solo se registra agregada por país).
3. Datos bancarios de proveedores (IBAN, cuenta, routing, sort code).

`screen()` se aplica a la descripción de cada registro EN LA INGESTA, antes
de que llegue a la base de datos o al agente. Si encuentra algo, el registro
queda dudoso y su descripción no se guarda: solo su huella SHA-256 y las
CATEGORÍAS encontradas (nunca el fragmento, que sería guardar el propio dato).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Tuple

from services.agent.memory import phi_guard

PATIENT_DATA = "patient_data"
INDIVIDUAL_SALARY = "individual_salary"
BANK_DETAILS = "bank_details"

_BANK_RULES = (
    re.compile(r"\b[A-Z]{2}\d{2}[A-Z]{4}\d{6,}\b"),  # IBAN
    re.compile(r"(?i)\b(?:account(?:\s+(?:number|no\.?))?|routing(?:\s+number)?|aba|sort\s+code|iban|swift|bic)"
               r"\s*[:#]\s*\S+"),
    re.compile(r"\b\d{2}-\d{2}-\d{2}\b"),  # sort code suelto
)
# "salary of John Smith", "bonus for Dr. Ana López", "nómina de Pedro Ruiz".
_PERSON = r"(?:(?:Dr|Dra|Mr|Mrs|Ms)\.?\s+)?[A-ZÁÉÍÓÚÑ][a-záéíóúñ'\-]+\s+[A-ZÁÉÍÓÚÑ][a-záéíóúñ'\-]+"
_SALARY_RULES = (
    # `(?i:...)` solo en las palabras clave: el nombre propio SÍ exige mayúscula.
    re.compile(rf"(?i:\b(?:salary|wage|bonus|pay\s*slip|payslip|salario|n[óo]mina|sueldo)\b)[^\n]{{0,20}}?"
               rf"(?i:\b(?:of|for|to|de|para))\s+{_PERSON}"),
    re.compile(r"(?i)\b(?:employee|empleado|staff)\s*(?:id|n[º°o.]*)\s*[:#]?\s*\w*\d"),
)


@dataclass(frozen=True)
class Screening:
    categories: Tuple[str, ...]
    phi_categories: Tuple[str, ...]  # detalle de phi_guard, solo para auditoría

    @property
    def is_sensitive(self) -> bool:
        return bool(self.categories)

    @property
    def has_patient_data(self) -> bool:
        return PATIENT_DATA in self.categories


def screen(text: str) -> Screening:
    """Categorías sensibles de un texto. Un texto vacío no tiene ninguna."""
    if not text:
        return Screening((), ())
    categories = []
    verdict = phi_guard.scan(text)
    if verdict.contains_phi:
        categories.append(PATIENT_DATA)
    if any(rule.search(text) for rule in _SALARY_RULES):
        categories.append(INDIVIDUAL_SALARY)
    if any(rule.search(text) for rule in _BANK_RULES):
        categories.append(BANK_DETAILS)
    return Screening(tuple(categories), verdict.categories)


def is_clean(text: str) -> bool:
    return not screen(text).is_sensitive
