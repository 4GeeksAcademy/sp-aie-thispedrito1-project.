from __future__ import annotations

import hashlib
import hmac
import logging
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from dotenv import load_dotenv
from fastapi import Request
from sqlmodel import Session

import telemetry_repository
from models import TelemetryEvent
from telemetry_models import TelemetryEventRecord

# Mirrors the load_dotenv() calls already in security.py/database.py: a
# module that gets imported standalone (e.g. from a script, or before
# main.py has imported security.py) still needs JWT_SECRET_KEY for
# hash_identifier below.
load_dotenv(Path(__file__).resolve().parent / ".env")

SCHEMA_VERSION = "1.0.0"

# Unused today (backend-originated events log directly via log_event below,
# no HTTP round-trip to itself needed) — read here to establish the same
# settings pattern Phase 3 will need once this stub starts forwarding to a
# real pipeline, per the class brief.
TELEMETRY_ENDPOINT = os.getenv("TELEMETRY_ENDPOINT", "")

telemetry_logger = logging.getLogger("api.telemetry")


def log_event(event: TelemetryEvent) -> None:
    """Single sink every event — frontend-submitted or backend-originated —
    passes through. Logging only: persistence for frontend-submitted batches
    now happens in routes/telemetry.py via telemetry_repository.bulk_insert,
    right after validation. Backend-originated events (emit_backend_event
    below) are deliberately NOT persisted here — see build_event_record's
    docstring for why."""
    telemetry_logger.info(f"event_type={event.event_type} eventId={event.eventId} userId={event.userId}")


def log_batch(events: list[TelemetryEvent]) -> int:
    for event in events:
        log_event(event)
    return len(events)


def emit_backend_event(
    *,
    event_type: str,
    user_id: str | None,
    properties: dict[str, Any],
    session_id: str = "backend",
    request_id: str | None = None,
    db_session: Session | None = None,
) -> None:
    """For events the backend detects on its own — login outcomes, 5xx
    responses, inventory triggers — instead of receiving them from the
    frontend queue. Same envelope and log sink as POST /telemetry/events;
    built server-side because the data (or the HMAC secret in
    hash_identifier) only exists here.

    db_session is optional and only passed by call sites that also want the
    event persisted to Supabase, not just logged (currently: login_succeeded/
    login_failed from routes/auth.py, via get_inventory_db_optional — see
    that dependency's docstring for why login can't depend on
    get_inventory_db directly). Persistence failures are swallowed: a
    Supabase hiccup must never turn into a broken login."""
    event = TelemetryEvent(
        eventId=str(uuid.uuid4()),
        timestamp=datetime.now(timezone.utc).isoformat(),
        sessionId=session_id,
        userId=user_id,
        event_type=event_type,
        schemaVersion=SCHEMA_VERSION,
        requestId=request_id or str(uuid.uuid4()),
        properties=properties,
    )
    log_event(event)

    if db_session is not None:
        try:
            record = build_event_record(event, service="api")
            telemetry_repository.bulk_insert(db_session, [record])
        except Exception:
            telemetry_logger.warning(
                "failed to persist backend-originated event_type=%s", event_type, exc_info=True
            )


def emit_api_error(request: Request) -> str:
    """Called from the global exception handler for every uncaught 5xx.
    Returns the error_id so the caller can also hand it back to the client
    for support/log correlation — the response itself still never includes
    the exception message or stack trace, matching the handler's existing
    contract."""
    error_id = str(uuid.uuid4())
    emit_backend_event(
        event_type="api_error_response",
        user_id=None,
        properties={
            "route_template": request.url.path,
            "method": request.method,
            "status_code": 500,
            "error_id": error_id,
        },
    )
    return error_id


def hash_identifier(identifier: str) -> str:
    """HMAC-SHA256 of a login identifier (email), keyed with the app's JWT
    secret — never the raw value, per event-schemas.json's pii_notes for
    login_failed. Falls back to an empty key (still deterministic, still
    never the raw email) rather than raising, so a missing secret degrades
    telemetry instead of breaking login itself."""
    secret = os.getenv("JWT_SECRET_KEY", "")
    return hmac.new(secret.encode("utf-8"), identifier.encode("utf-8"), hashlib.sha256).hexdigest()


# --- Phase 3: real storage (docs/telemetry/telemetry-plan.md) ---
# Everything below builds the row POST /telemetry/events inserts into
# Supabase's telemetry_events table for each *validated* event in a batch.

_VALUE_CANDIDATE_KEYS = (
    "value",
    "duration_ms",
    "quantity",
    "current_stock",
    "quantity_at_risk",
    "threshold_value",
    "quantity_requested",
)


def _parse_timestamp(raw: str) -> datetime:
    """Frontend sends ISO 8601 with a trailing 'Z' (JS's Date.toISOString()).
    datetime.fromisoformat only accepts a bare 'Z' from Python 3.11 onward,
    and this project's local venv is 3.9 (see techContext.md) — normalize it
    by hand instead of requiring a newer interpreter."""
    return datetime.fromisoformat(raw.replace("Z", "+00:00"))


def derive_value(properties: dict[str, Any]) -> Optional[float]:
    """Best-effort numeric summary for the telemetry_events.value column.
    Checks a small set of the most common numeric property names across the
    catalog (docs/telemetry/event-schemas.json) in priority order — not
    every event_type has one obvious number, so this stays None rather than
    guessing when none of them match."""
    for key in _VALUE_CANDIDATE_KEYS:
        raw = properties.get(key)
        if isinstance(raw, (int, float)) and not isinstance(raw, bool):
            return float(raw)
    return None


def build_tags(event: TelemetryEvent) -> dict[str, Any]:
    """tags = the allowlisted properties (enforced upstream by the emitting
    code — frontend track() callers — not here) plus the envelope's
    correlation fields. Decision: store eventId/sessionId/userId/requestId/
    schemaVersion alongside properties so the future dashboard/report can
    trace a row back to its session and user, and eventId can de-duplicate
    a retried batch. Documented in docs/telemetry/telemetry-plan.md."""
    return {
        **event.properties,
        "eventId": event.eventId,
        "sessionId": event.sessionId,
        "userId": event.userId,
        "requestId": event.requestId,
        "schemaVersion": event.schemaVersion,
    }


_ERROR_KEYWORDS = ("error",)
_WARN_KEYWORDS = ("failed", "rejected", "threshold", "flagged", "abandoned")


def derive_level(event_type: str) -> str:
    """Business call: which event_types represent something worth flagging
    versus routine activity. Feeds telemetry_events.level — the column the
    future ops/compliance dashboard (docs/telemetry/telemetry-plan.md) will
    filter and alert on, e.g. escalating stock_threshold_triggered to
    Marcus (Operaciones Clinicas).

    Keyword match on event_type rather than a 24-way lookup table: the
    catalog's own naming convention already encodes severity
    (*_rejected, *_failed, *_flagged, *_threshold_triggered), so a name
    that matches one of those keywords is "warn" by construction, an
    outright *_error* event is "error", and everything else (creations,
    views, successes) defaults to "info". Always returns one of the three
    — never raises — since this runs inline per event in the bulk-insert
    path and must be total.
    """
    if any(keyword in event_type for keyword in _ERROR_KEYWORDS):
        return "error"
    if any(keyword in event_type for keyword in _WARN_KEYWORDS):
        return "warn"
    return "info"


def build_event_record(event: TelemetryEvent, *, service: str = "backoffice") -> TelemetryEventRecord:
    """Maps one validated envelope to a telemetry_events row.

    service defaults to 'backoffice': most callers reach this function
    through POST /telemetry/events, which only the frontend's
    TelemetryService calls. The one exception is emit_backend_event above
    (login_succeeded/login_failed today), which passes service="api" since
    those events never touch the frontend queue. Every OTHER
    backend-originated event (5xx responses, inventory triggers) still
    isn't routed through here at all: persisting them would mean opening a
    Supabase session from request-handling code paths (the global exception
    handler, inventory routes) that don't go through a FastAPI Depends the
    test suite's override can intercept — see techContext.md. login is the
    exception because get_inventory_db_optional (database.py) IS a regular
    Depends, injected via the route signature."""
    return TelemetryEventRecord(
        timestamp=_parse_timestamp(event.timestamp),
        service=service,
        event_type=event.event_type,
        level=derive_level(event.event_type),
        value=derive_value(event.properties),
        message=event.properties.get("error_message"),
        tags=build_tags(event),
    )
