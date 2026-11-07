"""Endpoints del orquestador de incorporaciones (`/onboarding`).

Solo HTTP: validar, guardar la entrada humana y encolar. El grafo corre en el
worker de Celery (`onboarding.advance_hire`). Excepción documentada: las
dudas (`POST /onboarding/me/questions`) se responden en la propia petición,
porque no avanzan el grafo (no hay estado que persistir) y la persona espera
la respuesta; llevan el mismo límite de uso que el resto de canales con modelo.

Quién ve qué (permissions.py):
- Personas, Diane y Cumplimiento: todas las incorporaciones (vista de equipo).
- Un responsable directo: solo las de su equipo.
- La persona incorporada: solo la suya (`/onboarding/me`).
"""

from __future__ import annotations

import logging
import uuid
from datetime import date
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Body, Depends, HTTPException, status
from fastapi.responses import JSONResponse
from kombu.exceptions import OperationalError
from redis.exceptions import RedisError
from sqlmodel import Session

from data.pipelines import onboarding_docs
from database import get_inventory_db
from rate_limit import enforce_llm_rate_limit
from security import get_current_user
from services.onboarding import access, approvals, checks, followup, qa, repository, runner
from services.onboarding.catalog import (
    ACTOR_HUMAN,
    DEPARTMENTS,
    FRAMEWORKS,
    LANGUAGES,
    ORG_ROLE_COMPLIANCE,
    ORG_ROLE_LINE_MANAGER,
    ORG_ROLE_PEOPLE_TEAM,
    ORG_ROLE_VP_PEOPLE,
    ROLE_VARIANTS,
    SITES,
    SPANISH_ALLOWED_FRAMEWORKS,
    STAGES,
    SYSTEMS,
    get_stage,
)
from services.onboarding.graph import PAPERWORK_FIELDS, WAIT_APPROVAL, WAIT_CHECK, WAIT_PAPERWORK
from services.onboarding.models import PHASE_STARTING, OnboardingHire
from services.onboarding.permissions import CAP_CREATE_ONBOARDING, CAP_VIEW_ALL_ONBOARDINGS, CAP_VIEW_AUDIT, HUMAN_CAPABILITIES
from services.onboarding.schemas import (
    AccessRequestRead,
    AccessRequestResult,
    AuditEventRead,
    CatalogRead,
    CheckRead,
    ContentRead,
    MyOnboarding,
    NotificationRead,
    OnboardingCreated,
    OnboardingDetail,
    OnboardingListItem,
    OnboardingRole,
    QuestionRead,
    QueuedResponse,
    StageRead,
    SystemRead,
    WaitingForRead,
)

logger = logging.getLogger("healthcore.onboarding.api")

router = APIRouter(prefix="/onboarding", tags=["onboarding"], dependencies=[Depends(get_current_user)])

HIRE_FIELDS = (
    "hire_user_id", "full_name", "job_title", "department", "role_variant", "site",
    "framework", "language", "manager_user_id", "start_date",
)


def _bad_request(field: str, message: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail={"errors": [{"field": field, "message": message}]})


def _reject_unknown(payload: Any, allowed: tuple) -> Dict[str, Any]:
    """Un campo fuera de lista se rechaza nombrándolo, SIN repetir su valor."""
    if not isinstance(payload, dict):
        raise _bad_request("body", "El cuerpo debe ser un objeto JSON.")
    unknown = sorted(set(payload) - set(allowed))
    if unknown:
        raise _bad_request(unknown[0], "Campo no permitido: el onboarding no guarda este dato.")
    return payload


def _enqueue(hire_id: uuid.UUID) -> None:
    """Encola el avance. Sustituible en tests para ejecutar en línea."""
    from services.tasks.onboarding_tasks import enqueue_onboarding_advance

    try:
        enqueue_onboarding_advance(hire_id)
    except (RedisError, OperationalError, OSError) as exc:
        logger.error("onboarding_enqueue_failed onboarding_id=%s error_type=%s", hire_id, type(exc).__name__)
        raise HTTPException(
            status_code=503,
            detail="La acción quedó guardada, pero la cola no responde: se aplicará cuando vuelva.",
        ) from exc


# --- Quién llama ------------------------------------------------------------


def _capabilities(session: Session, user: Dict[str, Any]) -> set:
    staff = repository.get_staff(session, user.get("id"))
    caps: set = set(HUMAN_CAPABILITIES[staff.org_role]) if staff else set()
    return caps


def _get_hire_or_404(session: Session, hire_id: uuid.UUID) -> OnboardingHire:
    hire = repository.get_hire(session, hire_id)
    if hire is None:
        raise HTTPException(status_code=404, detail="Incorporación no encontrada.")
    return hire


def _ensure_can_view(session: Session, hire: OnboardingHire, user: Dict[str, Any]) -> None:
    if user.get("id") in (hire.hire_user_id, hire.manager_user_id):
        return
    if CAP_VIEW_ALL_ONBOARDINGS in _capabilities(session, user):
        return
    raise HTTPException(status_code=403, detail="No puedes ver esta incorporación.")


def _my_hire(session: Session, user: Dict[str, Any]) -> OnboardingHire:
    hire = repository.get_hire_by_user(session, user.get("id"))
    if hire is None:
        raise HTTPException(status_code=404, detail="Tu cuenta no tiene una incorporación en curso.")
    return hire


# --- Construcción de respuestas ------------------------------------------------


def _stages(hire: OnboardingHire) -> List[StageRead]:
    items = followup.timeline(hire)
    return [
        StageRead(
            number=item.number,
            key=get_stage(item.number).key,
            title=get_stage(item.number).title("es"),
            deadline_day=get_stage(item.number).deadline_day,
            due_at=item.due_at,
            status=item.status,
            approver=get_stage(item.number).approver,
            pass_threshold=get_stage(item.number).pass_threshold,
        )
        for item in items
    ]


def _list_item(hire: OnboardingHire) -> OnboardingListItem:
    reasons = followup.blocked_reasons(hire)
    deadlines = followup.deadlines_for(hire)
    return OnboardingListItem(
        hire_id=hire.hire_id,
        full_name=hire.full_name,
        job_title=hire.job_title,
        department=hire.department,
        site=hire.site,
        framework=hire.framework,
        language=hire.language,
        start_date=hire.start_date,
        status=hire.status,
        current_stage=hire.current_stage,
        current_stage_title=get_stage(hire.current_stage).title("es"),
        phase=hire.phase,
        current_due_at=deadlines.get(hire.current_stage) if hire.status == "active" else None,
        blocked=bool(reasons),
        blocked_reasons=reasons,
        completed_stages=list(hire.completed_stages or []),
    )


def _waiting(hire: OnboardingHire) -> Optional[WaitingForRead]:
    waiting = hire.waiting_for or {}
    if not waiting.get("kind"):
        return None
    return WaitingForRead(kind=waiting["kind"], stage=int(waiting["stage"]), attempt=waiting.get("attempt"), approver=waiting.get("approver"))


def _access_requests(session: Session, hire: OnboardingHire) -> List[AccessRequestRead]:
    return [
        AccessRequestRead(
            system_key=r.system_key,
            system_name=SYSTEMS[r.system_key].name,
            contains_patient_data=r.contains_patient_data,
            status=r.status,
            requested_by_type=r.requested_by_type,
            created_at=r.created_at,
        )
        for r in repository.access_requests_for_hire(session, hire.hire_id)
    ]


def _approver_for(session: Session, user: Dict[str, Any]) -> Optional[approvals.Approver]:
    staff = repository.get_staff(session, user.get("id"))
    if staff is None:
        return None
    return approvals.Approver(user_id=staff.user_id, org_role=staff.org_role, department=staff.department)


# --- Catálogo y rol -------------------------------------------------------------


@router.get("/catalog", response_model=CatalogRead)
def get_catalog() -> CatalogRead:
    return CatalogRead(
        stages=[
            {"number": s.number, "key": s.key, "title": s.title("es"), "deadline_day": s.deadline_day, "approver": s.approver, "kind": s.kind, "pass_threshold": s.pass_threshold}
            for s in STAGES
        ],
        departments=DEPARTMENTS,
        role_variants={k: {"department": v.department, "title": v.title_es} for k, v in ROLE_VARIANTS.items()},
        sites=SITES,
        frameworks=FRAMEWORKS,
        systems=[SystemRead(key=s.key, name=s.name, contains_patient_data=s.contains_patient_data) for s in SYSTEMS.values()],
    )


@router.get("/whoami", response_model=OnboardingRole)
def whoami(user: Dict[str, Any] = Depends(get_current_user), session: Session = Depends(get_inventory_db)) -> OnboardingRole:
    staff = repository.get_staff(session, user.get("id"))
    hire = repository.get_hire_by_user(session, user.get("id"))
    caps = _capabilities(session, user)
    return OnboardingRole(
        is_hire=hire is not None,
        hire_id=hire.hire_id if hire else None,
        org_role=staff.org_role if staff else None,
        department=staff.department if staff else None,
        can_view_all=CAP_VIEW_ALL_ONBOARDINGS in caps,
        can_view_reports=bool(staff and staff.org_role == ORG_ROLE_LINE_MANAGER),
        can_create=CAP_CREATE_ONBOARDING in caps,
    )


# --- Vista del equipo -------------------------------------------------------------


@router.get("/hires", response_model=List[OnboardingListItem])
def list_onboardings(user: Dict[str, Any] = Depends(get_current_user), session: Session = Depends(get_inventory_db)) -> List[OnboardingListItem]:
    caps = _capabilities(session, user)
    staff = repository.get_staff(session, user.get("id"))
    if CAP_VIEW_ALL_ONBOARDINGS in caps:
        hires = repository.list_hires(session)
    elif staff and staff.org_role == ORG_ROLE_LINE_MANAGER:
        hires = repository.list_hires(session, manager_user_id=staff.user_id)
    else:
        raise HTTPException(status_code=403, detail="Solo el equipo de Personas, Cumplimiento y los responsables directos ven incorporaciones.")
    return [_list_item(hire) for hire in hires]


def validate_new_hire(payload: Dict[str, Any], session: Session) -> Dict[str, Any]:
    _reject_unknown(payload, HIRE_FIELDS)
    clean: Dict[str, Any] = {}
    for field in HIRE_FIELDS:
        value = payload.get(field, "en" if field == "language" else None)
        if not isinstance(value, str) or not value.strip():
            raise _bad_request(field, "Campo obligatorio.")
        clean[field] = value.strip()
    if clean["department"] not in DEPARTMENTS:
        raise _bad_request("department", "Departamento no válido.")
    if clean["site"] not in SITES:
        raise _bad_request("site", "La sede debe ser Austin o Londres.")
    if clean["framework"] not in FRAMEWORKS:
        raise _bad_request("framework", "El marco debe ser HIPAA o UK GDPR.")
    if clean["language"] not in LANGUAGES:
        raise _bad_request("language", "Idioma no válido.")
    if clean["language"] == "es" and clean["framework"] not in SPANISH_ALLOWED_FRAMEWORKS:
        raise _bad_request("language", "No existe material oficial de UK GDPR en español; el agente no lo traduce.")
    variant = ROLE_VARIANTS.get(clean["role_variant"])
    if variant is None:
        raise _bad_request("role_variant", "No hay material oficial de la etapa «Tu función» para este puesto.")
    if variant.department != clean["department"]:
        raise _bad_request("role_variant", "El puesto no pertenece a ese departamento.")
    try:
        clean["start_date"] = date.fromisoformat(clean["start_date"])
    except ValueError:
        raise _bad_request("start_date", "Fecha no válida (AAAA-MM-DD).") from None
    manager = repository.get_staff(session, clean["manager_user_id"])
    if manager is None or manager.org_role != ORG_ROLE_LINE_MANAGER or manager.department != clean["department"]:
        raise _bad_request("manager_user_id", "El responsable directo debe ser un responsable de ese departamento.")
    if clean["hire_user_id"] == clean["manager_user_id"]:
        raise _bad_request("hire_user_id", "La persona no puede ser su propio responsable.")
    if repository.get_hire_by_user(session, clean["hire_user_id"]) is not None:
        raise _bad_request("hire_user_id", "Esa cuenta ya tiene una incorporación.")
    profile = onboarding_docs.HireProfile(clean["language"], clean["framework"], clean["role_variant"], clean["department"])
    missing = onboarding_docs.material_available(profile)
    if missing:
        raise _bad_request("language", f"Falta material oficial: {', '.join(missing)}.")
    return clean


@router.post("/hires", response_model=OnboardingCreated, status_code=status.HTTP_201_CREATED)
def create_onboarding(
    payload: Any = Body(...),
    user: Dict[str, Any] = Depends(get_current_user),
    session: Session = Depends(get_inventory_db),
) -> OnboardingCreated:
    if CAP_CREATE_ONBOARDING not in _capabilities(session, user):
        raise HTTPException(status_code=403, detail="Solo el equipo de Personas da de alta incorporaciones.")
    clean = validate_new_hire(payload, session)
    hire = OnboardingHire(**clean)
    session.add(hire)
    repository.record_audit(
        session, event_key=f"{hire.hire_id}:created", hire_id=hire.hire_id, stage=None,
        action="onboarding_created", actor_type=ACTOR_HUMAN, actor_user_id=user.get("id"),
    )
    session.commit()
    _enqueue(hire.hire_id)
    return OnboardingCreated(hire_id=hire.hire_id, status=PHASE_STARTING)


@router.get("/hires/{hire_id}", response_model=OnboardingDetail)
def get_onboarding(hire_id: uuid.UUID, user: Dict[str, Any] = Depends(get_current_user), session: Session = Depends(get_inventory_db)) -> OnboardingDetail:
    hire = _get_hire_or_404(session, hire_id)
    _ensure_can_view(session, hire, user)
    caps = _capabilities(session, user)
    approver = _approver_for(session, user)
    waiting = hire.waiting_for or {}
    refusal: Optional[str] = None
    can_decide = False
    if waiting.get("kind") in (WAIT_APPROVAL, WAIT_PAPERWORK):
        refusal = approvals.authorize(int(waiting["stage"]), approver, runner.hire_ref_of(hire))
        can_decide = refusal is None and waiting.get("kind") == WAIT_APPROVAL
    audit = None
    if CAP_VIEW_AUDIT in caps:
        audit = [
            AuditEventRead(stage=e.stage, action=e.action, actor_type=e.actor_type, actor_ref=e.actor_ref, outcome=e.outcome, security_event=e.security_event, created_at=e.created_at)
            for e in repository.list_audit(session, hire.hire_id)
        ]
    base = _list_item(hire).model_dump()
    return OnboardingDetail(
        **base,
        role_variant=hire.role_variant,
        stages=_stages(hire),
        waiting_for=_waiting(hire),
        paperwork={field: bool((hire.paperwork or {}).get(field)) for field in PAPERWORK_FIELDS},
        approvals=hire.approvals or {},
        attempts=hire.attempts or {},
        failed_attempts=hire.failed_attempts or {},
        last_check_result=hire.last_check_result,
        rejection=hire.rejection,
        compliance_approved=access.compliance_approved(hire),
        access_requests=_access_requests(session, hire),
        can_mark_paperwork=waiting.get("kind") == WAIT_PAPERWORK and refusal is None,
        can_decide=can_decide,
        decide_refusal=approvals.REFUSAL_MESSAGES.get(refusal) if refusal else None,
        audit=audit,
    )


def _busy_or_state(exc: Exception) -> HTTPException:
    return HTTPException(status_code=409, detail=str(exc))


@router.post("/hires/{hire_id}/paperwork", response_model=QueuedResponse, status_code=status.HTTP_202_ACCEPTED)
def mark_paperwork(
    hire_id: uuid.UUID,
    payload: Any = Body(...),
    user: Dict[str, Any] = Depends(get_current_user),
    session: Session = Depends(get_inventory_db),
) -> QueuedResponse:
    """Solo tres booleanos. Cualquier otro campo (p. ej. el resultado de la
    verificación de antecedentes) se rechaza sin repetir su valor."""
    hire = _get_hire_or_404(session, hire_id)
    _reject_unknown(payload, PAPERWORK_FIELDS)
    marks = {}
    for field in PAPERWORK_FIELDS:
        value = payload.get(field, False)
        if not isinstance(value, bool):
            raise _bad_request(field, "Debe ser true o false.")
        marks[field] = value
    approver = _approver_for(session, user)
    refusal = approvals.authorize(1, approver, runner.hire_ref_of(hire))
    if refusal:
        raise HTTPException(status_code=403, detail=approvals.REFUSAL_MESSAGES[refusal])
    try:
        runner.submit_command(session, hire, {"kind": WAIT_PAPERWORK, "stage": 1, "value": {"actor": approver.as_claims(), "marks": marks}})
    except (runner.OnboardingBusyError, runner.OnboardingStateError) as exc:
        raise _busy_or_state(exc) from exc
    _enqueue(hire.hire_id)
    return QueuedResponse(hire_id=hire.hire_id)


@router.post("/hires/{hire_id}/approvals", response_model=QueuedResponse, status_code=status.HTTP_202_ACCEPTED)
def decide_approval(
    hire_id: uuid.UUID,
    payload: Any = Body(...),
    user: Dict[str, Any] = Depends(get_current_user),
    session: Session = Depends(get_inventory_db),
) -> QueuedResponse:
    hire = _get_hire_or_404(session, hire_id)
    if not isinstance(payload, dict):
        raise _bad_request("body", "El cuerpo debe ser un objeto JSON.")
    stage = payload.get("stage")
    if not isinstance(stage, int) or stage not in (s.number for s in STAGES):
        raise _bad_request("stage", "Etapa no válida.")
    try:
        decision, comment = approvals.validate_decision(payload)
    except approvals.DecisionError as exc:
        raise _bad_request(exc.field, exc.message) from exc
    approver = _approver_for(session, user)
    refusal = approvals.authorize(stage, approver, runner.hire_ref_of(hire))
    if refusal:
        # Un intento de aprobar sin permiso también queda auditado.
        repository.record_audit(
            session, event_key=f"{hire.hire_id}:approval-refused:{uuid.uuid4()}", hire_id=hire.hire_id, stage=stage,
            action="approval_refused", actor_type=ACTOR_HUMAN, actor_user_id=user.get("id"), outcome=refusal,
            security_event=True,
        )
        session.commit()
        raise HTTPException(status_code=403, detail=approvals.REFUSAL_MESSAGES[refusal])
    try:
        runner.submit_command(session, hire, {"kind": WAIT_APPROVAL, "stage": stage, "value": {"actor": approver.as_claims(), "decision": decision, "comment": comment}})
    except (runner.OnboardingBusyError, runner.OnboardingStateError) as exc:
        raise _busy_or_state(exc) from exc
    _enqueue(hire.hire_id)
    return QueuedResponse(hire_id=hire.hire_id)


# --- Vista de la persona incorporada ------------------------------------------------


@router.get("/me", response_model=MyOnboarding)
def my_onboarding(user: Dict[str, Any] = Depends(get_current_user), session: Session = Depends(get_inventory_db)) -> MyOnboarding:
    hire = _my_hire(session, user)
    profile = runner.profile_of(hire)
    waiting = hire.waiting_for or {}
    content = check = None
    if waiting.get("kind") == WAIT_CHECK:
        stage, attempt = int(waiting["stage"]), int(waiting.get("attempt") or 1)
        reinforcement = bool(hire.reinforce_sections)
        presented = onboarding_docs.present(stage, profile, only_sections=hire.reinforce_sections if reinforcement else None)
        content = ContentRead(**{k: presented[k] for k in ("doc_id", "title", "language", "sections")}, reinforcement=reinforcement)
        check = CheckRead(**checks.public_questions(stage, profile, attempt))
    stages = _stages(hire)
    completed = set(hire.completed_stages or [])
    return MyOnboarding(
        hire_id=hire.hire_id,
        full_name=hire.full_name,
        job_title=hire.job_title,
        language=hire.language,
        framework=hire.framework,
        status=hire.status,
        phase=hire.phase,
        current_stage=hire.current_stage,
        stages=stages,
        remaining_stages=[s.number for s in STAGES if s.number not in completed],
        content=content,
        check=check,
        last_check_result=hire.last_check_result,
        rejection=hire.rejection,
        waiting_for=_waiting(hire),
        questions=[
            QuestionRead(id=q.id, stage=q.stage, question=q.question, status=q.status, answer=q.answer, citations=list(q.citations or []), routed_to=q.routed_to, created_at=q.created_at)
            for q in repository.questions_for_hire(session, hire.hire_id)
        ],
        access_requests=_access_requests(session, hire),
        systems=[SystemRead(key=s.key, name=s.name, contains_patient_data=s.contains_patient_data) for s in SYSTEMS.values()],
        compliance_approved=access.compliance_approved(hire),
    )


@router.post("/me/check", response_model=QueuedResponse, status_code=status.HTTP_202_ACCEPTED)
def submit_check(payload: Any = Body(...), user: Dict[str, Any] = Depends(get_current_user), session: Session = Depends(get_inventory_db)) -> QueuedResponse:
    hire = _my_hire(session, user)
    _reject_unknown(payload, ("stage", "attempt", "answers"))
    stage, attempt, answers = payload.get("stage"), payload.get("attempt"), payload.get("answers")
    if not isinstance(stage, int) or not isinstance(attempt, int) or not isinstance(answers, dict):
        raise _bad_request("answers", "Faltan la etapa, el intento o las respuestas.")
    waiting = hire.waiting_for or {}
    if waiting.get("kind") != WAIT_CHECK or waiting.get("stage") != stage or waiting.get("attempt") != attempt:
        raise HTTPException(status_code=409, detail="Esa comprobación no está abierta ahora mismo.")
    try:
        clean = checks.validate_answers(stage, runner.profile_of(hire), attempt, answers)
    except checks.CheckAnswersError as exc:
        raise _bad_request("answers", str(exc)) from exc
    try:
        runner.submit_command(session, hire, {"kind": WAIT_CHECK, "stage": stage, "value": {"stage": stage, "attempt": attempt, "answers": clean}})
    except (runner.OnboardingBusyError, runner.OnboardingStateError) as exc:
        raise _busy_or_state(exc) from exc
    _enqueue(hire.hire_id)
    return QueuedResponse(hire_id=hire.hire_id)


@router.post("/me/questions", response_model=QuestionRead, status_code=status.HTTP_201_CREATED, dependencies=[Depends(enforce_llm_rate_limit)])
def ask_question(payload: Any = Body(...), user: Dict[str, Any] = Depends(get_current_user), session: Session = Depends(get_inventory_db)) -> QuestionRead:
    hire = _my_hire(session, user)
    _reject_unknown(payload, ("question",))
    try:
        record = qa.answer_question(session, hire, payload.get("question"))
    except qa.QuestionError as exc:
        raise _bad_request("question", str(exc)) from exc
    return QuestionRead(
        id=record.id, stage=record.stage, question=record.question, status=record.status, answer=record.answer,
        citations=list(record.citations or []), routed_to=record.routed_to, created_at=record.created_at,
    )


@router.post(
    "/me/access-requests",
    response_model=AccessRequestResult,
    status_code=status.HTTP_201_CREATED,
    responses={403: {"description": "Bloqueada: sistema con datos de pacientes y etapa 4 sin aprobar"}},
)
def request_access(payload: Any = Body(...), user: Dict[str, Any] = Depends(get_current_user), session: Session = Depends(get_inventory_db)) -> Any:
    hire = _my_hire(session, user)
    _reject_unknown(payload, ("system_key",))
    system_key = payload.get("system_key")
    try:
        request = access.issue_access_request(session, hire, str(system_key), requested_by_type=ACTOR_HUMAN, actor_user_id=user.get("id"))
    except access.UnknownSystemError:
        raise _bad_request("system_key", "Sistema desconocido.") from None
    except access.AccessBlockedError as exc:
        return JSONResponse(status_code=403, content={"detail": str(exc), "status": "blocked", "system_key": exc.request.system_key})
    session.commit()
    return AccessRequestResult(system_key=request.system_key, status=request.status)


@router.get("/notifications", response_model=List[NotificationRead])
def my_notifications(user: Dict[str, Any] = Depends(get_current_user), session: Session = Depends(get_inventory_db)) -> List[NotificationRead]:
    return [
        NotificationRead(id=n.id, hire_id=n.hire_id, stage=n.stage, kind=n.kind, subject=n.subject, body=n.body, sender=n.sender, created_at=n.created_at)
        for n in repository.notifications_for_user(session, user.get("id"))
    ]
