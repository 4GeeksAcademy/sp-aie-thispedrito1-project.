"""Ejecuta el orquestador para una incorporación y vuelca su estado a Supabase.

`advance()` es el único punto de entrada y es idempotente:
- sin checkpoint → arranca el grafo hasta la primera pausa (trámites);
- con una entrada humana guardada por la API (`pending_command`) → la aplica
  con `Command(resume=...)` y el grafo sigue EXACTAMENTE desde la pausa;
- sin nada pendiente → solo sincroniza.

Persistencia (BRIEFING del README: "el proceso dura días o semanas"):
- Postgres de Supabase si `DATABASE_URL` es Postgres (el mismo
  `open_checkpointer` de las aprobaciones de RFP, con su gotcha del pooler);
- si no, un archivo SQLite (`ONBOARDING_CHECKPOINT_DB`, por defecto
  data/onboarding/checkpoints.sqlite, en .gitignore). Es lo que usa la prueba
  de reinicio entre dos procesos reales.
Un hilo por incorporación (`onboarding-<hire_id>`): incorporaciones en
paralelo nunca comparten estado.

Concurrencia: un `advance` a la vez por incorporación (advisory lock en
Postgres, candado de proceso en SQLite), igual que `ticket_lock` de RFP.
"""

from __future__ import annotations

import logging
import os
import threading
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

from langgraph.types import Command
from sqlalchemy import text
from sqlmodel import Session

from data.pipelines.onboarding_docs import HireProfile
from services.onboarding import access, repository
from services.onboarding.approvals import HireRef
from services.onboarding.catalog import ACTOR_AGENT, COMPLIANCE_STAGE
from services.onboarding.graph import (
    WAIT_APPROVAL,
    WAIT_CHECK,
    WAIT_PAPERWORK,
    compile_onboarding_graph,
    initial_state,
)
from services.onboarding.models import (
    HIRE_ACTIVE,
    HIRE_COMPLETED,
    PHASE_APPROVAL,
    PHASE_CHECK,
    PHASE_COMPLETED,
    PHASE_PAPERWORK,
    OnboardingHire,
    utcnow,
)

logger = logging.getLogger("healthcore.onboarding")

ROOT_DIR = Path(__file__).resolve().parents[2]
THREAD_PREFIX = "onboarding-"
DEFAULT_SQLITE_PATH = ROOT_DIR / "data" / "onboarding" / "checkpoints.sqlite"

PHASE_BY_WAIT = {WAIT_PAPERWORK: PHASE_PAPERWORK, WAIT_CHECK: PHASE_CHECK, WAIT_APPROVAL: PHASE_APPROVAL}


class OnboardingBusyError(RuntimeError):
    """Ya hay una entrada humana esperando a que el worker la aplique."""


class OnboardingStateError(RuntimeError):
    """La entrada no corresponde a lo que el agente está esperando ahora."""


def thread_config(hire_id: Any) -> Dict[str, Any]:
    return {"configurable": {"thread_id": f"{THREAD_PREFIX}{hire_id}"}}


@contextmanager
def open_saver(database_url: Optional[str] = None, sqlite_path: Optional[Path] = None) -> Iterator[Any]:
    url = database_url if database_url is not None else os.getenv("DATABASE_URL", "")
    if url.startswith(("postgresql", "postgres")) and sqlite_path is None:
        from data.pipelines.rfp_intake.checkpointer import open_checkpointer

        with open_checkpointer(url) as saver:
            yield saver
        return
    import sqlite3

    from langgraph.checkpoint.sqlite import SqliteSaver

    path = Path(sqlite_path or os.getenv("ONBOARDING_CHECKPOINT_DB") or DEFAULT_SQLITE_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(str(path), check_same_thread=False)
    try:
        yield SqliteSaver(connection)
    finally:
        connection.close()


_local_locks: Dict[str, threading.Lock] = {}
_local_locks_guard = threading.Lock()


@contextmanager
def hire_lock(engine: Any, hire_id: uuid.UUID) -> Iterator[None]:
    key = f"onboarding:{hire_id}"
    if engine.dialect.name == "postgresql":
        with engine.connect() as connection, connection.begin():
            connection.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"), {"key": key})
            yield
        return
    with _local_locks_guard:
        lock = _local_locks.setdefault(key, threading.Lock())
    with lock:
        yield


def profile_of(hire: OnboardingHire) -> HireProfile:
    return HireProfile(language=hire.language, framework=hire.framework, role_variant=hire.role_variant, department=hire.department)


def hire_ref_of(hire: OnboardingHire) -> HireRef:
    return HireRef(hire_user_id=hire.hire_user_id, department=hire.department, manager_user_id=hire.manager_user_id)


# --- Entradas humanas ---------------------------------------------------------


def submit_command(session: Session, hire: OnboardingHire, command: Dict[str, Any]) -> None:
    """La API guarda la entrada validada; el worker la aplica. Comprueba que el
    agente espera justo eso (mismo tipo, etapa e intento)."""
    if hire.pending_command:
        raise OnboardingBusyError("Ya hay una acción en curso para esta incorporación. Espera unos segundos.")
    waiting = hire.waiting_for or {}
    value = command.get("value", {})
    if waiting.get("kind") != command.get("kind") or waiting.get("stage") != command.get("stage"):
        raise OnboardingStateError("La incorporación no está esperando esa acción ahora mismo.")
    if command["kind"] == WAIT_CHECK and value.get("attempt") != waiting.get("attempt"):
        raise OnboardingStateError("Esas respuestas son de un intento que ya no está abierto.")
    hire.pending_command = command
    hire.updated_at = utcnow()
    session.add(hire)
    session.commit()


# --- Avance -------------------------------------------------------------------


def advance(session: Session, hire_id: uuid.UUID, *, saver: Any, engine: Any = None) -> str:
    """Arranca o reanuda el grafo de una incorporación. Devuelve la fase."""
    engine = engine or session.get_bind()
    with hire_lock(engine, hire_id):
        hire = repository.get_hire(session, hire_id)
        if hire is None:
            raise LookupError(f"Incorporación {hire_id} no encontrada")
        graph = compile_onboarding_graph(saver)
        config = thread_config(hire_id)
        snapshot = graph.get_state(config)
        if not snapshot.values:
            graph.invoke(initial_state(hire_id=str(hire_id), profile=profile_of(hire), hire_ref=hire_ref_of(hire)), config)
            logger.info("onboarding_started onboarding_id=%s", hire_id)
        elif hire.pending_command:
            command = hire.pending_command
            graph.invoke(Command(resume=command.get("value")), config)
            hire.pending_command = None
            logger.info("onboarding_resumed onboarding_id=%s kind=%s stage=%s", hire_id, command.get("kind"), command.get("stage"))
        sync(session, hire, graph.get_state(config))
        return hire.phase


def _waiting_for(snapshot: Any) -> Optional[Dict[str, Any]]:
    for task in snapshot.tasks:
        for pending in getattr(task, "interrupts", ()) or ():
            if isinstance(pending.value, dict):
                return dict(pending.value)
    return None


def sync(session: Session, hire: OnboardingHire, snapshot: Any) -> None:
    """Estado del grafo → fila de la incorporación + efectos nuevos."""
    values = snapshot.values or {}
    waiting = _waiting_for(snapshot)
    hire.waiting_for = waiting
    hire.current_stage = int(values.get("stage", hire.current_stage))
    hire.completed_stages = list(values.get("completed_stages", []))
    hire.attempts = dict(values.get("attempts", {}))
    hire.failed_attempts = dict(values.get("failed_attempts", {}))
    result = values.get("last_result")
    hire.last_check_result = result if result and "score" in result else None
    hire.reinforce_sections = values.get("reinforce_sections")
    hire.rejection = values.get("rejection")
    hire.paperwork = dict(values.get("paperwork", {}))
    # Para la UI: rol y departamento de quien aprobó, nunca su id.
    hire.approvals = {
        stage: {"org_role": info.get("org_role"), "department": info.get("department")}
        for stage, info in (values.get("approvals") or {}).items()
    }
    if str(COMPLIANCE_STAGE) in hire.approvals and hire.compliance_approved_at is None:
        hire.compliance_approved_at = utcnow()
    if values.get("done"):
        hire.phase = PHASE_COMPLETED
        hire.status = HIRE_COMPLETED
        hire.completed_at = hire.completed_at or utcnow()
        hire.waiting_for = None
    elif waiting:
        hire.phase = PHASE_BY_WAIT.get(waiting.get("kind"), hire.phase)
        hire.status = HIRE_ACTIVE
        if waiting.get("stage"):
            hire.current_stage = int(waiting["stage"])
    hire.last_error = None
    hire.updated_at = utcnow()
    session.add(hire)
    session.flush()
    persist_effects(session, hire, list(values.get("effects", [])))
    session.commit()


def persist_effects(session: Session, hire: OnboardingHire, effects: List[Dict[str, Any]]) -> None:
    done = repository.existing_keys(session, [e["id"] for e in effects])
    for effect in effects:
        if effect["id"] in done:
            continue
        kind = effect["type"]
        if kind == "audit":
            repository.record_audit(
                session,
                event_key=effect["id"],
                hire_id=hire.hire_id,
                stage=effect.get("stage"),
                action=effect["action"],
                actor_type=effect["actor_type"],
                actor_user_id=effect.get("actor_user_id"),
                outcome=effect.get("outcome"),
                security_event=bool(effect.get("security_event")),
            )
        elif kind == "notification":
            repository.record_notification(
                session, hire, key=effect["id"], stage=effect.get("stage"), kind=effect["kind"],
                recipient_role=effect["recipient"], params=effect.get("params", {}),
            )
            repository.record_audit(
                session, event_key=effect["id"], hire_id=hire.hire_id, stage=effect.get("stage"),
                action=f"notification_{effect['kind']}", actor_type=ACTOR_AGENT, outcome=effect["recipient"],
            )
        elif kind == "access_request":
            try:
                access.issue_access_request(session, hire, effect["system"], requested_by_type=ACTOR_AGENT)
            except access.AccessBlockedError:
                pass  # ya registrado como evento de seguridad dentro
            repository.record_audit(
                session, event_key=effect["id"], hire_id=hire.hire_id, stage=effect.get("stage"),
                action="access_request_processed", actor_type=ACTOR_AGENT, outcome=effect["system"],
            )
