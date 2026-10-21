"""Validación de las respuestas humanas de la Parte 3, ANTES de reanudar.

README: "Valida la respuesta humana al reanudar (aprobar, rechazar o
solicitar cambios) antes de dejarla entrar de nuevo al grafo". Esta es esa
puerta. La API la usa para responder 400 al momento (con errores por campo)
y el runner la repite justo antes del `Command(resume=...)`, porque entre
una cosa y otra la petición del grafo pudo cambiar (otra ronda, otro
arbitraje).

Se valida contra la PETICIÓN que hizo el grafo (`approval_request`, payload
del interrupt), no solo contra el esquema: "solicitar cambios" solo existe
mientras queden rondas, y la capacidad confirmada solo la pide Clinical.

Los comentarios llegan al generador de la Parte 2 como feedback y se guardan
en la sección: pasan por el mismo detector de PHI que la RFP. Con PHI se
rechazan enteros (no se redactan): quien escribe debe saber que no puede.
"""

from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ValidationError

from data.pipelines.rfp_intake.models import DECISION_APPROVE, DECISION_REJECT, DECISION_REQUEST_CHANGES
from data.pipelines.rfp_intake.phi import find_matches, is_patient_phi

MAX_COMMENT_CHARS = 2000
MIN_COMMENT_CHARS = 10
MAX_CAPACITY = 1_000_000

ARBITRATION_REDUCE_POPULATION = "reduce_population"
ARBITRATION_ADD_SITES = "add_sites"


class DecisionError(ValueError):
    """Respuesta humana no válida. `errors` = [{field, message}], el formato
    que el backoffice ya pinta por campo (ApiFieldError)."""

    def __init__(self, errors: List[Dict[str, str]]) -> None:
        super().__init__("; ".join(f"{e['field']}: {e['message']}" for e in errors))
        self.errors = errors


class SectionDecisionIn(BaseModel):
    decision: Literal["approve", "request_changes", "reject"]
    comments: Optional[str] = None
    confirmed_capacity: Optional[int] = None


class ArbitrationDecisionIn(BaseModel):
    option: Literal["reduce_population", "add_sites"]
    new_population: Optional[int] = None
    note: Optional[str] = None


def _schema_errors(exc: ValidationError) -> List[Dict[str, str]]:
    return [
        {"field": ".".join(str(p) for p in error["loc"]) or "body", "message": "Valor no válido."}
        for error in exc.errors()
    ]


def contains_patient_data(text: str) -> bool:
    return is_patient_phi(frozenset(category for category, _, _ in find_matches(text)))


def _check_text(value: Optional[str], *, field: str, required: bool, errors: List[Dict[str, str]]) -> Optional[str]:
    text = (value or "").strip()
    if not text:
        if required:
            errors.append({"field": field, "message": "Explica el motivo: el generador y el resto de departamentos lo necesitan."})
        return None
    if required and len(text) < MIN_COMMENT_CHARS:
        errors.append({"field": field, "message": f"Escribe al menos {MIN_COMMENT_CHARS} caracteres."})
    if len(text) > MAX_COMMENT_CHARS:
        errors.append({"field": field, "message": f"Máximo {MAX_COMMENT_CHARS} caracteres."})
    if contains_patient_data(text):
        errors.append({
            "field": field,
            "message": "El texto parece contener datos de un paciente (nombre, fecha de nacimiento, historia clínica...). "
                       "Quítalos: no pueden aparecer en la propuesta ni en el ticket.",
        })
    return text


def validate_section_decision(raw: Dict[str, Any], request: Dict[str, Any]) -> Dict[str, Any]:
    """Decisión de un departamento → dict normalizado, o `DecisionError`."""
    try:
        decision = SectionDecisionIn.model_validate(raw)
    except ValidationError as exc:
        raise DecisionError(_schema_errors(exc)) from exc

    errors: List[Dict[str, str]] = []
    needs_reason = decision.decision in (DECISION_REQUEST_CHANGES, DECISION_REJECT)
    comments = _check_text(decision.comments, field="comments", required=needs_reason, errors=errors)

    if decision.decision == DECISION_REQUEST_CHANGES and not request.get("can_request_changes", False):
        errors.append({
            "field": "decision",
            "message": f"Ya se usaron las {request.get('max_revision_rounds')} rondas de cambios de esta sección: "
                       "apruébala tal cual o recházala.",
        })

    capacity = decision.confirmed_capacity
    if capacity is not None and not request.get("asks_capacity", False):
        errors.append({"field": "confirmed_capacity", "message": "Solo Clinical Operations confirma capacidad."})
    elif request.get("asks_capacity") and decision.decision == DECISION_APPROVE:
        if capacity is None:
            errors.append({
                "field": "confirmed_capacity",
                "message": "Indica cuántas personas pueden atender las sedes y el personal asignados.",
            })
        elif not 0 < capacity <= MAX_CAPACITY:
            errors.append({"field": "confirmed_capacity", "message": "Debe ser un número entre 1 y 1.000.000."})

    if errors:
        raise DecisionError(errors)
    return {
        "decision": decision.decision,
        "comments": comments,
        "confirmed_capacity": capacity if decision.decision == DECISION_APPROVE else None,
    }


def validate_arbitration_decision(raw: Dict[str, Any], request: Dict[str, Any]) -> Dict[str, Any]:
    """Decisión del árbitro humano (Tom Callahan, `capacity-vs-population`)."""
    try:
        decision = ArbitrationDecisionIn.model_validate(raw)
    except ValidationError as exc:
        raise DecisionError(_schema_errors(exc)) from exc

    errors: List[Dict[str, str]] = []
    if decision.option not in request.get("options", []):
        errors.append({"field": "option", "message": "Esa opción no está disponible para este conflicto."})
    new_population = None
    if decision.option == ARBITRATION_REDUCE_POPULATION:
        capacity = request.get("confirmed_capacity") or 0
        new_population = decision.new_population
        if new_population is None or not 0 < new_population <= capacity:
            errors.append({
                "field": "new_population",
                "message": f"La nueva población debe estar entre 1 y la capacidad confirmada ({capacity}).",
            })
    note = _check_text(
        decision.note, field="note", required=decision.option == ARBITRATION_ADD_SITES, errors=errors
    )
    if errors:
        raise DecisionError(errors)
    return {"option": decision.option, "new_population": new_population, "note": note}
