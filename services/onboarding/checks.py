"""Comprobaciones de comprensión: banco fijo, corregido en código.

Decisión del usuario (2026-11-07): las preguntas NO las genera ni corrige un
modelo. Son de opción múltiple, escritas a partir del material oficial
(docs/company-onboarding/checks/<doc_id>.<idioma>.json), con dos juegos por
documento. Así:
- una pregunta nunca sale de fuera del material;
- el 100 % de cumplimiento es una comparación exacta, no la opinión de un modelo;
- "repite la comprobación con preguntas distintas" (BRIEFING) es literal: el
  intento n usa el juego (n-1) % 2, así que dos intentos seguidos nunca
  repiten preguntas.

Todo es puro: el grafo llama a `grade()` y guarda el resultado en su estado.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Tuple

from data.pipelines.onboarding_docs import HireProfile, doc_id_for, load_check_bank, select_document
from services.onboarding.catalog import get_stage

MIN_QUESTIONS = 4
MAX_QUESTIONS = 6


class CheckAnswersError(ValueError):
    """Respuestas que no corresponden a la comprobación abierta."""


def question_set(stage: int, profile: HireProfile, attempt: int) -> Tuple[str, List[Dict[str, Any]]]:
    """(id del juego, preguntas COMPLETAS con la respuesta) para este intento."""
    if attempt < 1:
        raise ValueError("Los intentos empiezan en 1")
    doc_id = doc_id_for(stage, profile)
    bank = load_check_bank(doc_id, profile.language)
    sets = bank["sets"]
    index = (attempt - 1) % len(sets)
    return f"{doc_id}:{profile.language}:{index}", sets[index]


def public_questions(stage: int, profile: HireProfile, attempt: int) -> Dict[str, Any]:
    """Lo que ve la persona: enunciado y opciones, NUNCA la respuesta."""
    set_id, questions = question_set(stage, profile, attempt)
    return {
        "set_id": set_id,
        "attempt": attempt,
        "threshold": get_stage(stage).pass_threshold,
        "questions": [{"id": q["id"], "question": q["question"], "options": list(q["options"])} for q in questions],
    }


@dataclass(frozen=True)
class GradeResult:
    score: float
    passed: bool
    correct: int
    total: int
    failed_question_ids: Tuple[str, ...]
    failed_sections: Tuple[str, ...]

    def as_dict(self) -> Dict[str, Any]:
        return {
            "score": self.score,
            "passed": self.passed,
            "correct": self.correct,
            "total": self.total,
            "failed_question_ids": list(self.failed_question_ids),
            "failed_sections": list(self.failed_sections),
        }


def validate_answers(stage: int, profile: HireProfile, attempt: int, answers: Mapping[str, Any]) -> Dict[str, int]:
    """Las respuestas deben cubrir EXACTAMENTE las preguntas del juego abierto,
    con un índice de opción válido. Sin esto, alguien podría mandar las
    respuestas del juego anterior o dejar preguntas en blanco."""
    _, questions = question_set(stage, profile, attempt)
    expected = {q["id"]: len(q["options"]) for q in questions}
    if set(answers) != set(expected):
        raise CheckAnswersError("Hay que responder exactamente las preguntas de la comprobación abierta.")
    clean: Dict[str, int] = {}
    for question_id, value in answers.items():
        if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value < expected[question_id]:
            raise CheckAnswersError(f"Respuesta no válida para la pregunta {question_id}.")
        clean[question_id] = value
    return clean


def grade(stage: int, profile: HireProfile, attempt: int, answers: Mapping[str, int]) -> GradeResult:
    clean = validate_answers(stage, profile, attempt, answers)
    _, questions = question_set(stage, profile, attempt)
    failed = [q for q in questions if clean[q["id"]] != q["answer"]]
    total = len(questions)
    correct = total - len(failed)
    score = correct / total
    threshold = get_stage(stage).pass_threshold
    assert threshold is not None, "solo las etapas de contenido tienen comprobación"
    sections: List[str] = []
    for q in failed:
        if q["section"] not in sections:
            sections.append(q["section"])
    return GradeResult(
        score=round(score, 4),
        passed=score >= threshold,
        correct=correct,
        total=total,
        failed_question_ids=tuple(q["id"] for q in failed),
        failed_sections=tuple(sections),
    )


def validate_bank(stage: int, profile: HireProfile) -> List[str]:
    """Problemas del banco de una etapa (vacío = válido). Lo usa el test que
    recorre todo el corpus: 4-6 preguntas por juego, al menos dos juegos sin
    preguntas repetidas, respuesta dentro de las opciones y cada pregunta
    apuntando a una sección que existe en el documento."""
    problems: List[str] = []
    document = select_document(stage, profile)
    bank = load_check_bank(document.doc_id, profile.language)
    sets = bank.get("sets", [])
    if len(sets) < 2:
        problems.append(f"{document.doc_id}/{profile.language}: necesita al menos 2 juegos")
    seen_ids: set = set()
    seen_texts: set = set()
    for index, questions in enumerate(sets):
        if not MIN_QUESTIONS <= len(questions) <= MAX_QUESTIONS:
            problems.append(f"{document.doc_id}/{profile.language} juego {index}: {len(questions)} preguntas")
        for q in questions:
            if q["id"] in seen_ids:
                problems.append(f"id repetido {q['id']}")
            if q["question"] in seen_texts:
                problems.append(f"pregunta repetida entre juegos {q['id']}")
            seen_ids.add(q["id"])
            seen_texts.add(q["question"])
            if not 0 <= q["answer"] < len(q["options"]):
                problems.append(f"{q['id']}: respuesta fuera de las opciones")
            if document.section(q["section"]) is None:
                problems.append(f"{q['id']}: la sección {q['section']!r} no existe en {document.doc_id}")
    return problems
