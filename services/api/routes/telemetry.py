from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, Depends
from pydantic import ValidationError
from sqlmodel import Session

import telemetry_repository
import telemetry_service
from database import get_inventory_db
from models import TelemetryEvent, TelemetryIngestResponse

router = APIRouter(prefix="/telemetry", tags=["telemetry"])


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
