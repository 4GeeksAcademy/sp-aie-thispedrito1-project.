"""Fixtures del orquestador de incorporaciones (sin red, sin Supabase, sin Redis).

Se ejecutan desde la raíz con el venv de la API:

    services/api/.venv/bin/python -m pytest tests/onboarding

- Base de datos: SQLite (archivo temporal) con SOLO las tablas del onboarding
  (`create_all(tables=...)`: crear el metadata entero falla si otro test
  registró las tablas `reporting.*`).
- Checkpointer: `SqliteSaver` sobre otro archivo temporal, el mismo tipo que
  usa la prueba de reinicio entre procesos.
"""

from __future__ import annotations

import sys
import uuid
from datetime import date
from pathlib import Path
from typing import Any, Dict, Iterator

ROOT_DIR = Path(__file__).resolve().parents[2]
for path in (str(ROOT_DIR), str(ROOT_DIR / "services" / "api")):
    if path not in sys.path:
        sys.path.insert(0, path)

import pytest  # noqa: E402
from sqlmodel import Session, SQLModel, create_engine  # noqa: E402

from services.onboarding import checks, repository, runner  # noqa: E402
from services.onboarding.graph import WAIT_APPROVAL, WAIT_CHECK, WAIT_PAPERWORK  # noqa: E402
from services.onboarding.models import (  # noqa: E402
    OnboardingAccessRequest,
    OnboardingAuditEvent,
    OnboardingHire,
    OnboardingNotification,
    OnboardingQuestion,
    OnboardingStaff,
)

from services.audit.models import AuditLogEntry  # noqa: E402

ONBOARDING_TABLES = [
    AuditLogEntry.__table__,  # Registro de Auditoría: los accesos del agente se auditan
    OnboardingStaff.__table__,
    OnboardingHire.__table__,
    OnboardingAuditEvent.__table__,
    OnboardingNotification.__table__,
    OnboardingQuestion.__table__,
    OnboardingAccessRequest.__table__,
]

# Directorio sintético (BRIEFING, "Datos semilla"): dos responsables de
# departamentos distintos, una aprobadora de Cumplimiento, Diane y Personas.
STAFF = {
    "diane": ("Diane Foster", "vp_people", "people", "austin"),
    "people-1": ("Ruth Okafor", "people_team", "people", "austin"),
    "claire": ("Claire Whitfield", "compliance_approver", "compliance", "london"),
    "mgr-digital": ("Marco Ruiz", "line_manager", "digital", "austin"),
    "mgr-revenue": ("Hannah Price", "line_manager", "revenue_cycle", "london"),
}


def create_tables(engine: Any) -> None:
    SQLModel.metadata.create_all(engine, tables=ONBOARDING_TABLES)


def seed_staff(session: Session) -> None:
    for user_id, (name, role, department, site) in STAFF.items():
        session.add(OnboardingStaff(user_id=user_id, display_name=name, org_role=role, department=department, site=site))
    session.commit()


def new_hire(session: Session, **overrides: Any) -> OnboardingHire:
    data: Dict[str, Any] = dict(
        hire_user_id=f"hire-{uuid.uuid4().hex[:8]}",
        full_name="Alex Test",
        job_title="Software Engineer",
        department="digital",
        role_variant="digital_engineer",
        site="austin",
        framework="hipaa",
        language="en",
        manager_user_id="mgr-digital",
        start_date=date(2026, 11, 2),
    )
    data.update(overrides)
    hire = OnboardingHire(**data)
    session.add(hire)
    session.commit()
    session.refresh(hire)
    return hire


def actor(user_id: str) -> Dict[str, str]:
    _, role, department, _ = STAFF[user_id]
    return {"user_id": user_id, "org_role": role, "department": department}


def answers_for(hire: OnboardingHire, stage: int, attempt: int, *, correct: bool = True, wrong: int = 0) -> Dict[str, int]:
    """Respuestas al juego del intento: todas bien, o las `wrong` primeras mal."""
    _, questions = checks.question_set(stage, runner.profile_of(hire), attempt)
    result = {}
    for index, q in enumerate(questions):
        bad = (not correct) or index < wrong
        result[q["id"]] = (q["answer"] + 1) % len(q["options"]) if bad else q["answer"]
    return result


class Driver:
    """Atajos para recorrer el flujo como lo haría la API + el worker."""

    def __init__(self, session: Session, saver: Any) -> None:
        self.session = session
        self.saver = saver

    def advance(self, hire: OnboardingHire) -> OnboardingHire:
        runner.advance(self.session, hire.hire_id, saver=self.saver)
        self.session.refresh(hire)
        return hire

    def send(self, hire: OnboardingHire, kind: str, stage: int, value: Dict[str, Any]) -> OnboardingHire:
        runner.submit_command(self.session, hire, {"kind": kind, "stage": stage, "value": value})
        return self.advance(hire)

    def paperwork(self, hire: OnboardingHire, by: str = "people-1", **marks: bool) -> OnboardingHire:
        marks = marks or {"contract_received": True, "payroll_docs_received": True, "background_check_completed": True}
        return self.send(hire, WAIT_PAPERWORK, 1, {"actor": actor(by), "marks": marks})

    def check(self, hire: OnboardingHire, stage: int, *, correct: bool = True, wrong: int = 0) -> OnboardingHire:
        attempt = int(hire.waiting_for["attempt"])
        answers = answers_for(hire, stage, attempt, correct=correct, wrong=wrong)
        return self.send(hire, WAIT_CHECK, stage, {"stage": stage, "attempt": attempt, "answers": answers})

    def decide(self, hire: OnboardingHire, stage: int, by: str, decision: str = "approve", comment: str = None) -> OnboardingHire:
        return self.send(hire, WAIT_APPROVAL, stage, {"actor": actor(by), "decision": decision, "comment": comment})

    def to_stage(self, hire: OnboardingHire, target: int) -> OnboardingHire:
        """Avanza aprobándolo todo hasta que la etapa `target` espera algo."""
        if hire.phase == "starting":
            hire = self.advance(hire)
        approvers = {4: "claire", 5: hire.manager_user_id, 7: "diane"}
        while hire.current_stage < target and hire.status == "active":
            waiting = hire.waiting_for
            if waiting["kind"] == WAIT_PAPERWORK:
                hire = self.paperwork(hire)
            elif waiting["kind"] == WAIT_CHECK:
                hire = self.check(hire, waiting["stage"])
            else:
                hire = self.decide(hire, waiting["stage"], approvers[waiting["stage"]])
        return hire


@pytest.fixture()
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "onboarding.sqlite"


@pytest.fixture()
def checkpoint_path(tmp_path: Path) -> Path:
    return tmp_path / "checkpoints.sqlite"


@pytest.fixture()
def engine(db_path: Path):
    engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})
    create_tables(engine)
    yield engine
    engine.dispose()


@pytest.fixture()
def session(engine) -> Iterator[Session]:
    with Session(engine) as session:
        seed_staff(session)
        yield session


@pytest.fixture()
def saver(checkpoint_path: Path):
    with runner.open_saver(database_url="", sqlite_path=checkpoint_path) as saver:
        yield saver


@pytest.fixture()
def driver(session: Session, saver) -> Driver:
    return Driver(session, saver)


@pytest.fixture()
def audit_rows(session: Session):
    def read(hire: OnboardingHire):
        return repository.list_audit(session, hire.hire_id)

    return read
