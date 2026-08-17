from __future__ import annotations

from sqlmodel import Session

from telemetry_models import TelemetryEventRecord


def bulk_insert(session: Session, records: list[TelemetryEventRecord]) -> None:
    """Persists every record in a single transaction.

    This is the entire point of batching (see README's 'Por qué importa el
    bulk insert'): one INSERT per event would open one transaction per
    event, and a 20-event batch from 10 concurrent users would be 200
    transactions per flush cycle.
    """
    if not records:
        return
    session.add_all(records)
    session.commit()
