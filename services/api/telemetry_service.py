from __future__ import annotations

import hashlib
import hmac
import logging
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from fastapi import Request

from models import TelemetryEvent

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
    passes through. Phase 1 stub per the class brief: log only, no
    persistence (that's Phase 3, a later milestone)."""
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
) -> None:
    """For events the backend detects on its own — login outcomes, 5xx
    responses, inventory triggers — instead of receiving them from the
    frontend queue. Same envelope and log sink as POST /telemetry/events;
    built server-side because the data (or the HMAC secret in
    hash_identifier) only exists here."""
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
