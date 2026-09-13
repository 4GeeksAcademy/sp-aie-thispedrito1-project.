"""Lectura del estado de una tarea desde el result backend (Redis)."""

from __future__ import annotations

from typing import Any, Dict

from celery import states
from celery.result import AsyncResult

from services.celery_app import celery_app
from services.tasks.redaction import describe_error
from services.tasks.schemas import PublicTaskStatus


# Criterio: el cliente que consulta en bucle solo debe dejar de preguntar
# cuando la tarea ya no va a cambiar (success/failure).
_PUBLIC_STATUS_BY_CELERY_STATE: Dict[str, PublicTaskStatus] = {
    states.PENDING: "pending",
    # Recibida pero sin empezar: para el cliente sigue siendo "en cola".
    states.RECEIVED: "pending",
    states.STARTED: "started",
    # Esperando un reintento: el trabajo ya empezó y no ha terminado. Nunca
    # `failure`, que haría parar al cliente justo antes de un posible éxito;
    # el error del intento anterior viaja aparte en el campo `error`.
    states.RETRY: "started",
    states.SUCCESS: "success",
    states.FAILURE: "failure",
    # No se va a ejecutar nunca: si no fuera `failure`, el cliente esperaría
    # para siempre una tarea muerta.
    states.REVOKED: "failure",
    states.REJECTED: "failure",
    states.IGNORED: "failure",
}


def to_public_status(celery_state: str) -> PublicTaskStatus:
    """Traduce un estado interno de Celery a uno de los cuatro del ticket.

    Estados que Celery puede devolver (celery.states):
      PENDING   desconocido o aún en cola (Celery no distingue ambos casos)
      RECEIVED  un worker ya tiene el mensaje, pero no ha empezado
      STARTED   ejecutándose (requiere task_track_started=True, ya activo)
      RETRY     falló un intento y espera su reintento con backoff
      SUCCESS   terminó bien
      FAILURE   falló definitivamente (ya está en la DLQ)
      REVOKED   cancelada a mano (p. ej. desde Flower)
      REJECTED / IGNORED  descartada por el worker
    """
    # Un estado desconocido (p. ej. de una versión futura de Celery) cae en
    # `pending` en vez de reventar la respuesta con un valor fuera del contrato.
    return _PUBLIC_STATUS_BY_CELERY_STATE.get(celery_state, "pending")


def read_task_snapshot(task_id: str) -> Dict[str, Any]:
    """Estado actual de una tarea. Lanza las excepciones de conexión de
    Redis tal cual: el router las convierte en 503."""
    async_result = AsyncResult(task_id, app=celery_app)
    celery_state = async_result.state
    payload: Dict[str, Any] = {
        "task_id": task_id,
        "status": to_public_status(celery_state),
        "result": None,
        "error": None,
    }
    outcome = async_result.result
    if celery_state == states.SUCCESS:
        payload["result"] = outcome if isinstance(outcome, dict) else {"value": outcome}
    elif isinstance(outcome, BaseException):
        # FAILURE (error definitivo) o RETRY (error del intento anterior).
        payload["error"] = {"type": type(outcome).__name__, "message": describe_error(outcome)}
    return payload
