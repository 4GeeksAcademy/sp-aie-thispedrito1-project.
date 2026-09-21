"""Cola de tareas asíncronas (Ticket #DEV-55): tarea de Celery, DLQ y
GET /tasks/{task_id}.

Sin Redis ni worker: la tarea se ejecuta con `apply()`, el modo síncrono de
Celery, que respeta autoretry/max_retries (ejecuta cada reintento en el acto,
sin esperar el countdown). El flow de Prefect se sustituye por uno falso: aquí
se prueba la capa de cola, no el pipeline (eso vive en test_business_pipeline).
"""

from __future__ import annotations

import logging
import uuid
from datetime import date

import pytest
from celery import states
from celery.utils.time import get_exponential_backoff_interval
from fastapi.testclient import TestClient
from kombu.exceptions import OperationalError
from sqlmodel import Session, select

from data.pipelines import pipeline
from data.pipelines.monthly_clinic_supply_performance import run_log
from data.pipelines.monthly_clinic_supply_performance.models import PipelineRun
from services.celery_app import celery_app
from services.tasks import dead_letters, pipeline_tasks, status
from services.tasks.dead_letters import TaskDeadLetter
from services.tasks.pipeline_tasks import run_monthly_clinic_supply_performance as pipeline_task

AUGUST = "2026-08-01"


@pytest.fixture()
def task_engine(inventory_engine):
    pipeline_tasks.use_engine(inventory_engine)
    yield inventory_engine
    pipeline_tasks.use_engine(None)


@pytest.fixture()
def fake_flow(monkeypatch):
    """Sustituye el flow real. `outcomes` es la lista de resultados por
    ejecución: una excepción se lanza, cualquier otro valor se devuelve."""
    calls = []

    def install(*outcomes):
        pending = list(outcomes)

        def flow(**kwargs):
            calls.append(kwargs)
            outcome = pending.pop(0) if len(pending) > 1 else pending[0]
            if isinstance(outcome, BaseException):
                raise outcome
            return outcome

        monkeypatch.setattr(pipeline, "monthly_clinic_supply_performance_flow", flow)
        return calls

    return install


def _run(task_id: str = None, run_id: str = None):
    return pipeline_task.apply(
        kwargs={"month_start": AUGUST, "run_id": run_id, "triggered_by": "user-1"},
        task_id=task_id or str(uuid.uuid4()),
    )


def _dead_letters(engine):
    dead_letters.ensure_dead_letter_table(engine)
    with Session(engine) as session:
        return session.exec(select(TaskDeadLetter)).all()


# --- Configuración: lo que evalúa el ticket ---------------------------------


def test_retry_policy_is_three_retries_with_growing_backoff_and_no_jitter():
    assert pipeline_task.max_retries == 3
    assert pipeline_task.retry_jitter is False  # con jitter, un reintento podría salir a los 0 s
    waits = [
        get_exponential_backoff_interval(
            factor=pipeline_task.retry_backoff, retries=retry, maximum=pipeline_task.retry_backoff_max
        )
        for retry in range(pipeline_task.max_retries)
    ]
    assert waits == [10, 20, 40]


def test_broker_config_acks_late_tracks_started_and_limits_execution_time():
    conf = celery_app.conf
    assert conf.task_acks_late is True  # ACK solo tras éxito
    assert conf.task_track_started is True  # sin esto nunca se vería `started`
    assert conf.worker_prefetch_multiplier == 1
    assert 0 < conf.task_soft_time_limit < conf.task_time_limit
    # Redis reentregaría una tarea sana si tardara más que la visibilidad.
    assert conf.broker_transport_options["visibility_timeout"] > conf.task_time_limit
    assert conf.accept_content == ["json"]


# --- Ejecución de la tarea --------------------------------------------------


def test_task_succeeds_on_first_attempt_using_the_run_created_by_the_api(task_engine, fake_flow):
    calls = fake_flow({"run_id": "run-1", "status": "completed"})

    result = _run(run_id="run-1")

    assert result.state == states.SUCCESS
    assert result.result == {"run_id": "run-1", "status": "completed"}
    assert calls == [{"month_start": date(2026, 8, 1), "trigger_type": "manual", "triggered_by": "user-1", "run_id": "run-1"}]
    assert _dead_letters(task_engine) == []


def test_task_fails_four_times_then_lands_in_dlq_with_attempt_and_error(task_engine, fake_flow):
    calls = fake_flow(RuntimeError("pooler closed the connection"))
    task_id = str(uuid.uuid4())

    result = _run(task_id=task_id, run_id="run-from-api")

    assert result.state == states.FAILURE
    assert len(calls) == 4  # 1 ejecución + max_retries=3
    # Solo el primer intento reutiliza la fila de la API; cada reintento crea la suya.
    assert [call["run_id"] for call in calls] == ["run-from-api", None, None, None]
    [dead] = _dead_letters(task_engine)
    assert dead.task_id == task_id
    assert dead.task_name == "reporting.run_monthly_clinic_supply_performance"
    assert dead.attempt == 4
    assert dead.error_type == "RuntimeError"
    assert dead.error_message == "pooler closed the connection"
    assert dead.failed_at is not None
    assert dead.task_kwargs == {"month_start": AUGUST, "run_id": "run-from-api", "triggered_by": "user-1"}


def test_task_that_recovers_on_a_retry_is_not_dead_lettered(task_engine, fake_flow):
    calls = fake_flow(RuntimeError("transient"), {"status": "completed"})

    result = _run()

    assert result.state == states.SUCCESS
    assert len(calls) == 2
    assert _dead_letters(task_engine) == []


def test_soft_time_limit_goes_straight_to_dlq_without_retries(task_engine, fake_flow):
    from celery.exceptions import SoftTimeLimitExceeded

    calls = fake_flow(SoftTimeLimitExceeded("15 min"))

    result = _run()

    assert result.state == states.FAILURE
    assert len(calls) == 1
    assert [dead.attempt for dead in _dead_letters(task_engine)] == [1]


def test_cancelled_flow_is_a_successful_task_not_a_retry(task_engine, fake_flow):
    class Cancelled:
        message = "window_locked: active run 123"

    calls = fake_flow(Cancelled())

    result = _run()

    assert result.state == states.SUCCESS
    assert result.result == {"status": "cancelled", "month_start": AUGUST, "reason": "window_locked: active run 123"}
    assert len(calls) == 1


def test_first_attempt_failure_releases_the_month_lock_held_by_the_queued_run(task_engine, fake_flow):
    """Si el flow muere tras crear la fila `queued` pero antes de registrar
    su fallo, retendría el mes 30 minutos y los reintentos saldrían cancelados."""
    with Session(task_engine) as session:
        run = run_log.create_queued_run(session, month_start=date(2026, 8, 1), trigger_type="manual")
        run_id = str(run.run_id)
    fake_flow(ConnectionError("supabase unreachable"))

    _run(run_id=run_id)

    with Session(task_engine) as session:
        released = session.exec(select(PipelineRun).where(PipelineRun.run_id == uuid.UUID(run_id))).one()
        assert released.status == "failed"
        assert released.error_type == "ConnectionError"
        assert run_log.get_active_run(session, date(2026, 8, 1)) is None


def test_dead_letter_is_recorded_once_per_task_id(task_engine):
    first = dead_letters.record_dead_letter(task_engine, task_id="t-1", task_name="x", attempt=4, error=ValueError("a"))
    second = dead_letters.record_dead_letter(task_engine, task_id="t-1", task_name="x", attempt=4, error=ValueError("a"))

    assert (first, second) == (True, False)
    assert len(_dead_letters(task_engine)) == 1


def test_dlq_write_failure_is_logged_and_never_raises(task_engine, fake_flow, monkeypatch, caplog):
    def broken(*args, **kwargs):
        raise OperationalError("database is down")

    monkeypatch.setattr(pipeline_tasks, "record_dead_letter", broken)
    fake_flow(RuntimeError("boom"))
    task_id = str(uuid.uuid4())

    with caplog.at_level(logging.CRITICAL, logger="healthcore.tasks"):
        result = _run(task_id=task_id)

    assert result.state == states.FAILURE
    assert any("dead_letter_write_failed" in r.getMessage() and task_id in r.getMessage() for r in caplog.records)


# --- Observabilidad ---------------------------------------------------------


def test_each_execution_logs_task_id_attempt_status_and_duration(task_engine, fake_flow, caplog):
    fake_flow(RuntimeError("first attempt fails"), {"status": "completed"})
    task_id = str(uuid.uuid4())

    with caplog.at_level(logging.INFO, logger="healthcore.tasks"):
        _run(task_id=task_id)

    lines = [r.getMessage() for r in caplog.records if r.name == "healthcore.tasks"]
    retry = next(line for line in lines if line.startswith("task_retry"))
    success = next(line for line in lines if line.startswith("task_succeeded"))
    for fragment in (f"task_id={task_id}", "attempt=1", "status=retry", "duration_ms=", "next_retry_in_s=10", "error=first attempt fails"):
        assert fragment in retry
    for fragment in (f"task_id={task_id}", "attempt=2", "status=success", "duration_ms="):
        assert fragment in success


def test_error_emails_are_masked_in_our_logs_celery_logs_and_the_dlq(task_engine, fake_flow, caplog):
    fake_flow(RuntimeError("could not notify ana.perez@healthcore.com"))

    with caplog.at_level(logging.INFO):
        _run()

    rendered = "\n".join(
        logging.Formatter("%(message)s").format(record) for record in caplog.records
    )
    assert "ana.perez@healthcore.com" not in rendered
    assert "could not notify <email>" in rendered
    # El logger interno de Celery conserva el traceback, ya enmascarado.
    assert any(r.name == "celery.app.trace" and r.exc_text for r in caplog.records)
    assert _dead_letters(task_engine)[0].error_message == "could not notify <email>"


# --- Productor: POST /reporting/pipeline-runs --------------------------------


def test_trigger_returns_202_with_task_id_and_a_message_with_identifiers_only(
    client: TestClient, admin_headers, monkeypatch
):
    published = []

    class FakeAsyncResult:
        id = "8d1e0f7a-2b7c-4d7e-9f10-5c7f9e3b2a01"

    def fake_apply_async(*, kwargs, retry):
        published.append({"kwargs": kwargs, "retry": retry})
        return FakeAsyncResult()

    monkeypatch.setattr(pipeline_task, "apply_async", fake_apply_async)

    response = client.post("/reporting/pipeline-runs", json={"month_start": AUGUST}, headers=admin_headers)

    assert response.status_code == 202
    assert response.json()["task_id"] == FakeAsyncResult.id
    [message] = published
    assert message["retry"] is False  # sin Redis, falla ya en vez de colgar la petición
    assert set(message["kwargs"]) == {"month_start", "run_id", "triggered_by"}
    assert message["kwargs"]["run_id"] == response.json()["run_id"]
    # Regla 1 del patrón: solo identificadores cortos, nunca eventos ni lotes.
    assert all(isinstance(value, str) and len(value) <= 64 for value in message["kwargs"].values())


def test_trigger_returns_503_and_writes_nothing_when_broker_is_down(
    client: TestClient, admin_headers, inventory_engine, monkeypatch
):
    def broker_down(**kwargs):
        raise OperationalError("Error 61 connecting to localhost:6379. Connection refused.")

    monkeypatch.setattr(pipeline_task, "apply_async", broker_down)

    response = client.post("/reporting/pipeline-runs", json={"month_start": AUGUST}, headers=admin_headers)

    assert response.status_code == 503
    assert response.json() == {"detail": "Task queue unavailable. Try again later."}
    with Session(inventory_engine) as session:
        # La petición solo lee: sin broker no queda ni fila ni lock retenido,
        # así que el reintento del cliente no choca con un 409.
        assert session.exec(select(PipelineRun)).all() == []
    assert client.post("/reporting/pipeline-runs", json={"month_start": AUGUST}, headers=admin_headers).status_code == 503


# --- Consulta: GET /tasks/{task_id} -----------------------------------------


def _fake_backend(monkeypatch, state, result=None):
    class FakeAsyncResult:
        def __init__(self, task_id, app):
            self.id = task_id
            self.state = state
            self.result = result

    monkeypatch.setattr(status, "AsyncResult", FakeAsyncResult)


TASK_ID = "3b9a6f3e-1c2d-4e5f-8a9b-0c1d2e3f4a5b"


def test_task_status_requires_admin_and_a_uuid(client: TestClient, auth_headers, admin_headers):
    assert client.get(f"/tasks/{TASK_ID}").status_code == 401
    assert client.get(f"/tasks/{TASK_ID}", headers=auth_headers).status_code == 403
    assert client.get("/tasks/not-a-task-id", headers=admin_headers).status_code == 422


def test_task_status_success_returns_the_result(client: TestClient, admin_headers, monkeypatch):
    _fake_backend(monkeypatch, states.SUCCESS, {"run_id": "run-1", "status": "completed"})

    response = client.get(f"/tasks/{TASK_ID}", headers=admin_headers)

    assert response.status_code == 200
    assert response.json() == {
        "task_id": TASK_ID,
        "status": "success",
        "result": {"run_id": "run-1", "status": "completed"},
        "error": None,
    }


def test_task_status_failure_returns_masked_error_and_no_result(client: TestClient, admin_headers, monkeypatch):
    _fake_backend(monkeypatch, states.FAILURE, RuntimeError("mail to ana@healthcore.com failed"))

    body = client.get(f"/tasks/{TASK_ID}", headers=admin_headers).json()

    assert body["status"] == "failure"
    assert body["result"] is None
    assert body["error"] == {"type": "RuntimeError", "message": "mail to <email> failed"}


@pytest.mark.parametrize(
    ("celery_state", "expected"),
    [(states.PENDING, "pending"), (states.STARTED, "started"), (states.SUCCESS, "success"), (states.FAILURE, "failure")],
)
def test_the_four_ticket_states_map_to_themselves(celery_state, expected):
    assert status.to_public_status(celery_state) == expected


@pytest.mark.parametrize(
    ("celery_state", "expected"),
    [
        (states.RECEIVED, "pending"),
        (states.RETRY, "started"),  # nunca failure: aún puede terminar bien
        (states.REVOKED, "failure"),  # no se ejecutará nunca: el cliente debe dejar de esperar
        (states.REJECTED, "failure"),
        (states.IGNORED, "failure"),
        ("SOME_FUTURE_STATE", "pending"),  # nunca un valor fuera del contrato
    ],
)
def test_intermediate_and_terminal_celery_states_map_to_the_four_ticket_states(celery_state, expected):
    assert status.to_public_status(celery_state) == expected


def test_task_waiting_for_retry_reports_started_with_the_previous_error(client: TestClient, admin_headers, monkeypatch):
    _fake_backend(monkeypatch, states.RETRY, ConnectionError("pooler closed the connection"))

    body = client.get(f"/tasks/{TASK_ID}", headers=admin_headers).json()

    assert body == {
        "task_id": TASK_ID,
        "status": "started",
        "result": None,
        "error": {"type": "ConnectionError", "message": "pooler closed the connection"},
    }


def test_task_status_returns_503_when_result_backend_is_down(client: TestClient, admin_headers, monkeypatch):
    from redis.exceptions import ConnectionError as RedisConnectionError

    class DownAsyncResult:
        def __init__(self, task_id, app):
            pass

        @property
        def state(self):
            raise RedisConnectionError("Connection refused")

    monkeypatch.setattr(status, "AsyncResult", DownAsyncResult)

    response = client.get(f"/tasks/{TASK_ID}", headers=admin_headers)

    assert response.status_code == 503
    assert response.json() == {"detail": "Task backend unavailable. Try again later."}
