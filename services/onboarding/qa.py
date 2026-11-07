"""Dudas de la persona incorporada: material oficial o derivación a una persona.

README: "Las respuestas salen del material oficial o no salen". Flujo:
1. La duda se redacta (sensitive.scan) ANTES de todo: el texto original no se
   guarda, no se loguea y no llega al modelo.
2. Se buscan secciones del material de ESTA persona (idioma, marco, puesto y
   departamento) en `healthcore_onboarding`.
3. Ninguna supera el umbral → no se llama al modelo: respuesta fija + aviso a
   quien corresponda (`routing.route_uncovered_question`).
4. Alguna lo supera → el modelo redacta SOLO con esos extractos y responde con
   el marcador NOT_COVERED si no bastan (→ se deriva igual). Las citas las
   pone el código a partir de los extractos que el modelo referenció, no las
   que el modelo diga.
5. Fallo del buscador o del modelo → se deriva (nunca una respuesta inventada).

En el estado del grafo no entra ninguna duda: responder no avanza etapas y no
necesita checkpoint. Lo que se guarda es la fila `onboarding_questions` (texto
ya redactado) y la auditoría con `actor_type=agent`.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Callable, Dict, List, Optional, Sequence

from sqlmodel import Session

from data.pipelines import onboarding_docs
from services.onboarding import repository, routing, sensitive
from services.onboarding.catalog import ACTOR_AGENT, DEPARTMENTS, FRAMEWORKS, ROLE_VARIANTS
from services.onboarding.models import QUESTION_ANSWERED, QUESTION_ROUTED, OnboardingHire, OnboardingQuestion
from services.onboarding.permissions import CAP_ANSWER_FROM_OFFICIAL_MATERIAL, require_agent_capability
from services.onboarding.runner import profile_of

logger = logging.getLogger("healthcore.onboarding.qa")

MAX_QUESTION_LENGTH = 1000
NOT_COVERED_MARKER = "NOT_COVERED"

REASON_NO_MATCH = "not_in_official_material"
REASON_MODEL_NOT_COVERED = "model_found_material_insufficient"
REASON_UNAVAILABLE = "assistant_unavailable"

ROUTED_ANSWERS = {
    "en": "This is not covered by HealthCore's official onboarding material, so I will not guess. I have passed your question to {who}, who will get back to you.",
    "es": "El material oficial de incorporación de HealthCore no cubre esta duda, así que no voy a suponer una respuesta. Se la he pasado a {who}, que te responderá.",
}
ROUTED_TO_NAMES = {
    routing.RECIPIENT_COMPLIANCE: {"en": "Claire Whitfield (Compliance)", "es": "Claire Whitfield (Cumplimiento)"},
    routing.RECIPIENT_VP_PEOPLE: {"en": "Diane Foster (People)", "es": "Diane Foster (Personas)"},
}

SYSTEM_PROMPT = """You are HealthCore's onboarding assistant for new corporate (non-clinical) staff.

Rules (mandatory):
1. Answer ONLY with information that appears in the EXCERPTS. Never use outside knowledge, never guess, never add policy that is not written there.
2. Cite every statement with the excerpt number in square brackets, e.g. [1].
3. If the excerpts do not answer the question completely, reply with exactly NOT_COVERED and nothing else.
4. Answer in {language_name}. Plain text, at most 120 words, no Markdown.
5. Never ask for or repeat personal data (ID numbers, bank details, salary, background check details)."""


class QuestionError(ValueError):
    pass


RetrieveFn = Callable[..., List[Dict[str, Any]]]


def _excerpts(context: Sequence[Dict[str, Any]]) -> str:
    return "\n\n".join(f"[{i}] {item['citation']}\n{item['text']}" for i, item in enumerate(context, start=1))


def build_messages(question: str, context: Sequence[Dict[str, Any]], hire: OnboardingHire) -> List[Dict[str, str]]:
    """Prompt sin nombre ni datos de la persona: solo puesto, departamento y marco."""
    language_name = "Spanish" if hire.language == "es" else "English"
    role = ROLE_VARIANTS[hire.role_variant].title_en
    user = (
        f"NEW HIRE ROLE: {role} · Department: {DEPARTMENTS[hire.department]} · Framework: {FRAMEWORKS[hire.framework]}\n\n"
        f"EXCERPTS:\n{_excerpts(context)}\n\nQUESTION:\n{question}"
    )
    return [{"role": "system", "content": SYSTEM_PROMPT.format(language_name=language_name)}, {"role": "user", "content": user}]


def _default_llm_call(messages: List[Dict[str, str]]) -> str:
    from data.pipelines.rag import get_generation_model
    from data.process.rag import get_llm_client

    completion = get_llm_client().chat.completions.create(model=get_generation_model(), messages=messages, temperature=0.0)
    return (completion.choices[0].message.content or "").strip()


def _referenced(answer: str, context: Sequence[Dict[str, Any]]) -> List[str]:
    numbers = {int(n) for n in re.findall(r"\[(\d+)\]", answer)}
    cited = [item["citation"] for i, item in enumerate(context, start=1) if i in numbers]
    return cited or [item["citation"] for item in context]


def answer_question(
    session: Session,
    hire: OnboardingHire,
    question: str,
    *,
    retrieve_fn: Optional[RetrieveFn] = None,
    llm_call: Optional[Callable[[List[Dict[str, str]]], str]] = None,
    min_score: float = onboarding_docs.DEFAULT_MIN_SCORE,
) -> OnboardingQuestion:
    require_agent_capability(CAP_ANSWER_FROM_OFFICIAL_MATERIAL)
    if not isinstance(question, str) or not question.strip():
        raise QuestionError("Escribe una duda.")
    if len(question) > MAX_QUESTION_LENGTH:
        raise QuestionError(f"Máximo {MAX_QUESTION_LENGTH} caracteres.")
    scan = sensitive.scan(question.strip())
    text = scan.redacted
    profile = profile_of(hire)
    retrieve_fn = retrieve_fn or onboarding_docs.retrieve
    llm_call = llm_call or _default_llm_call

    hits: List[Dict[str, Any]] = []
    reason: Optional[str] = None
    try:
        hits = retrieve_fn(text, profile, min_score=-1.0)
    except Exception as exc:  # noqa: BLE001 — Qdrant caído: se deriva, no se inventa
        logger.error("onboarding_qa_retrieve_failed onboarding_id=%s error_type=%s", hire.hire_id, type(exc).__name__)
        reason = REASON_UNAVAILABLE
    covered = [hit for hit in hits if hit.get("score", 0.0) >= min_score]

    answer: Optional[str] = None
    citations: List[str] = []
    if covered and reason is None:
        try:
            output = llm_call(build_messages(text, covered, hire))
        except Exception as exc:  # noqa: BLE001
            logger.error("onboarding_qa_model_failed onboarding_id=%s error_type=%s", hire.hire_id, type(exc).__name__)
            output, reason = "", REASON_UNAVAILABLE
        if output and NOT_COVERED_MARKER not in output:
            answer = sensitive.redact(output)
            citations = _referenced(output, covered)
        elif reason is None:
            reason = REASON_MODEL_NOT_COVERED
    elif reason is None:
        reason = REASON_NO_MATCH

    nearest = int(hits[0]["stage"]) if hits else None
    if answer is not None:
        record = OnboardingQuestion(hire_id=hire.hire_id, stage=hire.current_stage, question=text, status=QUESTION_ANSWERED, answer=answer, citations=citations)
        action, outcome = "question_answered", ",".join(citations)[:64]
    else:
        routed_to = routing.route_uncovered_question(text, nearest_stage=nearest, current_stage=hire.current_stage)
        language = hire.language if hire.language in ROUTED_ANSWERS else "en"
        message = ROUTED_ANSWERS[language].format(who=ROUTED_TO_NAMES[routed_to][language])
        record = OnboardingQuestion(
            hire_id=hire.hire_id, stage=hire.current_stage, question=text, status=QUESTION_ROUTED,
            answer=message, citations=[], routed_to=routed_to, reason=reason,
        )
        action, outcome = "question_routed", routed_to
    session.add(record)
    session.flush()
    if record.status == QUESTION_ROUTED:
        repository.record_notification(
            session, hire, key=f"{hire.hire_id}:question:{record.id}", stage=hire.current_stage,
            kind="question_routed", recipient_role=record.routed_to, params={"question": text},
        )
    repository.record_audit(
        session, event_key=f"{hire.hire_id}:question:{record.id}", hire_id=hire.hire_id, stage=hire.current_stage,
        action=action, actor_type=ACTOR_AGENT, outcome=outcome or None,
        security_event=False,
    )
    if scan.found:
        repository.record_audit(
            session, event_key=f"{hire.hire_id}:question:{record.id}:redacted", hire_id=hire.hire_id, stage=hire.current_stage,
            action="sensitive_data_redacted", actor_type=ACTOR_AGENT, outcome=",".join(scan.categories),
        )
    session.commit()
    session.refresh(record)
    logger.info("onboarding_question onboarding_id=%s status=%s hits=%d covered=%d", hire.hire_id, record.status, len(hits), len(covered))
    return record
