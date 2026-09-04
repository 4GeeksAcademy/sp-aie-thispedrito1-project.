from __future__ import annotations

from fastapi import APIRouter

import telemetry_service
from models import TelemetryBatch, TelemetryIngestResponse

router = APIRouter(prefix="/telemetry", tags=["telemetry"])


@router.post("/events", response_model=TelemetryIngestResponse)
def ingest_events(payload: TelemetryBatch) -> TelemetryIngestResponse:
    """Phase 1 stub: accepts a batch, logs count + event_type per event,
    returns how many were received. No auth dependency and no persistence
    on purpose — this endpoint's only job today is to prove the frontend's
    batching/retry pipeline reaches the backend; Phase 3 adds storage."""
    received = telemetry_service.log_batch(payload.events)
    telemetry_service.telemetry_logger.info(f"telemetry batch received: {received} event(s)")
    return TelemetryIngestResponse(received=received)
