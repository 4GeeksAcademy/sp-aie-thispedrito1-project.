from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends
from pydantic import ValidationError
from sqlmodel import Session

import telemetry_repository
import telemetry_service
from cache import cache
from database import get_inventory_db
from models import TelemetryEvent, TelemetryIngestResponse
from services.telemetry.analysis import (
    auth_failure_rate,
    error_rate_by_day,
    events_per_day,
    web_vital_latency_by_day,
)

router = APIRouter(prefix="/telemetry", tags=["telemetry"])

REPORT_CACHE_TTL_SECONDS = 60
REPORT_DEFAULT_WINDOW_DAYS = 7


@router.post("/events", response_model=TelemetryIngestResponse)
def ingest_events(
    payload: Dict[str, Any], session: Session = Depends(get_inventory_db)
) -> TelemetryIngestResponse:
    """Phase 3: real storage, replacing the Phase 1 stub.

    The body is accepted loosely (a plain dict) instead of a typed
    `list[TelemetryEvent]` on purpose — see the class README's "Validacion
    parcial" section. If FastAPI validated the whole list up front, one
    malformed event would 422 the entire batch; instead each raw item is
    validated individually with TelemetryEvent.model_validate, so a bad
    event is rejected on its own and the rest of the batch still gets
    stored. All valid events land in Supabase in a single bulk insert — no
    auth dependency here either, unchanged from the stub, since the
    frontend queue must be able to flush even around a session_expired
    redirect.

    Schema validity isn't the only way an event can be unusable: `timestamp`
    is typed as a plain `str` on TelemetryEvent (unmodified from the prior
    class, on purpose), so a value like "not-a-real-timestamp" passes
    model_validate but blows up when telemetry_service.build_event_record
    tries to parse it into a real datetime. That failure is caught here
    per-event too, for the same reason: one bad row must not cost the rest
    of the batch, or the endpoint, a 500.
    """
    raw_events = payload.get("events", [])
    received = len(raw_events)
    valid_events: list[TelemetryEvent] = []
    rejected = 0

    for raw in raw_events:
        try:
            valid_events.append(TelemetryEvent.model_validate(raw))
        except ValidationError:
            rejected += 1

    records: list = []
    for event in valid_events:
        try:
            record = telemetry_service.build_event_record(event)
        except (ValueError, TypeError):
            rejected += 1
            continue
        records.append(record)
        telemetry_service.log_event(event)

    telemetry_repository.bulk_insert(session, records)

    return TelemetryIngestResponse(received=received, stored=len(records), rejected=rejected)


def _parse_query_datetime(value: str) -> datetime:
    """Same 'Z' normalization as telemetry_service._parse_timestamp — a
    query param like '2026-08-20T00:00:00Z' fails datetime.fromisoformat on
    Python < 3.11, which this project's local venv still is (see
    techContext.md)."""
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _resolve_period(start_date: Optional[str], end_date: Optional[str]) -> tuple[datetime, datetime]:
    """Resolves the report window exactly once, here — every metric
    function receives the same start/end and applies it only in its own SQL
    load, never a second 'last 7 days' filter of its own (per the class
    README's 'Propiedad de la ventana de fechas')."""
    end = _parse_query_datetime(end_date) if end_date else datetime.now(timezone.utc)
    start = (
        _parse_query_datetime(start_date)
        if start_date
        else end - timedelta(days=REPORT_DEFAULT_WINDOW_DAYS)
    )
    return start, end


@router.get("/report")
def get_report(
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    session: Session = Depends(get_inventory_db),
) -> Dict[str, Any]:
    """Reporte técnico/operacional: no calcula nada por request — cachea el
    resultado 60s por combinación de start_date/end_date, y solo recalcula
    cuando esa entrada expira o cambia. Ver services/telemetry/analysis.py
    para las funciones de métrica que hace cada key de "metrics"."""
    period_start, period_end = _resolve_period(start_date, end_date)
    cache_key = f"telemetry_report:{period_start.isoformat()}:{period_end.isoformat()}"

    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    report = {
        "period": {"from": period_start.isoformat(), "to": period_end.isoformat()},
        "metrics": {
            "events_per_day": events_per_day(session, start_date=period_start, end_date=period_end),
            "error_rate_by_day": error_rate_by_day(session, start_date=period_start, end_date=period_end),
            "web_vital_latency_by_day": web_vital_latency_by_day(
                session, start_date=period_start, end_date=period_end
            ),
            "auth_failure_rate": auth_failure_rate(session, start_date=period_start, end_date=period_end),
        },
    }
    cache.set(cache_key, report, ttl_seconds=REPORT_CACHE_TTL_SECONDS)
    return report
