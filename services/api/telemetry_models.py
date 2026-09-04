from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Optional

from sqlalchemy import JSON, Column, Index
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.types import TypeDecorator
from sqlmodel import Field, SQLModel


class _JSONBOrJSON(TypeDecorator):
    """JSONB on Supabase (Postgres); plain JSON everywhere else. The
    in-memory SQLite the test suite uses (see tests/conftest.py) has no
    JSONB dialect to compile against, so this picks the right column type
    per engine instead of hardcoding Postgres-only DDL."""

    impl = JSON
    cache_ok = True

    def load_dialect_impl(self, dialect):
        if dialect.name == "postgresql":
            return dialect.type_descriptor(JSONB())
        return dialect.type_descriptor(JSON())


class TelemetryEventRecord(SQLModel, table=True):
    """Append-only store for validated telemetry events (docs/telemetry/
    telemetry-plan.md). Never updated or deleted — each row is an immutable
    fact. `id` is generated client-side (uuid4) rather than via Postgres'
    gen_random_uuid() so the same model works against the SQLite engine
    tests use, per the project's existing dual-engine test convention."""

    __tablename__ = "telemetry_events"
    __table_args__ = (
        Index("ix_telemetry_events_timestamp", "timestamp"),
        Index("ix_telemetry_events_event_type", "event_type"),
        # postgresql_using is a dialect-specific kwarg: Postgres gets a real
        # GIN index over the JSONB column, SQLite (tests) gets a plain index
        # instead of erroring on an unsupported index type.
        Index("ix_telemetry_events_tags_gin", "tags", postgresql_using="gin"),
    )

    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    timestamp: datetime
    service: str
    event_type: str
    level: str = Field(default="info")
    value: Optional[float] = None
    message: Optional[str] = None
    tags: dict[str, Any] = Field(default_factory=dict, sa_column=Column(_JSONBOrJSON))
