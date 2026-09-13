"""Contrato de GET /tasks/{task_id} (docs/serialization-audit.md: un
esquema explícito por endpoint, barrido por tests/test_serialization.py)."""

from __future__ import annotations

from typing import Any, Dict, Literal, Optional

from pydantic import BaseModel

# Los cuatro estados que pide el ticket, en minúsculas. Los estados internos
# de Celery (RETRY, RECEIVED, REVOKED...) se traducen a uno de estos en
# services/tasks/status.py::to_public_status.
PublicTaskStatus = Literal["pending", "started", "success", "failure"]


class TaskError(BaseModel):
    type: str
    message: str


class TaskStatusResponse(BaseModel):
    task_id: str
    status: PublicTaskStatus
    # Resumen que devuelve la tarea al terminar bien (conteos, avisos, run_id).
    result: Optional[Dict[str, Any]] = None
    # Último error: el definitivo en `failure`, o el del intento anterior si
    # la tarea está esperando un reintento.
    error: Optional[TaskError] = None
