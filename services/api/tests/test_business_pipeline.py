"""Pipeline de desempeño de negocio (data/pipelines/pipeline.py) y endpoints
de services/reporting.

Mismo principio que el resto de la bateria: nunca Supabase real. El engine
del pipeline se sustituye por la SQLite en memoria de la fixture
`inventory_engine` (con el esquema `reporting` adjunto, ver conftest.py).
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from data.pipelines import pipeline
from data.pipelines.monthly_clinic_supply_performance import run_log, storage
from data.pipelines.monthly_clinic_supply_performance.models import (
    MonthlyClinicSupplyPerformance,
    PipelineRun,
    PipelineRunPartition,
)
from data.process.supply_performance_transforms import (
    evaluate_capture_coverage,
    resolve_month_start,
    transform_supply_events,
)
from inventory_models import MedicalSupply, SupplyDelivery
from services.reporting import router as reporting_router
from services.tasks import pipeline_tasks
from telemetry_models import TelemetryEventRecord

AUGUST = date(2026, 8, 1)


@pytest.fixture(scope="module", autouse=True)
def prefect_server():
    """Servidor de Prefect aislado para este modulo (base de datos temporal,
    nada en ~/.prefect). Ademas se apaga al terminar el modulo, con la salida
    de pytest aun abierta: sin esto, Prefect apaga su servidor efimero en el
    atexit del interprete y su log de cierre revienta contra un stream ya
    cerrado ("I/O operation on closed file")."""
    from prefect.testing.utilities import prefect_test_harness

    with prefect_test_harness(server_startup_timeout=60):
        yield


def _raw(ts: str, event_type: str, event_id: str | None = None, **tags) -> dict:
    """Fila cruda con la forma que devuelve storage.fetch_supply_events."""
    return {
        "id": str(uuid.uuid4()),
        "timestamp": ts,
        "event_type": event_type,
        "tags": {"eventId": event_id or str(uuid.uuid4()), "sessionId": "s-1", "userId": "u-1", **tags},
    }


def _store(engine, ts: str, event_type: str, event_id: str | None = None, **tags) -> None:
    with Session(engine) as session:
        session.add(
            TelemetryEventRecord(
                timestamp=datetime.fromisoformat(ts),
                service="test",
                event_type=event_type,
                tags={"eventId": event_id or str(uuid.uuid4()), "sessionId": "s-1", **tags},
            )
        )
        session.commit()


# --- Transformaciones (Pandas puro) ----------------------------------------


def test_transform_computes_literal_kpis_with_deduplication():
    retry_id = str(uuid.uuid4())
    rows = [
        _raw("2026-08-03T10:00:00", "inbound_order_created", retry_id, clinic_id=1, country="US", quantity=200, unit_cost=0.42, delivery_id=41),
        # Reintento de red: mismo eventId -> no cuenta dos veces.
        _raw("2026-08-03T10:00:02", "inbound_order_created", retry_id, clinic_id=1, country="US", quantity=200, unit_cost=0.42, delivery_id=41),
        _raw("2026-08-04T09:00:00", "outbound_order_created", clinic_id=1, country="US", quantity=5, consumption_id=7),
        # Doble track() de la misma orden: eventId distinto, mismo consumption_id.
        _raw("2026-08-04T09:00:00", "outbound_order_created", clinic_id=1, country="US", quantity=5, consumption_id=7),
        _raw("2026-08-05T09:00:00", "stock_threshold_triggered", clinic_id=1, country="US"),
        _raw("2026-08-06T09:00:00", "stock_threshold_triggered", clinic_id=1, country="US"),
        _raw("2026-08-07T09:00:00", "supply_expiry_flagged", clinic_id=10, country="UK"),
    ]

    result = transform_supply_events(rows, AUGUST)

    assert result["rows"] == [
        {
            "clinic_id": "1",
            "country": "US",
            "month_start": AUGUST,
            "total_supply_cost": Decimal("84.00"),
            "supply_consumption_count": 1,
            "critical_stockout_count": 2,  # conteo literal de eventos (deduplicados)
            "expiry_risk_count": 0,
            "currency": "USD",
        },
        {
            "clinic_id": "10",
            "country": "UK",
            "month_start": AUGUST,
            "total_supply_cost": Decimal("0.00"),
            "supply_consumption_count": 0,
            "critical_stockout_count": 0,
            "expiry_risk_count": 1,
            "currency": "GBP",
        },
    ]
    assert result["counts"]["duplicates_dropped"] == 2
    assert result["counts"]["rows_after_dedup"] == 5


def test_transform_treats_missing_unit_cost_as_unknown_not_zero():
    rows = [
        _raw("2026-08-03T10:00:00", "inbound_order_created", clinic_id=2, country="US", quantity=10, unit_cost=2.5, delivery_id=1),
        _raw("2026-08-03T11:00:00", "inbound_order_created", clinic_id=2, country="US", quantity=99, delivery_id=2),
    ]

    result = transform_supply_events(rows, AUGUST)

    assert result["rows"][0]["total_supply_cost"] == Decimal("25.00")
    assert result["quality"]["inbound_events_missing_cost"] == {"2": 1}


def test_transform_rejects_clinic_with_mixed_countries_instead_of_mixing_currencies():
    rows = [
        _raw("2026-08-03T10:00:00", "outbound_order_created", clinic_id=3, country="US", quantity=1, consumption_id=1),
        _raw("2026-08-03T11:00:00", "outbound_order_created", clinic_id=3, country="UK", quantity=1, consumption_id=2),
    ]

    result = transform_supply_events(rows, AUGUST)

    assert result["rows"] == []
    assert result["rejected"] == [{"clinic_id": "3", "reason": "mixed_country", "countries": ["UK", "US"]}]


def test_transform_excludes_invalid_rows_and_events_outside_the_month():
    rows = [
        _raw("2026-08-03T10:00:00", "outbound_order_created", clinic_id=99, country="US", quantity=1, consumption_id=1),
        _raw("2026-08-03T10:00:00", "outbound_order_created", clinic_id=1, country="FR", quantity=1, consumption_id=2),
        _raw("2026-09-01T00:00:00", "outbound_order_created", clinic_id=1, country="US", quantity=1, consumption_id=3),
        _raw("2026-07-31T23:59:59", "outbound_order_created", clinic_id=1, country="US", quantity=1, consumption_id=4),
    ]

    result = transform_supply_events(rows, AUGUST)

    assert result["rows"] == []
    assert result["counts"]["rows_invalid"] == 2


def test_transform_accepts_timestamps_with_and_without_microseconds():
    """Regresion: en Supabase conviven "10:00:00" y "10:00:00.519000"
    (isoformat() omite microsegundos a cero) y Pandas inferia el formato de
    la primera fila."""
    rows = [
        _raw("2026-08-03T10:00:00", "outbound_order_created", clinic_id=1, country="US", quantity=1, consumption_id=1),
        _raw("2026-08-03T10:00:05.519000", "outbound_order_created", clinic_id=1, country="US", quantity=1, consumption_id=2),
    ]

    result = transform_supply_events(rows, AUGUST)

    assert result["rows"][0]["supply_consumption_count"] == 2


def test_resolve_month_start_defaults_to_last_closed_month_and_rejects_open_months():
    assert resolve_month_start(None, date(2026, 9, 13)) == AUGUST
    assert resolve_month_start(None, date(2026, 1, 5)) == date(2025, 12, 1)
    with pytest.raises(ValueError):
        resolve_month_start(date(2026, 9, 1), date(2026, 9, 13))  # mes en curso
    with pytest.raises(ValueError):
        resolve_month_start(date(2026, 8, 15), date(2026, 9, 13))  # no es dia 1


def test_capture_coverage_blocks_when_orders_exist_but_no_events_arrived():
    blocked = evaluate_capture_coverage({}, {"deliveries": {"1": 4}, "consumptions": {}}, 0.95)
    assert blocked["blocking_error"].startswith("capture_gap")

    partial = evaluate_capture_coverage(
        {"1": {"inbound_order_created": 1, "outbound_order_created": 2}},
        {"deliveries": {"1": 2}, "consumptions": {"1": 2}},
        0.95,
    )
    assert partial["blocking_error"] is None
    assert partial["warnings"] == ["low_capture_ratio:1:inbound"]

    unavailable = evaluate_capture_coverage({}, None, 0.95)
    assert unavailable["warnings"] == ["coverage_unavailable"]


def test_transform_cache_key_is_stable_for_same_events_and_changes_with_a_late_event():
    events = [_raw("2026-08-03T10:00:00", "stock_threshold_triggered", clinic_id=1, country="US")]
    late = events + [_raw("2026-08-20T10:00:00", "stock_threshold_triggered", clinic_id=1, country="US")]

    key = pipeline.supply_events_cache_key(None, {"events": events, "month_start": AUGUST})

    assert key == pipeline.supply_events_cache_key(None, {"events": list(events), "month_start": AUGUST})
    assert key != pipeline.supply_events_cache_key(None, {"events": late, "month_start": AUGUST})
    assert key != pipeline.supply_events_cache_key(None, {"events": events, "month_start": date(2026, 7, 1)})


# --- Carga idempotente y log de corridas ----------------------------------


def _load(engine, rows_input: list[dict]) -> dict:
    transformed = transform_supply_events(rows_input, AUGUST)
    with Session(engine) as session:
        run = run_log.create_queued_run(session, month_start=AUGUST, trigger_type="test")
        counts = storage.load_monthly_rows(
            session,
            run_id=run.run_id,
            month_start=AUGUST,
            rows=transformed["rows"],
            rejected=transformed["rejected"],
            partition_event_counts=transformed["partition_event_counts"],
        )
        run_log.finish_run(session, run.run_id, status="completed")
    return counts


def _published(engine) -> list[tuple]:
    with Session(engine) as session:
        return [
            (r.clinic_id, r.country, Decimal(r.total_supply_cost), r.supply_consumption_count, r.critical_stockout_count, r.expiry_risk_count, r.currency)
            for r in session.exec(select(MonthlyClinicSupplyPerformance).order_by(MonthlyClinicSupplyPerformance.clinic_id)).all()
        ]


def test_load_twice_over_same_data_leaves_identical_table_without_duplicates(inventory_engine):
    events = [
        _raw("2026-08-03T10:00:00", "inbound_order_created", clinic_id=1, country="US", quantity=10, unit_cost=1.5, delivery_id=1),
        _raw("2026-08-04T10:00:00", "supply_expiry_flagged", clinic_id=11, country="UK"),
    ]

    first = _load(inventory_engine, events)
    table_after_first = _published(inventory_engine)
    second = _load(inventory_engine, events)

    assert first["inserted"] == 2
    assert second == {"inserted": 0, "updated": 0, "unchanged": 2, "removed": 0, "rejected": 0}
    assert _published(inventory_engine) == table_after_first
    assert len(table_after_first) == 2


def test_late_event_updates_published_row_and_keeps_previous_values(inventory_engine):
    events = [_raw("2026-08-03T10:00:00", "stock_threshold_triggered", clinic_id=4, country="US")]
    _load(inventory_engine, events)

    late = events + [_raw("2026-08-28T10:00:00", "stock_threshold_triggered", clinic_id=4, country="US")]
    counts = _load(inventory_engine, late)

    assert counts["updated"] == 1
    assert _published(inventory_engine)[0][4] == 2
    with Session(inventory_engine) as session:
        audit = session.exec(select(PipelineRunPartition).where(PipelineRunPartition.action == "updated")).one()
    assert audit.previous_values["critical_stockout_count"] == 1
    assert audit.new_values["critical_stockout_count"] == 2


def test_partition_rejected_on_recompute_is_removed_with_audit_trail(inventory_engine):
    _load(inventory_engine, [_raw("2026-08-03T10:00:00", "outbound_order_created", clinic_id=5, country="US", quantity=1, consumption_id=1)])

    mixed = [
        _raw("2026-08-03T10:00:00", "outbound_order_created", clinic_id=5, country="US", quantity=1, consumption_id=1),
        _raw("2026-08-04T10:00:00", "outbound_order_created", clinic_id=5, country="UK", quantity=1, consumption_id=2),
    ]
    counts = _load(inventory_engine, mixed)

    assert counts["removed"] == 1
    assert _published(inventory_engine) == []
    with Session(inventory_engine) as session:
        audit = session.exec(select(PipelineRunPartition).where(PipelineRunPartition.action == "removed")).one()
    assert audit.reason == "mixed_country"
    assert audit.previous_values["supply_consumption_count"] == 1


def test_window_lock_blocks_a_second_active_run_until_it_goes_stale(inventory_engine):
    with Session(inventory_engine) as session:
        first = run_log.create_queued_run(session, month_start=AUGUST, trigger_type="scheduled")
        with pytest.raises(run_log.WindowLockedError) as locked:
            run_log.create_queued_run(session, month_start=AUGUST, trigger_type="manual")
        assert locked.value.active_run_id == first.run_id

        # Otro mes no esta bloqueado.
        run_log.create_queued_run(session, month_start=date(2026, 7, 1), trigger_type="manual")

        # Sin heartbeat durante mas de 30 minutos: se libera el lock.
        run_log.update_run(session, first.run_id)
        stale = session.get(PipelineRun, first.run_id)
        stale.heartbeat_at = datetime.now(timezone.utc) - timedelta(minutes=45)
        session.add(stale)
        session.commit()
        second = run_log.create_queued_run(session, month_start=AUGUST, trigger_type="manual")

        assert session.get(PipelineRun, first.run_id).status == "crashed"
        assert second.status == "queued"


def test_report_is_stale_after_day_one_deadline_without_previous_month():
    now = datetime(2026, 9, 1, 7, 0, tzinfo=timezone.utc)
    assert run_log.is_report_stale(date(2026, 7, 1), now) is True
    assert run_log.is_report_stale(AUGUST, now) is False
    assert run_log.is_report_stale(date(2026, 7, 1), datetime(2026, 9, 1, 5, 0, tzinfo=timezone.utc)) is False
    assert run_log.is_report_stale(None, now) is True


# --- Flow de Prefect de punta a punta --------------------------------------


@pytest.fixture()
def pipeline_engine(inventory_engine, tmp_path, monkeypatch):
    """El flow real contra la SQLite de tests, con data/eval redirigido a un
    directorio temporal para no escribir en el repo."""
    pipeline.use_engine(inventory_engine)
    monkeypatch.setattr(pipeline, "EVAL_DIR", tmp_path / "eval")
    yield inventory_engine
    pipeline.use_engine(None)


def _seed_august(engine) -> None:
    _store(engine, "2026-08-03T10:00:00", "inbound_order_created", clinic_id=1, country="US", quantity=100, unit_cost=2.0, delivery_id=1)
    _store(engine, "2026-08-04T10:00:00", "outbound_order_created", clinic_id=1, country="US", quantity=60, consumption_id=1)
    _store(engine, "2026-08-04T10:00:01", "stock_threshold_triggered", clinic_id=1, country="US")
    _store(engine, "2026-08-10T10:00:00", "supply_expiry_flagged", clinic_id=12, country="UK")


def test_flow_runs_end_to_end_and_second_run_is_idempotent(pipeline_engine):
    _seed_august(pipeline_engine)

    first = pipeline.monthly_clinic_supply_performance_flow(month_start=AUGUST, trigger_type="test")
    table_after_first = _published(pipeline_engine)
    second = pipeline.monthly_clinic_supply_performance_flow(month_start=AUGUST, trigger_type="test")

    assert first["status"] == "completed"
    assert first["load"]["inserted"] == 2
    assert second["load"] == {"inserted": 0, "updated": 0, "unchanged": 2, "removed": 0, "rejected": 0}
    assert _published(pipeline_engine) == table_after_first == [
        ("1", "US", Decimal("200.00"), 1, 1, 0, "USD"),
        ("12", "UK", Decimal("0.00"), 0, 0, 1, "GBP"),
    ]
    with Session(pipeline_engine) as session:
        runs = session.exec(select(PipelineRun)).all()
    assert len(runs) == 2
    for run in runs:
        # Los cinco campos minimos del hito: inicio, fin, registros, estado, errores.
        assert run.started_at and run.finished_at
        assert run.rows_extracted == 4
        assert run.status == "completed"
        assert run.error_type is None


def test_flow_continues_when_the_optional_eval_snapshot_fails(pipeline_engine, tmp_path, monkeypatch):
    _seed_august(pipeline_engine)
    blocker = tmp_path / "not-a-directory"
    blocker.write_text("x")
    monkeypatch.setattr(pipeline, "EVAL_DIR", blocker)  # mkdir debajo de un archivo -> la task falla

    result = pipeline.monthly_clinic_supply_performance_flow(month_start=AUGUST, trigger_type="test")

    assert result["status"] == "completed_with_warnings"
    assert "eval_snapshot_failed" in result["warnings"]
    assert len(_published(pipeline_engine)) == 2  # la carga principal no se interrumpio


def test_flow_fails_and_records_error_when_capture_is_broken(pipeline_engine):
    with Session(pipeline_engine) as session:
        supply = MedicalSupply(name="Guantes", sku="HCR-PPE-001", category="ppe", unit="box", country="US")
        session.add(supply)
        session.commit()
        session.refresh(supply)
        session.add(
            SupplyDelivery(
                supply_id=supply.id, quantity=5, vendor_name="MedLine", clinic_id=1,
                created_at=datetime(2026, 8, 5, 10, 0), user_uuid="u-1",
            )
        )
        session.commit()

    with pytest.raises(Exception):
        pipeline.monthly_clinic_supply_performance_flow(month_start=AUGUST, trigger_type="test")

    with Session(pipeline_engine) as session:
        run = session.exec(select(PipelineRun)).one()
    assert run.status == "failed"
    assert run.error_type == "CaptureGapError"
    assert run.finished_at is not None
    assert _published(pipeline_engine) == []


# --- Endpoints de services/reporting ----------------------------------------


def test_reporting_endpoints_require_authentication(client: TestClient):
    assert client.get("/reporting/monthly-clinic-supply-performance").status_code == 401
    assert client.get("/reporting/pipeline-runs/latest").status_code == 401
    assert client.post("/reporting/pipeline-runs").status_code == 401


def test_kpi_endpoint_returns_context_contract_grouped_by_currency(client: TestClient, auth_headers, inventory_engine):
    assert client.get("/reporting/monthly-clinic-supply-performance", headers=auth_headers).status_code == 404

    _load(
        inventory_engine,
        [
            _raw("2026-08-03T10:00:00", "inbound_order_created", clinic_id=10, country="UK", quantity=4, unit_cost=2.5, delivery_id=1),
            _raw("2026-08-03T10:00:00", "inbound_order_created", clinic_id=2, country="US", quantity=10, unit_cost=1.25, delivery_id=2),
            _raw("2026-08-04T10:00:00", "outbound_order_created", clinic_id=2, country="US", quantity=1, consumption_id=1),
        ],
    )

    response = client.get("/reporting/monthly-clinic-supply-performance", headers=auth_headers)

    assert response.status_code == 200
    assert response.json() == {
        "month_start": "2026-08-01",
        "clinics": [
            {"clinic_id": "10", "country": "UK", "total_supply_cost": 10.0, "supply_consumption_count": 0, "critical_stockout_count": 0, "expiry_risk_count": 0, "currency": "GBP"},
            {"clinic_id": "2", "country": "US", "total_supply_cost": 12.5, "supply_consumption_count": 1, "critical_stockout_count": 0, "expiry_risk_count": 0, "currency": "USD"},
        ],
    }
    explicit = client.get("/reporting/monthly-clinic-supply-performance?month_start=2026-08-01", headers=auth_headers)
    assert explicit.json() == response.json()
    assert client.get("/reporting/monthly-clinic-supply-performance?month_start=2026-08-15", headers=auth_headers).status_code == 400
    assert client.get("/reporting/monthly-clinic-supply-performance?month_start=2026-07-01", headers=auth_headers).status_code == 404


def test_latest_run_endpoint_exposes_run_metadata(client: TestClient, auth_headers, inventory_engine):
    assert client.get("/reporting/pipeline-runs/latest", headers=auth_headers).status_code == 404

    _load(inventory_engine, [_raw("2026-08-03T10:00:00", "stock_threshold_triggered", clinic_id=1, country="US")])
    with Session(inventory_engine) as session:
        run = run_log.get_latest_run(session)
        run_log.update_run(
            session,
            run.run_id,
            started_at=datetime.now(timezone.utc),
            rows_extracted=1,
            quality_checks={"warnings": [], "inbound_events_missing_cost": {"10": 1, "3": 2}},
        )

    body = client.get("/reporting/pipeline-runs/latest", headers=auth_headers).json()

    assert body["status"] == "completed"
    assert body["month_start"] == "2026-08-01"
    assert body["records_processed"] == 1
    assert body["started_at"] and body["finished_at"]
    assert "triggered_by" not in body
    assert isinstance(body["is_stale"], bool)
    # Compras sin coste: el dashboard no debe presentar ese 0 como gasto real.
    assert body["clinics_with_unrecorded_cost"] == ["3", "10"]


def test_manual_trigger_requires_admin_and_valid_closed_month(client: TestClient, auth_headers, admin_headers, monkeypatch):
    monkeypatch.setattr(reporting_router, "enqueue_monthly_pipeline_run", lambda *args: "task-never-used")
    current_month = datetime.now(timezone.utc).date().replace(day=1).isoformat()

    assert client.post("/reporting/pipeline-runs", json={"month_start": "2026-01-01"}, headers=auth_headers).status_code == 403
    assert client.post("/reporting/pipeline-runs", json={"month_start": current_month}, headers=admin_headers).status_code == 400
    assert client.post("/reporting/pipeline-runs", json={"month_start": "2026-01-15"}, headers=admin_headers).status_code == 400


def test_manual_trigger_reserves_run_enqueues_task_and_writes_nothing(
    client: TestClient, admin_headers, inventory_engine, monkeypatch
):
    """Ticket #DEV-55: la petición solo lee; la fila con el lock la crea el
    worker, con el run_id que se devuelve aquí."""
    enqueued = []

    def fake_enqueue(run_id, month_start, triggered_by):
        enqueued.append((run_id, month_start, triggered_by))
        return "5f0c3d1e-8f5b-4c55-9a52-6f7f2b1f0a11"

    monkeypatch.setattr(reporting_router, "enqueue_monthly_pipeline_run", fake_enqueue)

    response = client.post("/reporting/pipeline-runs", json={"month_start": "2026-01-01"}, headers=admin_headers)

    assert response.status_code == 202
    body = response.json()
    assert body["task_id"] == "5f0c3d1e-8f5b-4c55-9a52-6f7f2b1f0a11"
    assert body["status"] == "queued" and body["month_start"] == "2026-01-01"
    assert enqueued == [(body["run_id"], date(2026, 1, 1), enqueued[0][2])]
    with Session(inventory_engine) as session:
        assert session.exec(select(PipelineRun)).all() == []


def test_manual_trigger_rejects_a_month_with_a_live_run_but_not_a_stale_one(
    client: TestClient, admin_headers, inventory_engine, monkeypatch
):
    monkeypatch.setattr(reporting_router, "enqueue_monthly_pipeline_run", lambda *args: "task-id")
    with Session(inventory_engine) as session:
        live = run_log.create_queued_run(session, month_start=date(2026, 1, 1), trigger_type="manual")
        live_id = str(live.run_id)
        stale = run_log.create_queued_run(session, month_start=date(2026, 2, 1), trigger_type="manual")
        stale.queued_at = datetime.now(timezone.utc) - timedelta(minutes=run_log.HEARTBEAT_STALE_MINUTES + 1)
        session.add(stale)
        session.commit()

    overlap = client.post("/reporting/pipeline-runs", json={"month_start": "2026-01-01"}, headers=admin_headers)
    assert overlap.status_code == 409
    assert overlap.json()["detail"]["run_id"] == live_id
    # La caducada no bloquea: el worker la marcará crashed al tomar el lock.
    assert client.post("/reporting/pipeline-runs", json={"month_start": "2026-02-01"}, headers=admin_headers).status_code == 202


def test_task_is_cancelled_when_the_month_was_taken_after_the_api_check(pipeline_engine):
    """Carrera: dos peticiones pasan la comprobación a la vez. El índice único
    sigue impidiendo dos corridas: la segunda tarea termina `cancelled`."""
    pipeline_tasks.use_engine(pipeline_engine)
    with Session(pipeline_engine) as session:
        winner_id = run_log.create_queued_run(session, month_start=AUGUST, trigger_type="manual").run_id
    reserved = str(uuid.uuid4())
    try:
        result = pipeline_tasks.run_monthly_clinic_supply_performance.apply(
            kwargs={"month_start": "2026-08-01", "run_id": reserved, "triggered_by": "u-1"}
        )
    finally:
        pipeline_tasks.use_engine(None)

    assert result.state == "SUCCESS"
    assert result.result["status"] == "cancelled"
    assert str(winner_id) in result.result["reason"]
    with Session(pipeline_engine) as session:
        assert session.get(PipelineRun, uuid.UUID(reserved)) is None  # el id reservado nunca llegó a existir
        assert session.get(PipelineRun, winner_id).status == "queued"  # la corrida ganadora sigue intacta


def test_celery_task_runs_the_real_flow_for_the_queued_run(client: TestClient, admin_headers, pipeline_engine, monkeypatch):
    """De punta a punta sin Redis: la API encola y la tarea de Celery se
    ejecuta en modo síncrono (`apply`), con el flow real de Prefect."""
    _seed_august(pipeline_engine)
    pipeline_tasks.use_engine(pipeline_engine)
    executed = {}

    def enqueue_and_run_inline(run_id, month_start, triggered_by):
        result = pipeline_tasks.run_monthly_clinic_supply_performance.apply(
            kwargs={"month_start": month_start.isoformat(), "run_id": run_id, "triggered_by": triggered_by}
        )
        executed["state"], executed["result"] = result.state, result.result
        return result.id

    monkeypatch.setattr(reporting_router, "enqueue_monthly_pipeline_run", enqueue_and_run_inline)
    try:
        response = client.post("/reporting/pipeline-runs", json={"month_start": "2026-08-01"}, headers=admin_headers)
    finally:
        pipeline_tasks.use_engine(None)
    assert response.status_code == 202

    assert executed["state"] == "SUCCESS"
    assert executed["result"]["run_id"] == response.json()["run_id"]  # el primer intento usa la fila de la API
    latest = client.get("/reporting/pipeline-runs/latest", headers=admin_headers).json()
    assert latest["run_id"] == response.json()["run_id"]
    assert latest["trigger_type"] == "manual"
    assert latest["status"] == "completed"
    kpis = client.get("/reporting/monthly-clinic-supply-performance", headers=admin_headers).json()
    assert [clinic["clinic_id"] for clinic in kpis["clinics"]] == ["12", "1"]


# --- Parte 3: subflows ejecutables por separado -----------------------------


def test_kpi_subflow_runs_standalone_with_in_memory_events_and_no_database():
    events = [
        _raw("2026-08-03T10:00:00", "inbound_order_created", clinic_id=7, country="US", quantity=120, unit_cost=0.35, delivery_id=1),
        _raw("2026-08-05T10:00:00", "stock_threshold_triggered", clinic_id=7, country="US"),
    ]

    result = pipeline.compute_monthly_clinic_supply_kpis(events, AUGUST)

    assert result["rows"] == [
        {
            "clinic_id": "7",
            "country": "US",
            "month_start": AUGUST,
            "total_supply_cost": Decimal("42.00"),
            "supply_consumption_count": 0,
            "critical_stockout_count": 1,
            "expiry_risk_count": 0,
            "currency": "USD",
        }
    ]
    assert result["validation"]["warnings"] == ["coverage_unavailable"]  # sin actividad de dominio


def test_extraction_subflow_runs_standalone_and_minimizes_tags(pipeline_engine):
    _store(pipeline_engine, "2026-08-03T10:00:00", "outbound_order_created", clinic_id=1, country="US", quantity=2, consumption_id=9, userId="user-uuid-1", requestId="req-1")

    extracted = pipeline.extract_clinic_supply_activity(AUGUST)  # sin run_id: sin checkpoint

    assert len(extracted["events"]) == 1
    tags = extracted["events"][0]["tags"]
    assert "userId" not in tags and "requestId" not in tags
    assert tags["clinic_id"] == 1
    assert extracted["domain_activity"] == {"deliveries": {}, "consumptions": {}}
