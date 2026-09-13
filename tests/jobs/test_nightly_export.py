"""Tests del job nocturno (Ticket #DEV-53): máquina de estados, lock vía
`processing`, idempotencia por target_date, backup CSV y logs."""

from __future__ import annotations

import csv
import logging
import subprocess
import sys
import textwrap
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlmodel import Session

from services.job_runner import (
    CANCELLED_PREFIX,
    InvalidTransition,
    JobRun,
    ProcessingLockBusy,
    create_run,
    finish_run,
    list_runs,
    mark_processing,
)
from telemetry_models import TelemetryEventRecord

ROOT_DIR = Path(__file__).resolve().parents[2]
SCRIPT_PATH = ROOT_DIR / "scripts" / "nightly_export.py"
TARGET = date(2026, 9, 12)
JOB = "nightly_export"


def pipeline_calls(counter: Path) -> int:
    return len(counter.read_text()) if counter.exists() else 0


def seed_events(engine) -> None:
    """2 eventos dentro del día objetivo (UTC) y 2 fuera, en los bordes."""
    with Session(engine) as session:
        session.add_all(
            [
                TelemetryEventRecord(id="before", timestamp=datetime(2026, 9, 11, 23, 59, 59),
                                     service="backoffice", event_type="page_viewed", tags={}),
                TelemetryEventRecord(id="first", timestamp=datetime(2026, 9, 12, 0, 0, 0),
                                     service="backoffice", event_type="page_viewed",
                                     tags={"eventId": "e1", "path": "/inventory"}),
                TelemetryEventRecord(id="last", timestamp=datetime(2026, 9, 12, 23, 59, 59, 999000),
                                     service="api", event_type="login_failed", level="warning",
                                     value=1.5, message="nota", tags={"eventId": "e2"}),
                TelemetryEventRecord(id="after", timestamp=datetime(2026, 9, 13, 0, 0, 0),
                                     service="backoffice", event_type="page_viewed", tags={}),
            ]
        )
        session.commit()


def runs(engine, target_date=None) -> list[JobRun]:
    with Session(engine) as session:
        return list_runs(session, JOB, target_date)


def active_runs(engine) -> list[JobRun]:
    return [run for run in runs(engine) if run.status in ("pending", "processing")]


def insert_run(engine, *, status: str, target_date: date = TARGET, age: timedelta = timedelta(0)) -> JobRun:
    moment = datetime.now(timezone.utc) - age
    with Session(engine) as session:
        run = JobRun(job_name=JOB, target_date=target_date, status=status, created_at=moment,
                     started_at=moment if status == "processing" else None)
        session.add(run)
        session.commit()
        session.refresh(run)
        return run


# --- Fecha objetivo ------------------------------------------------------------


def test_target_date_defaults_to_yesterday_utc(nightly_export):
    assert nightly_export.resolve_target_date(env={}, today=date(2026, 9, 13)) == TARGET
    assert nightly_export.resolve_target_date(env={"TARGET_DATE": "  "}, today=date(2026, 3, 1)) == date(2026, 2, 28)


def test_target_date_env_override(nightly_export):
    assert nightly_export.resolve_target_date(env={"TARGET_DATE": "2026-08-20"}, today=date(2026, 9, 13)) == date(2026, 8, 20)


@pytest.mark.parametrize("raw", ["20-08-2026", "2026-13-01", "ayer"])
def test_target_date_rejects_bad_format(nightly_export, raw):
    with pytest.raises(nightly_export.TargetDateError, match="YYYY-MM-DD"):
        nightly_export.resolve_target_date(env={"TARGET_DATE": raw}, today=date(2026, 9, 13))


@pytest.mark.parametrize("raw", ["2026-09-13", "2026-12-01"])
def test_target_date_rejects_days_not_closed(nightly_export, raw):
    with pytest.raises(nightly_export.TargetDateError, match="no es un día cerrado"):
        nightly_export.resolve_target_date(env={"TARGET_DATE": raw}, today=date(2026, 9, 13))


# --- Camino feliz e idempotencia -----------------------------------------------


def test_happy_path_exports_csv_runs_pipeline_and_completes(nightly_export, engine, tmp_path, ok_pipeline, pipeline_counter):
    seed_events(engine)

    outcome = nightly_export.run_nightly_export(engine, TARGET, raw_dir=tmp_path, pipeline_command=ok_pipeline)

    assert outcome == "completed"
    csv_path = tmp_path / "telemetry_2026-09-12.csv"
    with csv_path.open(encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    assert [row["id"] for row in rows] == ["first", "last"]  # bordes del día UTC
    assert rows[1]["timestamp"] == "2026-09-12T23:59:59.999000"
    assert rows[1]["tags"] == '{"eventId": "e2"}'
    assert rows[0]["value"] == "" and rows[1]["value"] == "1.5"
    assert not list(tmp_path.glob(".*.tmp"))
    assert pipeline_calls(pipeline_counter) == 1

    [run] = runs(engine)
    assert run.status == "completed"
    assert run.target_date == TARGET
    assert run.started_at is not None and run.finished_at is not None and run.created_at is not None
    assert run.error_message is None
    assert run.rows_exported == 2
    assert run.pipeline_exit_code == 0


def test_second_run_same_day_is_skipped_without_duplicates(nightly_export, engine, tmp_path, ok_pipeline, pipeline_counter):
    seed_events(engine)
    nightly_export.run_nightly_export(engine, TARGET, raw_dir=tmp_path, pipeline_command=ok_pipeline)
    csv_path = tmp_path / "telemetry_2026-09-12.csv"
    first_content = csv_path.read_bytes()

    outcome = nightly_export.run_nightly_export(engine, TARGET, raw_dir=tmp_path, pipeline_command=ok_pipeline)

    assert outcome == "skipped"
    assert pipeline_calls(pipeline_counter) == 1
    assert csv_path.read_bytes() == first_content
    assert [run.status for run in runs(engine)] == ["completed"]


def test_other_day_is_not_blocked_by_a_completed_day(nightly_export, engine, tmp_path, ok_pipeline, pipeline_counter):
    nightly_export.run_nightly_export(engine, TARGET, raw_dir=tmp_path, pipeline_command=ok_pipeline)
    outcome = nightly_export.run_nightly_export(engine, date(2026, 9, 11), raw_dir=tmp_path, pipeline_command=ok_pipeline)

    assert outcome == "completed"
    assert pipeline_calls(pipeline_counter) == 2
    assert (tmp_path / "telemetry_2026-09-11.csv").exists()


def test_existing_csv_is_not_overwritten_but_pipeline_still_runs(nightly_export, engine, tmp_path, ok_pipeline, pipeline_counter):
    seed_events(engine)
    csv_path = tmp_path / "telemetry_2026-09-12.csv"
    csv_path.write_text("backup previo\n")

    outcome = nightly_export.run_nightly_export(engine, TARGET, raw_dir=tmp_path, pipeline_command=ok_pipeline)

    assert outcome == "completed"
    assert csv_path.read_text() == "backup previo\n"
    assert pipeline_calls(pipeline_counter) == 1
    [run] = runs(engine)
    assert run.rows_exported is None


def test_empty_day_still_writes_header_only_csv(nightly_export, engine, tmp_path, ok_pipeline):
    nightly_export.run_nightly_export(engine, TARGET, raw_dir=tmp_path, pipeline_command=ok_pipeline)

    assert (tmp_path / "telemetry_2026-09-12.csv").read_text().strip() == ",".join(nightly_export.CSV_COLUMNS)
    assert runs(engine)[0].rows_exported == 0


# --- Fallos: nunca processing ----------------------------------------------------


def test_pipeline_failure_marks_failed_with_message(nightly_export, engine, tmp_path):
    failing = (sys.executable, "-c", "import sys; sys.stderr.write('boom para ana@example.com\\n'); sys.exit(3)")

    with pytest.raises(nightly_export.PipelineFailedError):
        nightly_export.run_nightly_export(engine, TARGET, raw_dir=tmp_path, pipeline_command=failing)

    [run] = runs(engine)
    assert run.status == "failed"
    assert run.finished_at is not None
    assert run.pipeline_exit_code == 3
    assert run.rows_exported == 0  # el CSV sí se exportó antes del fallo
    assert "PipelineFailedError" in run.error_message and "código 3" in run.error_message
    assert "ana@example.com" not in run.error_message and "<email>" in run.error_message
    assert active_runs(engine) == []


def test_unexpected_exception_during_export_marks_failed(nightly_export, engine, tmp_path, ok_pipeline, pipeline_counter, monkeypatch):
    def broken_export(*_args, **_kwargs):
        raise RuntimeError("disco lleno")

    monkeypatch.setattr(nightly_export, "export_telemetry_csv", broken_export)

    with pytest.raises(RuntimeError, match="disco lleno"):
        nightly_export.run_nightly_export(engine, TARGET, raw_dir=tmp_path, pipeline_command=ok_pipeline)

    [run] = runs(engine)
    assert run.status == "failed"
    assert run.error_message == "RuntimeError: disco lleno"
    assert pipeline_calls(pipeline_counter) == 0


def test_interrupt_marks_failed_too(nightly_export, engine, tmp_path, ok_pipeline, monkeypatch):
    """KeyboardInterrupt/SIGTERM no heredan de Exception: el finally cubre igual."""

    def interrupted(*_args, **_kwargs):
        raise nightly_export.JobTerminatedError("señal 15 recibida")

    monkeypatch.setattr(nightly_export, "run_pipeline", interrupted)
    with pytest.raises(nightly_export.JobTerminatedError):
        nightly_export.run_nightly_export(engine, TARGET, raw_dir=tmp_path, pipeline_command=ok_pipeline)

    def ctrl_c(*_args, **_kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr(nightly_export, "run_pipeline", ctrl_c)
    with pytest.raises(KeyboardInterrupt):
        nightly_export.run_nightly_export(engine, date(2026, 9, 11), raw_dir=tmp_path, pipeline_command=ok_pipeline)

    assert [run.status for run in runs(engine)] == ["failed", "failed"]
    assert active_runs(engine) == []


def test_pipeline_timeout_marks_failed(nightly_export, engine, tmp_path):
    slow = (sys.executable, "-c", "import time; time.sleep(10)")

    with pytest.raises(subprocess.TimeoutExpired):
        nightly_export.run_nightly_export(engine, TARGET, raw_dir=tmp_path, pipeline_command=slow, pipeline_timeout=0.5)

    [run] = runs(engine)
    assert run.status == "failed" and "TimeoutExpired" in run.error_message


def test_retry_after_failure_completes_without_reexporting(nightly_export, engine, tmp_path, ok_pipeline, pipeline_counter):
    seed_events(engine)
    failing = (sys.executable, "-c", "import sys; sys.exit(1)")
    with pytest.raises(nightly_export.PipelineFailedError):
        nightly_export.run_nightly_export(engine, TARGET, raw_dir=tmp_path, pipeline_command=failing)

    outcome = nightly_export.run_nightly_export(engine, TARGET, raw_dir=tmp_path, pipeline_command=ok_pipeline)

    assert outcome == "completed"
    assert [run.status for run in runs(engine)] == ["failed", "completed"]
    assert runs(engine)[1].rows_exported is None  # el CSV del primer intento se reutiliza
    assert pipeline_calls(pipeline_counter) == 1


def test_state_not_recorded_is_reported(nightly_export, engine, tmp_path, ok_pipeline, monkeypatch):
    def broken_finish(*_args, **_kwargs):
        raise ConnectionError("se cayó la base de datos")

    monkeypatch.setattr(nightly_export, "finish_run", broken_finish)
    with pytest.raises(nightly_export.JobStateNotRecordedError):
        nightly_export.run_nightly_export(engine, TARGET, raw_dir=tmp_path, pipeline_command=ok_pipeline)


# --- Lock vía processing -----------------------------------------------------------


def test_active_processing_row_cancels_second_instance(nightly_export, engine, tmp_path, ok_pipeline, pipeline_counter):
    holder = insert_run(engine, status="processing", target_date=date(2026, 9, 11))

    outcome = nightly_export.run_nightly_export(engine, TARGET, raw_dir=tmp_path, pipeline_command=ok_pipeline)

    assert outcome == "cancelled"
    assert pipeline_calls(pipeline_counter) == 0
    assert not (tmp_path / "telemetry_2026-09-12.csv").exists()
    assert [run.id for run in runs(engine)] == [holder.id]  # ninguna fila nueva


def test_lost_race_for_the_lock_does_no_work_and_leaves_no_active_row(nightly_export, engine, tmp_path, ok_pipeline, pipeline_counter, monkeypatch):
    """Dos instancias pasan la comprobación a la vez; la otra toma el lock
    primero. Se simula haciendo que la consulta previa no la vea."""
    holder = insert_run(engine, status="processing")
    monkeypatch.setattr(nightly_export, "has_processing_lock", lambda *_args: False)

    outcome = nightly_export.run_nightly_export(engine, TARGET, raw_dir=tmp_path, pipeline_command=ok_pipeline)

    assert outcome == "cancelled"
    assert pipeline_calls(pipeline_counter) == 0
    assert not (tmp_path / "telemetry_2026-09-12.csv").exists()
    assert [run.id for run in active_runs(engine)] == [holder.id]
    [loser] = [run for run in runs(engine) if run.id != holder.id]
    assert loser.status == "failed"
    assert loser.error_message.startswith(CANCELLED_PREFIX)
    assert loser.started_at is None  # nunca llegó a processing


def test_day_completed_while_taking_the_lock_is_skipped(nightly_export, engine, tmp_path, ok_pipeline, pipeline_counter, monkeypatch):
    finished = insert_run(engine, status="completed")
    answers = iter([False])  # la primera consulta (sin lock) no lo ve
    real_check = nightly_export.has_completed_for_date

    def racy_check(session, job_name, target_date, exclude_run_id=None):
        if exclude_run_id is None:
            return next(answers)
        return real_check(session, job_name, target_date, exclude_run_id=exclude_run_id)

    monkeypatch.setattr(nightly_export, "has_completed_for_date", racy_check)

    outcome = nightly_export.run_nightly_export(engine, TARGET, raw_dir=tmp_path, pipeline_command=ok_pipeline)

    assert outcome == "skipped"
    assert pipeline_calls(pipeline_counter) == 0
    assert active_runs(engine) == []
    assert [run.id for run in runs(engine) if run.status == "completed"] == [finished.id]
    [cancelled] = [run for run in runs(engine) if run.id != finished.id]
    assert cancelled.status == "failed" and cancelled.error_message.startswith(CANCELLED_PREFIX)


def test_stale_processing_row_is_recovered_then_job_runs(nightly_export, engine, tmp_path, ok_pipeline, pipeline_counter):
    zombie = insert_run(engine, status="processing", target_date=date(2026, 9, 10), age=timedelta(hours=4))

    outcome = nightly_export.run_nightly_export(engine, TARGET, raw_dir=tmp_path, pipeline_command=ok_pipeline)

    assert outcome == "completed"
    by_id = {run.id: run for run in runs(engine)}
    assert by_id[zombie.id].status == "failed"
    assert "Abandonada: seguía en 'processing'" in by_id[zombie.id].error_message
    assert pipeline_calls(pipeline_counter) == 1


def test_recent_processing_row_is_not_treated_as_stale(nightly_export, engine, tmp_path, ok_pipeline):
    insert_run(engine, status="processing", age=timedelta(hours=2))

    assert nightly_export.run_nightly_export(engine, TARGET, raw_dir=tmp_path, pipeline_command=ok_pipeline) == "cancelled"


def test_two_real_processes_at_once_only_one_does_the_work(tmp_path):
    """Demostración del criterio de evaluación: dos procesos del script
    arrancados a la vez contra la misma base (SQLite en archivo)."""
    db_url = f"sqlite:///{tmp_path / 'jobs.db'}"
    counter = tmp_path / "pipeline_calls.txt"
    harness = tmp_path / "harness.py"
    harness.write_text(
        textwrap.dedent(
            f"""
            import importlib.util, sys, time
            from datetime import date
            from pathlib import Path
            spec = importlib.util.spec_from_file_location("nightly_export", {str(SCRIPT_PATH)!r})
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            from sqlmodel import create_engine
            engine = create_engine({db_url!r}, connect_args={{"timeout": 30}})
            start_at = float(sys.argv[1])
            time.sleep(max(0.0, start_at - time.time()))
            slow_pipeline = (sys.executable, "-c",
                "import sys, time; open(sys.argv[1], 'a').write('x'); time.sleep(3)", {str(counter)!r})
            print(module.run_nightly_export(engine, date(2026, 9, 12), raw_dir=Path({str(tmp_path)!r}),
                                            pipeline_command=slow_pipeline))
            """
        )
    )
    # Crea las tablas antes: si no, las dos instancias compiten por el DDL
    # (checkfirst no es atómico) y una muere con "table already exists".
    subprocess.run([sys.executable, "-c", textwrap.dedent(f"""
        import sys; sys.path[:0] = [{str(ROOT_DIR)!r}, {str(ROOT_DIR / "services" / "api")!r}]
        from sqlmodel import create_engine
        from services.job_runner import ensure_job_runs_table
        from telemetry_models import TelemetryEventRecord
        engine = create_engine({db_url!r})
        TelemetryEventRecord.__table__.create(engine)
        ensure_job_runs_table(engine)
    """)], check=True, cwd=ROOT_DIR)

    start_at = str(time.time() + 3)
    processes = [
        subprocess.Popen([sys.executable, str(harness), start_at], cwd=ROOT_DIR,
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        for _ in range(2)
    ]
    outputs = [process.communicate(timeout=60) for process in processes]

    outcomes = sorted(
        stdout.strip().splitlines()[-1] if stdout.strip() else f"crash: {' / '.join(stderr.strip().splitlines()[-4:])}"
        for stdout, stderr in outputs
    )
    assert outcomes == ["cancelled", "completed"]
    assert pipeline_calls(counter) == 1


# --- Máquina de estados (repositorio) ----------------------------------------------


def test_partial_unique_index_makes_processing_an_atomic_lock(engine):
    with Session(engine) as session:
        first = create_run(session, JOB, TARGET)
        second = create_run(session, JOB, date(2026, 9, 11))
        mark_processing(session, first.id)
        with pytest.raises(ProcessingLockBusy):
            mark_processing(session, second.id)
        assert session.get(JobRun, second.id).status == "pending"

        finish_run(session, first.id, status="completed")
        mark_processing(session, second.id)  # el lock quedó libre
        assert session.get(JobRun, second.id).status == "processing"


def test_other_job_names_do_not_share_the_lock(engine):
    with Session(engine) as session:
        mark_processing(session, create_run(session, JOB, TARGET).id)
        other = create_run(session, "otro_job", TARGET)
        assert mark_processing(session, other.id).status == "processing"


@pytest.mark.parametrize(
    ("setup", "target"),
    [([], "completed"), (["processing", "completed"], "failed"), (["failed"], "processing")],
)
def test_invalid_transitions_are_rejected(engine, setup, target):
    with Session(engine) as session:
        run = create_run(session, JOB, TARGET)
        for status in setup:
            if status == "processing":
                mark_processing(session, run.id)
            else:
                finish_run(session, run.id, status=status)
        with pytest.raises(InvalidTransition):
            if target == "processing":
                mark_processing(session, run.id)
            else:
                finish_run(session, run.id, status=target)


def test_status_check_constraint_rejects_unknown_values(engine):
    from sqlalchemy.exc import IntegrityError

    with Session(engine) as session:
        session.add(JobRun(job_name=JOB, target_date=TARGET, status="done", created_at=datetime.now(timezone.utc)))
        with pytest.raises(IntegrityError):
            session.commit()


# --- Independencia y logs --------------------------------------------------------------


def test_script_does_not_import_fastapi():
    code = textwrap.dedent(
        f"""
        import importlib.util, sys
        spec = importlib.util.spec_from_file_location("nightly_export", {str(SCRIPT_PATH)!r})
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        print(sorted(name for name in sys.modules if name.split('.')[0] in ('fastapi', 'starlette', 'main', 'prefect')))
        """
    )
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True, cwd=ROOT_DIR)
    assert result.stdout.strip() == "[]"


def test_logs_carry_job_name_and_status(nightly_export, engine, tmp_path, ok_pipeline, caplog):
    caplog.set_level(logging.INFO, logger="nightly_export")
    nightly_export.run_nightly_export(engine, TARGET, raw_dir=tmp_path, pipeline_command=ok_pipeline)
    nightly_export.run_nightly_export(engine, TARGET, raw_dir=tmp_path, pipeline_command=ok_pipeline)

    records = [record for record in caplog.records if record.name == "nightly_export"]
    assert all(record.job == JOB for record in records)
    assert [record.status for record in records] == [
        "pending", "processing", "processing", "processing", "processing", "completed", "skipped",
    ]
    assert all(record.levelno == logging.INFO for record in records)


def test_main_formats_lines_with_timestamp_job_and_status(tmp_path):
    """TARGET_DATE inválido: sale con 2 sin tocar la base de datos."""
    result = subprocess.run(
        [sys.executable, str(SCRIPT_PATH)],
        capture_output=True, text=True, cwd=ROOT_DIR, env={"TARGET_DATE": "mañana", "PATH": ""},
    )
    assert result.returncode == 2
    line = result.stdout.strip()
    date_part, level, job, status = line.split(" ")[:4]
    datetime.strptime(date_part, "%Y-%m-%dT%H:%M:%SZ")
    assert (level, job, status) == ("ERROR", "job=nightly_export", "status=failed")
