"""Siembra el Registro de Auditoría (BRIEFING §6): pacientes sintéticos,
cuentas demo y un histórico de entradas de auditoría.

    services/api/.venv/bin/python scripts/seed_audit_log.py

Desde la raíz. Necesita `DATABASE_URL` (Supabase) en services/api/.env.

- Cuentas: copia de la TinyDB en data/audit_log/demo-users.db.json (fuera de
  git), con la semilla de Roles y Permisos + lo que pide este BRIEFING:
  Claire y Sandra son Admin (§3), Nina (empleada de Operaciones clínicas, con
  pacientes asignados) y Leo (empleado de Revenue Cycle). Contraseña aleatoria
  (o AUDIT_SEED_PASSWORD), impresa al final.
- Pacientes: 8 fichas 100 % sintéticas en `patient_records` (idempotente).
- Auditoría: ~70 entradas en 30 días con lecturas (con motivo), cambios
  clínicos y de facturación, reclamaciones aprobada y denegada, una
  exportación, un acceso denegado, logins, un cambio de asignación, el
  asistente de documentación (proceso) y una ráfaga de lecturas de Leo en las
  últimas horas para la detección de accesos inusuales.

El registro es append-only: NO se puede "volver a sembrar" borrando. Cada
entrada de la semilla lleva `details.seed_batch`, y si ese lote ya existe el
script no escribe nada. Las entradas se fechan en el pasado (orden de
`sequence` ≠ orden temporal si la tabla ya tenía entradas reales).
"""

from __future__ import annotations

import os
import random
import secrets
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
API_DIR = ROOT_DIR / "services" / "api"
DEMO_TINYDB = ROOT_DIR / "data" / "audit_log" / "demo-users.db.json"
os.environ.setdefault("SUPPLIERS_DB_PATH", str(DEMO_TINYDB))
sys.path[:0] = [str(ROOT_DIR), str(API_DIR)]

SEED_BATCH = "audit-log-v1"

EXTRA_USERS = [
    # email, rol, departamento, nombre
    ("sandra.finance@healthcore.example", "admin", "revenue_cycle", "Dra. Sandra Okonkwo"),
    ("nina.coordinator@healthcore.example", "employee", "clinical_operations", "Nina Patel"),
    ("leo.billing@healthcore.example", "employee", "revenue_cycle", "Leo Grant"),
]


def _seed_users(password: str) -> dict[str, str]:
    from access_models import role_id_for
    from access_seed import seed
    from auth_repository import AuthRepository
    from security import hash_password

    hashed = hash_password(password)
    result = seed(hashed)
    repo = AuthRepository()
    ids = dict(result.user_ids)
    for email, role, department, name in EXTRA_USERS:
        existing = repo.get_user_by_email(email)
        if existing:
            ids[email] = existing["id"]
            continue
        user, _ = repo.create_user(
            email=email, hashed_password=hashed, role=role, department=department, profile_data={"name": name}
        )
        ids[email] = user["id"]
    # BRIEFING de auditoría §3: Claire es Admin (en la semilla de Roles era empleada).
    repo.assign(ids["claire.compliance@healthcore.example"], role_id=role_id_for("admin"))
    for user_id in ids.values():
        repo.set_password(user_id, hashed)
    return {email.split(".")[0]: user_id for email, user_id in ids.items()}


def _seed_patients(session, ids: dict[str, str]) -> int:
    from sqlmodel import select

    from services.patients import seed_data
    from services.patients.models import PatientRecord

    created = 0
    for patient, claims in seed_data.build_patients({1: ids["nina"], 2: ids["nina"], 4: ids["nina"]}):
        if session.exec(select(PatientRecord).where(PatientRecord.patient_ref == patient.patient_ref)).first():
            continue
        session.add(patient)
        session.flush()
        session.add_all(claims)
        created += 1
    session.commit()
    return created


def _already_seeded(session) -> bool:
    from sqlmodel import select

    from services.audit.models import AuditLogEntry

    return session.exec(
        select(AuditLogEntry.sequence).where(AuditLogEntry.details.contains(f'"seed_batch":"{SEED_BATCH}"')).limit(1)
    ).first() is not None


def _seed_entries(session, ids: dict[str, str]) -> int:
    from services.audit import catalog
    from services.audit.writer import Actor, anonymous_actor, human_actor, process_actor, record
    from services.patients import seed_data

    rng = random.Random(20261107)  # misma semilla → mismo histórico
    now = datetime.now(timezone.utc).replace(microsecond=0)
    clinical = catalog.department_id("clinical_operations")
    experience = catalog.department_id("patient_experience")
    revenue = catalog.department_id("revenue_cycle")
    ref = seed_data.patient_ref_for
    events: list[tuple[datetime, dict]] = []

    def ip(n: int) -> str:
        return f"ip:10.20.0.{n}"  # red interna ficticia

    def add(when: datetime, actor: Actor, action: str, **kwargs) -> None:
        details = {**kwargs.pop("details", {}), "seed_batch": SEED_BATCH}
        events.append((when, {"actor": actor, "action": action, "details": details, **kwargs}))

    # 1) Rutina de 30 días: Nina abre 1-2 fichas de sus pacientes cada día laborable.
    for days_ago in range(30, 1, -1):
        day = now - timedelta(days=days_ago)
        if day.weekday() >= 5:
            continue
        for _ in range(rng.choice((1, 1, 2))):
            when = day.replace(hour=rng.randint(8, 17), minute=rng.randint(0, 59))
            patient = rng.choice((1, 2, 4))
            add(when, human_actor(ids["nina"], ip(21)), catalog.PATIENT_RECORD_VIEWED,
                resource_id=None, patient_ref=ref(patient), department_id=clinical,
                context=rng.choice(("routine_review", "care_coordination")),
                details={"sections": ["clinical", "billing"]})
        # Leo, de facturación, mira 0-1 fichas al día: su media es baja.
        if rng.random() < 0.4:
            when = day.replace(hour=rng.randint(9, 16), minute=rng.randint(0, 59))
            add(when, human_actor(ids["leo"], ip(34)), catalog.PATIENT_RECORD_VIEWED,
                patient_ref=ref(rng.choice((1, 2, 5, 7))), department_id=revenue,
                context="billing_review", details={"sections": ["billing"]})

    d = lambda days, hour: (now - timedelta(days=days)).replace(hour=hour, minute=rng.randint(0, 59))  # noqa: E731

    # 2) Logins (correcto y fallido).
    add(d(6, 8), human_actor(ids["marcus"], ip(11)), "login_succeeded", resource_id=ids["marcus"],
        department_id=clinical, details={"login_method": "password"})
    add(d(6, 8), anonymous_actor(ip(99)), "login_failed", outcome=catalog.OUTCOME_DENIED,
        details={"login_method": "password", "identifier_hash": "0" * 63 + "1"})

    # 3) Modificaciones clínicas y de facturación, reclamaciones.
    add(d(5, 10), human_actor(ids["marcus"], ip(11)), "patient_clinical_updated", patient_ref=ref(1),
        department_id=clinical, context="care_coordination", details={"changed_fields": ["clinical_notes"]})
    add(d(5, 11), process_actor(catalog.PROCESS_CLINICAL_DOCUMENTATION, on_behalf_of=ids["nina"]),
        "patient_documentation_drafted", patient_ref=ref(1), department_id=clinical,
        context="care_coordination", details={"changed_fields": ["documentation_draft"]})
    add(d(4, 9), human_actor(ids["tom"], ip(31)), "patient_billing_updated", patient_ref=ref(4),
        department_id=revenue, context="billing_review", details={"changed_fields": ["outstanding_balance_cents"]})
    add(d(4, 10), human_actor(ids["tom"], ip(31)), "insurance_claim_approved", patient_ref=ref(5),
        department_id=revenue, context="claim_review",
        details={"claim_ref": seed_data.claim_ref_for(5, 1), "reason_code": "covered_service"})
    add(d(4, 11), human_actor(ids["tom"], ip(31)), "insurance_claim_denied", patient_ref=ref(2),
        department_id=revenue, context="claim_review",
        details={"claim_ref": seed_data.claim_ref_for(2, 2), "reason_code": "missing_documentation"})

    # 4) Exportación (BRIEFING §6) y un acceso denegado fuera del equipo de cuidado.
    add(d(3, 15), human_actor(ids["marcus"], ip(11)), "patient_data_exported", department_id=clinical,
        context="compliance_investigation",
        details={"record_count": 5, "patient_refs": [ref(n) for n in range(1, 6)], "format": "csv",
                 "sections": ["clinical", "billing"]})
    add(d(2, 12), human_actor(ids["priya"], ip(41)), catalog.PATIENT_RECORD_VIEWED, patient_ref=ref(3),
        department_id=clinical, context="routine_review", outcome=catalog.OUTCOME_DENIED)
    add(d(2, 13), human_actor(ids["priya"], ip(41)), catalog.PATIENT_RECORD_VIEWED, patient_ref=ref(7),
        department_id=experience, context="patient_request", details={"sections": ["clinical", "billing"]})

    # 5) Cambio de asignación hecho por un Admin (James).
    add(d(2, 16), human_actor(ids["james"], ip(5)), "user_assignment_changed", resource_id=ids["leo"],
        department_id=revenue, details={"target_user": ids["leo"], "from_role": "employee", "to_role": "employee",
                                        "from_department": "patient_experience", "to_department": "revenue_cycle"})

    # 6) Ráfaga: Leo abre 24 fichas en las últimas horas (BRIEFING §5).
    for minute in range(24):
        when = now - timedelta(hours=3) + timedelta(minutes=minute * 5)
        add(when, human_actor(ids["leo"], ip(34)), catalog.PATIENT_RECORD_VIEWED,
            patient_ref=ref(1 + minute % 8), department_id=revenue, context="billing_review",
            details={"sections": ["billing"]})

    for when, kwargs in sorted(events, key=lambda item: item[0]):
        record(session, occurred_at=when, **kwargs)
    session.commit()
    return len(events)


def main() -> int:
    DEMO_TINYDB.parent.mkdir(parents=True, exist_ok=True)
    password = os.getenv("AUDIT_SEED_PASSWORD") or secrets.token_urlsafe(12)
    try:
        ids = _seed_users(password)
    except Exception as exc:  # TinyDB ilegible, disco lleno…
        print(f"No se pudieron sembrar las cuentas: {type(exc).__name__}", file=sys.stderr)
        return 1

    try:
        from sqlmodel import Session, SQLModel

        from database import get_inventory_engine
        from services.audit.models import AuditLogEntry, ensure_audit_log_guards
        from services.patients.models import InsuranceClaim, PatientRecord

        engine = get_inventory_engine()
        SQLModel.metadata.create_all(
            engine, tables=[PatientRecord.__table__, InsuranceClaim.__table__, AuditLogEntry.__table__]
        )
        ensure_audit_log_guards(engine)
        with Session(engine) as session:
            patients = _seed_patients(session, ids)
            entries = 0 if _already_seeded(session) else _seed_entries(session, ids)
    except Exception as exc:  # Supabase dormido, DATABASE_URL ausente…
        print(f"No se pudo sembrar Supabase: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    print(f"TinyDB: {DEMO_TINYDB}")
    print(f"Pacientes sintéticos nuevos: {patients}")
    print(f"Entradas de auditoría nuevas: {entries}" + ("" if entries else " (el lote ya existía)"))
    print("Cuentas: " + ", ".join(sorted(ids)))
    print(f"\nContraseña de esta ejecución (todas las cuentas de la semilla): {password}")
    print(f"Arranca la API con: SUPPLIERS_DB_PATH={DEMO_TINYDB} .venv/bin/uvicorn main:app --port 8000")
    return 0


if __name__ == "__main__":
    sys.exit(main())
