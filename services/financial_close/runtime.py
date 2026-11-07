"""Identidad del agente, herramientas permitidas, registro de acciones y lectura de solo lectura.

README, "Seguridad y registro":
- El agente opera con identidad propia (`catalog.AGENT_ACTOR`) y acceso de SOLO
  LECTURA a los datos financieros: `read_only_session` abre una transacción
  que la propia base de datos impide escribir (Postgres `SET TRANSACTION READ
  ONLY`, SQLite `PRAGMA query_only`).
- Cada acción queda registrada como acción de un proceso automatizado
  (`actor_type = automated_process`). Las decisiones de personas llevan
  `human:<seudónimo>` y `actor_type = human`.

`AgentRuntime.call` es el único camino por el que el agente usa una
herramienta: si no está en `catalog.AGENT_TOOLS` se rechaza (`denied`).
"""

from __future__ import annotations

import hashlib
import json
import logging
import uuid
from contextlib import contextmanager
from datetime import date
from typing import Any, Callable, Dict, Iterator, List, Optional

from sqlalchemy import text
from sqlmodel import Session

from services.financial_close.catalog import AGENT_ACTOR, AGENT_TOOLS
from services.financial_close.models import FcAgentAction

logger = logging.getLogger("healthcore.financial_close.actions")

AUTOMATED = "automated_process"
HUMAN = "human"


def digest(value: str) -> str:
    return hashlib.sha256((value or "").encode("utf-8")).hexdigest()


def human_actor(user_id: Optional[str]) -> str:
    """Seudónimo estable de una persona: nunca su id ni su email en claro."""
    return "human:" + digest(user_id or "anonymous")[:12]


class ActionLog:
    """Acciones de una ejecución. Se escriben en `fc_agent_actions` con `flush`
    y, en el momento, como una línea JSON en el logger."""

    def __init__(self, request_id: Optional[str] = None) -> None:
        self.request_id = request_id or uuid.uuid4().hex
        self.actions: List[FcAgentAction] = []

    def record(self, actor: str, action: str, outcome: str, tool: Optional[str] = None, **details: Any) -> None:
        actor_type = HUMAN if actor.startswith("human:") else AUTOMATED
        safe = {k: v for k, v in details.items() if v is not None}
        self.actions.append(FcAgentAction(request_id=self.request_id, actor=actor, actor_type=actor_type,
                                          action=action, tool=tool, outcome=outcome, details=safe or None))
        logger.info(json.dumps({"request_id": self.request_id, "actor": actor, "actor_type": actor_type,
                                "action": action, "tool": tool, "outcome": outcome, **safe},
                               default=str, sort_keys=True))

    def flush(self, session: Session) -> None:
        from services.financial_close import repository

        if self.actions:
            repository.add_actions(session, self.actions)
            self.actions = []


class ToolNotAllowed(PermissionError):
    pass


class AgentRuntime:
    def __init__(self, log: ActionLog, actor: str = AGENT_ACTOR) -> None:
        self.actor = actor
        self.log = log

    def call(self, tool: str, fn: Callable[..., Any], *args: Any, audit: Optional[Dict[str, Any]] = None,
             **kwargs: Any) -> Any:
        audit = audit or {}
        if tool not in AGENT_TOOLS:
            self.log.record(self.actor, "tool_call", "denied", tool=tool, **audit)
            raise ToolNotAllowed(f"{self.actor} no tiene la herramienta {tool}")
        try:
            result = fn(*args, **kwargs)
        except Exception as exc:
            self.log.record(self.actor, "tool_call", "error", tool=tool, error_type=type(exc).__name__, **audit)
            raise
        self.log.record(self.actor, "tool_call", "ok", tool=tool, **audit)
        return result


@contextmanager
def read_only_session(engine) -> Iterator[Session]:
    """Sesión que la base de datos impide escribir.

    Postgres: `SET TRANSACTION READ ONLY` (cualquier INSERT/UPDATE falla con
    `ReadOnlySqlTransaction`). SQLite: `PRAGMA query_only = ON` en la conexión,
    restaurado al salir (en tests la conexión es compartida)."""
    with Session(engine) as session:
        dialect = engine.dialect.name
        if dialect == "postgresql":
            session.exec(text("SET TRANSACTION READ ONLY"))
        elif dialect == "sqlite":
            session.exec(text("PRAGMA query_only = ON"))
        try:
            yield session
        finally:
            session.rollback()
            if dialect == "sqlite":
                session.exec(text("PRAGMA query_only = OFF"))
                session.commit()


def read_period_data(engine, period: date):
    """Herramienta `read_period_data`: el periodo ingerido, en solo lectura."""
    from services.financial_close import repository

    with read_only_session(engine) as session:
        return repository.load_period(session, period)
