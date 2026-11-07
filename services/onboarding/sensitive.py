"""Datos sensibles de una incorporación (BRIEFING, "Datos sensibles").

Nunca deben aparecer en logs, prompts almacenados, mensajes del agente ni
respuestas de la API:
- documento de identidad o fiscal (SSN, National Insurance Number, pasaporte);
- datos bancarios para nómina;
- salario y condiciones económicas del contrato;
- cualquier detalle de la verificación de antecedentes (solo "completada").

Primera línea de defensa: el sistema no tiene NINGÚN campo donde guardarlos
(la etapa 1 solo recibe tres booleanos y la API rechaza cualquier otro campo
sin repetir su valor). Este módulo es la segunda: el texto libre (dudas,
comentarios de aprobación) se revisa con estas reglas. Las dudas se redactan
antes de guardarse o de llegar al modelo; los comentarios con un dato
sensible se rechazan, porque los ve la persona incorporada y quedan en la
auditoría.

Las reglas son deliberadamente amplias: un falso positivo cuesta reescribir
una frase; un falso negativo es un SSN en un log.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable, List, Pattern, Tuple

CATEGORY_NATIONAL_ID = "national_id"
CATEGORY_BANK = "bank_details"
CATEGORY_SALARY = "salary"
CATEGORY_BACKGROUND_CHECK = "background_check_detail"

PLACEHOLDERS = {
    CATEGORY_NATIONAL_ID: "[ID number removed]",
    CATEGORY_BANK: "[bank details removed]",
    CATEGORY_SALARY: "[pay details removed]",
    CATEGORY_BACKGROUND_CHECK: "[background check detail removed]",
}

_I = re.IGNORECASE

_RULES: Tuple[Tuple[str, Pattern[str]], ...] = (
    # SSN 123-45-6789 / 123 45 6789
    (CATEGORY_NATIONAL_ID, re.compile(r"\b\d{3}[- ]\d{2}[- ]\d{4}\b")),
    # National Insurance Number: AB123456C (con o sin espacios)
    (CATEGORY_NATIONAL_ID, re.compile(r"\b[A-CEGHJ-PR-TW-Z]{2}\s?\d{2}\s?\d{2}\s?\d{2}\s?[A-D]\b", _I)),
    # SSN / NI / pasaporte / DNI / ITIN seguidos de un número
    (
        CATEGORY_NATIONAL_ID,
        re.compile(
            r"\b(?:ssn|social security(?: number)?|national insurance(?: number)?|ni number|nino|passport(?: number| no\.?)?"
            r"|pasaporte|dni|nie|itin|tax id|n[uú]mero fiscal)\b[^\n\d]{0,20}\d(?:[ -]?\d){3,}(?:\s?[A-D]\b)?",
            _I,
        ),
    ),
    # IBAN
    (CATEGORY_BANK, re.compile(r"\b[A-Z]{2}\d{2}(?:\s?[A-Z0-9]{4}){2,7}(?:\s?[A-Z0-9]{1,3})?\b")),
    # Sort code británico 12-34-56
    (CATEGORY_BANK, re.compile(r"\b\d{2}-\d{2}-\d{2}\b")),
    # Cuenta / routing / sort code seguidos de dígitos
    (
        CATEGORY_BANK,
        re.compile(
            r"\b(?:account(?: number| no\.?)?|acct|routing(?: number)?|sort code|bank account|cuenta(?: bancaria)?|n[uú]mero de cuenta)"
            r"\b[^\n\d]{0,15}\d[\d -]{5,}",
            _I,
        ),
    ),
    # Salario: palabra clave + cifra, o importe con moneda y "k"/"per year"
    (
        CATEGORY_SALARY,
        re.compile(
            r"\b(?:salary|compensation|wage|bonus|salario|sueldo|retribuci[oó]n)\b[^\n\d]{0,25}[$£€]?\s?\d[\d.,]*\s?k?\b",
            _I,
        ),
    ),
    (CATEGORY_SALARY, re.compile(r"[$£€]\s?\d[\d.,]*\s?(?:k\b|(?:per|a|/)\s?(?:year|annum|month|hour))", _I)),
    # Verificación de antecedentes + cualquier palabra de resultado o contenido
    (
        CATEGORY_BACKGROUND_CHECK,
        re.compile(
            r"\b(?:background check|background screening|dbs check|dbs certificate|criminal record|verificaci[oó]n de antecedentes|antecedentes penales)\b"
            r"[^\n.]{0,60}\b(?:result|results|outcome|found|flag|flagged|record|conviction|convicted|arrest|offen[cs]e|clear|failed|"
            r"resultado|condena|delito|antecedente|limpio|positivo|negativo)\w*",
            _I,
        ),
    ),
    (
        CATEGORY_BACKGROUND_CHECK,
        re.compile(
            r"\b(?:result|outcome|resultado)s?\b[^\n.]{0,30}\b(?:background check|dbs|verificaci[oó]n de antecedentes)\b",
            _I,
        ),
    ),
    (CATEGORY_BACKGROUND_CHECK, re.compile(r"\b(?:criminal record|conviction|convicted|antecedentes penales|condena)\b", _I)),
)


@dataclass(frozen=True)
class ScanResult:
    categories: Tuple[str, ...]
    redacted: str

    @property
    def found(self) -> bool:
        return bool(self.categories)


def scan(text: str) -> ScanResult:
    """Categorías encontradas y el texto con cada coincidencia sustituida."""
    if not text:
        return ScanResult((), text or "")
    categories: List[str] = []
    redacted = text
    for category, pattern in _RULES:
        if pattern.search(redacted):
            if category not in categories:
                categories.append(category)
            redacted = pattern.sub(PLACEHOLDERS[category], redacted)
    return ScanResult(tuple(categories), redacted)


def redact(text: str) -> str:
    return scan(text).redacted


def contains_sensitive(text: str) -> bool:
    return scan(text).found


def assert_clean(values: Iterable[str]) -> None:
    """Para los tests y para los puntos de salida: revienta si algo sensible
    se iba a escribir. Nunca repite el valor en el mensaje."""
    for value in values:
        result = scan(value)
        if result.found:
            raise ValueError(f"Texto con datos sensibles bloqueado: {list(result.categories)}")
