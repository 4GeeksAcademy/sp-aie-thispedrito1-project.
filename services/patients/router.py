"""/patients: ficha de pacientes sintéticos con TODA acción auditada.

| Operación                                  | Rol mínimo | Datos                            | Auditoría                         |
|--------------------------------------------|------------|----------------------------------|-----------------------------------|
| GET /patients                              | employee   | refs visibles (sin contenido)    | no (no hay contenido clínico)     |
| GET /patients/{ref}?purpose=               | employee   | secciones visibles               | patient_record_viewed (+ denied)  |
| PATCH /patients/{ref}/clinical             | supervisor | equipo de cuidado                | patient_clinical_updated          |
| PATCH /patients/{ref}/billing              | supervisor | Revenue Cycle                    | patient_billing_updated           |
| POST …/claims/{claim_ref}/decision         | supervisor | Revenue Cycle                    | insurance_claim_approved/denied   |
| POST /patients/{ref}/documentation-draft   | employee   | equipo de cuidado                | patient_documentation_drafted (proceso) |
| POST /patients/export                      | supervisor | secciones visibles               | patient_data_exported             |

Cada cambio y su entrada de auditoría van en la MISMA transacción. Una
lectura se registra (commit) ANTES de devolver los datos: si el registro no
se puede escribir, no hay datos (falla cerrado).
"""

from __future__ import annotations

import csv
import io
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request, Response, status
from sqlmodel import Session, select

from access_models import CAP_RECORDS_APPROVE, CAP_RECORDS_CREATE, CAP_RECORDS_READ, CAP_RECORDS_UPDATE
from database import get_inventory_db
from permissions import forbidden_department, no_department, require_capability
from security import access_repo
from services.audit import catalog
from services.audit.writer import Actor, human_actor, process_actor, record, request_origin
from services.patients import assistant
from services.patients.models import CLAIM_APPROVED, CLAIM_DENIED, CLAIM_PENDING, InsuranceClaim, PatientRecord
from services.patients.rules import (
    CLAIM_REASON_CODES,
    REVENUE_CYCLE_ID,
    SECTION_BILLING,
    SECTION_CLINICAL,
    is_care_team,
    is_revenue_cycle,
    visible_sections,
)
from services.patients.schemas import (
    BillingSection,
    ClaimRead,
    ClinicalSection,
    PatientCatalog,
    PatientDetail,
    PatientListItem,
    PurposeOption,
)

router = APIRouter(prefix="/patients", tags=["patients"])

User = dict[str, Any]

TEXT_LIMITS = {"diagnosis": 200, "clinical_notes": 4000, "insurance_plan": 80}
MAX_BALANCE_CENTS = 100_000_000


# --- Validación de entradas (400 sin repetir el valor) ------------------------------


def _bad_request(errors: list[dict[str, str]]) -> HTTPException:
    return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail={"errors": errors})


def _purpose(value: Any) -> str:
    if value not in catalog.ACCESS_PURPOSES:
        raise _bad_request([{"field": "purpose", "message": "Choose an access purpose from the list."}])
    return str(value)


def _text_changes(payload: dict[str, Any], fields: tuple[str, ...]) -> dict[str, str]:
    changes: dict[str, str] = {}
    errors = []
    for name in fields:
        if name not in payload or payload[name] is None:
            continue
        value = payload[name]
        if not isinstance(value, str) or not value.strip():
            errors.append({"field": name, "message": "Must be a non-empty text."})
        elif len(value) > TEXT_LIMITS[name]:
            errors.append({"field": name, "message": f"Must be at most {TEXT_LIMITS[name]} characters."})
        else:
            changes[name] = value.strip()
    if errors:
        raise _bad_request(errors)
    return changes


# --- Ayudas ----------------------------------------------------------------------------


def _user_actor(user: User, request: Request) -> Actor:
    return human_actor(str(user["id"]), request_origin(request))


def _get_patient(session: Session, patient_ref: str) -> PatientRecord:
    patient = session.exec(select(PatientRecord).where(PatientRecord.patient_ref == patient_ref)).first()
    if patient is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Patient not found")
    return patient


def _claims(session: Session, patient: PatientRecord) -> list[InsuranceClaim]:
    return list(
        session.exec(
            select(InsuranceClaim)
            .where(InsuranceClaim.patient_record_id == patient.id)
            .order_by(InsuranceClaim.created_at)
        ).all()
    )


def _deny(
    session: Session, user: User, request: Request, *, action: str, patient: PatientRecord, context: Optional[str]
) -> HTTPException:
    """Registra el intento rechazado (commit) y devuelve el 403 para lanzarlo.

    Un acceso denegado a la ficha de un paciente es justo lo que Compliance
    quiere ver: se guarda con `outcome=denied` en el departamento del
    paciente (lo ve su Supervisor) y nunca con datos de la ficha."""
    record(
        session,
        actor=_user_actor(user, request),
        action=action,
        resource_id=patient.id,
        patient_ref=patient.patient_ref,
        department_id=patient.department_id,
        context=context,
        outcome=catalog.OUTCOME_DENIED,
    )
    session.commit()
    return forbidden_department()


def _entry_department(user: User, patient: PatientRecord) -> str:
    """Departamento de la entrada: el del paciente si actúa su equipo de
    cuidado; Revenue Cycle si actúa facturación."""
    return patient.department_id if is_care_team(user, patient.department_id) else REVENUE_CYCLE_ID


def _detail(session: Session, patient: PatientRecord, sections: list[str], audit_sequence: int) -> PatientDetail:
    clinical = (
        ClinicalSection.model_validate(patient, from_attributes=True) if SECTION_CLINICAL in sections else None
    )
    billing = None
    if SECTION_BILLING in sections:
        billing = BillingSection(
            insurance_plan=patient.insurance_plan,
            outstanding_balance_cents=patient.outstanding_balance_cents,
            currency=patient.currency,
            claims=[ClaimRead.model_validate(claim, from_attributes=True) for claim in _claims(session, patient)],
        )
    return PatientDetail(
        patient_ref=patient.patient_ref,
        department=access_repo.department_code(patient.department_id),
        country=patient.country,
        sections=sections,
        clinical=clinical,
        billing=billing,
        audit_sequence=audit_sequence,
    )


def _patients_in_scope(session: Session, user: User) -> list[PatientRecord]:
    if not user.get("department_id"):
        raise no_department()
    statement = select(PatientRecord).order_by(PatientRecord.patient_ref)
    if not is_revenue_cycle(user):
        statement = statement.where(PatientRecord.department_id == user["department_id"])
    return list(session.exec(statement).all())


# --- Rutas -------------------------------------------------------------------------------


@router.get("/catalog", response_model=PatientCatalog)
def patient_catalog(_: User = Depends(require_capability(CAP_RECORDS_READ))) -> PatientCatalog:
    return PatientCatalog(
        purposes=[PurposeOption(code=code, label=label) for code, label in catalog.ACCESS_PURPOSES.items()],
        claim_reason_codes={key: list(codes) for key, codes in CLAIM_REASON_CODES.items()},
    )


@router.get("", response_model=list[PatientListItem])
def list_patients(
    user: User = Depends(require_capability(CAP_RECORDS_READ)),
    session: Session = Depends(get_inventory_db),
) -> list[PatientListItem]:
    """Solo referencias y recuentos, sin contenido clínico (no se audita: el
    acceso al contenido es abrir la ficha)."""
    items = []
    for patient in _patients_in_scope(session, user):
        pending = sum(1 for claim in _claims(session, patient) if claim.status == CLAIM_PENDING)
        items.append(
            PatientListItem(
                patient_ref=patient.patient_ref,
                department=access_repo.department_code(patient.department_id),
                country=patient.country,
                assigned_to_me=patient.assigned_user_id == user["id"],
                sections=visible_sections(user, patient.department_id),
                pending_claims=pending,
            )
        )
    return items


@router.post(
    "/export",
    response_class=Response,
    responses={200: {"content": {"text/csv": {}}, "description": "CSV de los pacientes exportados (auditado)"}},
)
def export_patients(
    request: Request,
    payload: dict[str, Any] = Body(default_factory=dict),
    user: User = Depends(require_capability(CAP_RECORDS_APPROVE)),
    session: Session = Depends(get_inventory_db),
) -> Response:
    """CSV de los pacientes en alcance, con solo las secciones que el usuario
    puede ver. Toda exportación queda registrada, "sea cual sea el motivo"."""
    purpose = _purpose(payload.get("purpose"))
    requested = payload.get("patient_refs")
    in_scope = _patients_in_scope(session, user)
    if requested is not None:
        if not isinstance(requested, list) or not all(isinstance(ref, str) for ref in requested) or not requested:
            raise _bad_request([{"field": "patient_refs", "message": "Send a non-empty list of patient references."}])
        by_ref = {patient.patient_ref: patient for patient in in_scope}
        if any(ref not in by_ref for ref in requested):
            record(
                session,
                actor=_user_actor(user, request),
                action="patient_data_exported",
                department_id=user.get("department_id"),
                context=purpose,
                outcome=catalog.OUTCOME_DENIED,
                details={"record_count": len(requested), "format": "csv"},
            )
            session.commit()
            raise forbidden_department()
        patients = [by_ref[ref] for ref in dict.fromkeys(requested)]
    else:
        patients = in_scope

    columns = ["patient_ref", "country"]
    clinical = any(SECTION_CLINICAL in visible_sections(user, p.department_id) for p in patients)
    if clinical:
        columns += ["full_name", "date_of_birth", "diagnosis"]
    columns += ["insurance_plan", "outstanding_balance", "currency"]

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(columns)
    for patient in patients:
        sections = visible_sections(user, patient.department_id)
        row = {
            "patient_ref": patient.patient_ref,
            "country": patient.country,
            "insurance_plan": patient.insurance_plan,
            "outstanding_balance": f"{patient.outstanding_balance_cents / 100:.2f}",
            "currency": patient.currency,
        }
        if SECTION_CLINICAL in sections:
            row.update(full_name=patient.full_name, date_of_birth=patient.date_of_birth, diagnosis=patient.diagnosis)
        writer.writerow([_csv_safe(row.get(column, "")) for column in columns])

    export_id = str(uuid.uuid4())
    entry = record(
        session,
        actor=_user_actor(user, request),
        action="patient_data_exported",
        resource_id=export_id,
        department_id=user.get("department_id"),
        context=purpose,
        details={
            "record_count": len(patients),
            "patient_refs": [patient.patient_ref for patient in patients],
            "format": "csv",
            "sections": [SECTION_CLINICAL, SECTION_BILLING] if clinical else [SECTION_BILLING],
        },
    )
    session.commit()
    return Response(
        content=buffer.getvalue(),
        media_type="text/csv",
        headers={
            "Content-Disposition": f'attachment; filename="patients-export-{export_id[:8]}.csv"',
            "X-Audit-Sequence": str(entry.sequence),
        },
    )


def _csv_safe(value: Any) -> str:
    """Evita la inyección de fórmulas al abrir el CSV en una hoja de cálculo."""
    text = str(value)
    return f"'{text}" if text[:1] in ("=", "+", "-", "@") else text


@router.get("/{patient_ref}", response_model=PatientDetail)
def view_patient(
    patient_ref: str,
    request: Request,
    purpose: Optional[str] = Query(default=None),
    user: User = Depends(require_capability(CAP_RECORDS_READ)),
    session: Session = Depends(get_inventory_db),
) -> PatientDetail:
    """Abrir la ficha exige un motivo (BRIEFING §2: "desde qué contexto")."""
    context = _purpose(purpose)
    patient = _get_patient(session, patient_ref)
    sections = visible_sections(user, patient.department_id)
    if not sections:
        raise _deny(session, user, request, action=catalog.PATIENT_RECORD_VIEWED, patient=patient, context=context)

    entry = record(
        session,
        actor=_user_actor(user, request),
        action=catalog.PATIENT_RECORD_VIEWED,
        resource_id=patient.id,
        patient_ref=patient.patient_ref,
        department_id=_entry_department(user, patient),
        context=context,
        details={"sections": sections},
    )
    session.commit()
    return _detail(session, patient, sections, entry.sequence)


@router.patch("/{patient_ref}/clinical", response_model=PatientDetail)
def update_clinical(
    patient_ref: str,
    request: Request,
    payload: dict[str, Any] = Body(default_factory=dict),
    user: User = Depends(require_capability(CAP_RECORDS_UPDATE)),
    session: Session = Depends(get_inventory_db),
) -> PatientDetail:
    context = _purpose(payload.get("purpose"))
    changes = _text_changes(payload, ("diagnosis", "clinical_notes"))
    if not changes:
        raise _bad_request([{"field": "changes", "message": "Send diagnosis and/or clinical_notes."}])
    patient = _get_patient(session, patient_ref)
    if not is_care_team(user, patient.department_id):
        raise _deny(session, user, request, action="patient_clinical_updated", patient=patient, context=context)

    changed = sorted(name for name, value in changes.items() if getattr(patient, name) != value)
    for name, value in changes.items():
        setattr(patient, name, value)
    patient.updated_at = datetime.now(timezone.utc)
    session.add(patient)
    entry = record(
        session,
        actor=_user_actor(user, request),
        action="patient_clinical_updated",
        resource_id=patient.id,
        patient_ref=patient.patient_ref,
        department_id=patient.department_id,
        context=context,
        details={"changed_fields": changed},
    )
    session.commit()
    session.refresh(patient)
    return _detail(session, patient, visible_sections(user, patient.department_id), entry.sequence)


@router.patch("/{patient_ref}/billing", response_model=PatientDetail)
def update_billing(
    patient_ref: str,
    request: Request,
    payload: dict[str, Any] = Body(default_factory=dict),
    user: User = Depends(require_capability(CAP_RECORDS_UPDATE)),
    session: Session = Depends(get_inventory_db),
) -> PatientDetail:
    context = _purpose(payload.get("purpose"))
    changes: dict[str, Any] = _text_changes(payload, ("insurance_plan",))
    if payload.get("outstanding_balance_cents") is not None:
        balance = payload["outstanding_balance_cents"]
        if isinstance(balance, bool) or not isinstance(balance, int) or not 0 <= balance <= MAX_BALANCE_CENTS:
            raise _bad_request([{"field": "outstanding_balance_cents", "message": "Must be a whole number of cents ≥ 0."}])
        changes["outstanding_balance_cents"] = balance
    if not changes:
        raise _bad_request([{"field": "changes", "message": "Send insurance_plan and/or outstanding_balance_cents."}])
    patient = _get_patient(session, patient_ref)
    if not is_revenue_cycle(user):
        raise _deny(session, user, request, action="patient_billing_updated", patient=patient, context=context)

    changed = sorted(name for name, value in changes.items() if getattr(patient, name) != value)
    for name, value in changes.items():
        setattr(patient, name, value)
    patient.updated_at = datetime.now(timezone.utc)
    session.add(patient)
    entry = record(
        session,
        actor=_user_actor(user, request),
        action="patient_billing_updated",
        resource_id=patient.id,
        patient_ref=patient.patient_ref,
        department_id=REVENUE_CYCLE_ID,
        context=context,
        details={"changed_fields": changed},
    )
    session.commit()
    session.refresh(patient)
    return _detail(session, patient, visible_sections(user, patient.department_id), entry.sequence)


@router.post("/{patient_ref}/claims/{claim_ref}/decision", response_model=PatientDetail)
def decide_claim(
    patient_ref: str,
    claim_ref: str,
    request: Request,
    payload: dict[str, Any] = Body(default_factory=dict),
    user: User = Depends(require_capability(CAP_RECORDS_APPROVE)),
    session: Session = Depends(get_inventory_db),
) -> PatientDetail:
    decision = payload.get("decision")
    if decision not in CLAIM_REASON_CODES:
        raise _bad_request([{"field": "decision", "message": "Decision must be 'approve' or 'deny'."}])
    reason = payload.get("reason_code")
    if reason not in CLAIM_REASON_CODES[decision]:
        raise _bad_request([{"field": "reason_code", "message": "Choose a reason code from the list."}])
    action = "insurance_claim_approved" if decision == "approve" else "insurance_claim_denied"

    patient = _get_patient(session, patient_ref)
    claim = next((item for item in _claims(session, patient) if item.claim_ref == claim_ref), None)
    if claim is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Claim not found")
    if not is_revenue_cycle(user):
        raise _deny(session, user, request, action=action, patient=patient, context="claim_review")
    if claim.status != CLAIM_PENDING:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="This claim was already decided.")

    claim.status = CLAIM_APPROVED if decision == "approve" else CLAIM_DENIED
    claim.reason_code = reason
    claim.decided_by = str(user["id"])
    claim.decided_at = datetime.now(timezone.utc)
    session.add(claim)
    entry = record(
        session,
        actor=_user_actor(user, request),
        action=action,
        resource_id=claim.id,
        patient_ref=patient.patient_ref,
        department_id=REVENUE_CYCLE_ID,
        context="claim_review",
        details={"claim_ref": claim.claim_ref, "reason_code": reason},
    )
    session.commit()
    session.refresh(patient)
    return _detail(session, patient, visible_sections(user, patient.department_id), entry.sequence)


@router.post("/{patient_ref}/documentation-draft", response_model=PatientDetail)
def draft_documentation(
    patient_ref: str,
    request: Request,
    payload: dict[str, Any] = Body(default_factory=dict),
    user: User = Depends(require_capability(CAP_RECORDS_CREATE)),
    session: Session = Depends(get_inventory_db),
) -> PatientDetail:
    """El asistente de documentación escribe un borrador en la ficha. Quien
    actúa sobre los datos es el PROCESO; la persona queda en `on_behalf_of`."""
    context = _purpose(payload.get("purpose"))
    patient = _get_patient(session, patient_ref)
    if not is_care_team(user, patient.department_id):
        raise _deny(session, user, request, action="patient_documentation_drafted", patient=patient, context=context)

    patient.documentation_draft = assistant.draft_follow_up_note(patient)
    patient.updated_at = datetime.now(timezone.utc)
    session.add(patient)
    entry = record(
        session,
        actor=process_actor(catalog.PROCESS_CLINICAL_DOCUMENTATION, on_behalf_of=str(user["id"])),
        action="patient_documentation_drafted",
        resource_id=patient.id,
        patient_ref=patient.patient_ref,
        department_id=patient.department_id,
        context=context,
        details={"changed_fields": ["documentation_draft"]},
    )
    session.commit()
    session.refresh(patient)
    return _detail(session, patient, visible_sections(user, patient.department_id), entry.sequence)
