from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from database import get_db


class IncidentRepository:
    TABLE_NAME = "incidents"

    def __init__(self) -> None:
        self._db = get_db()
        self._table = self._db.table(self.TABLE_NAME)

    def list(
        self,
        status: str | None = None,
        origin: str | None = None,
        branch: str | None = None,
        category: str | None = None,
    ) -> list[dict[str, Any]]:
        records = [self._to_entity(doc) for doc in self._table.all()]

        if status:
            records = [item for item in records if item["status"] == status]
        if origin:
            records = [item for item in records if item["origin"] == origin]
        if branch:
            records = [item for item in records if item["branch"] == branch]
        if category:
            records = [item for item in records if item["category"] == category]

        records.sort(key=lambda item: str(item.get("created_at", "")), reverse=True)
        return records

    def get_by_id(self, incident_id: int) -> dict[str, Any] | None:
        doc = self._table.get(doc_id=incident_id)
        if not doc:
            return None
        return self._to_entity(doc)

    def create(self, payload: dict[str, Any]) -> dict[str, Any]:
        now = datetime.now(timezone.utc).isoformat()
        record = dict(payload)
        record["created_at"] = now
        record["updated_at"] = now
        doc_id = self._table.insert(record)
        return self.get_by_id(doc_id)  # type: ignore[return-value]

    def update_status(self, incident_id: int, status: str) -> dict[str, Any] | None:
        if not self._table.contains(doc_id=incident_id):
            return None

        updated_at = datetime.now(timezone.utc).isoformat()
        self._table.update({"status": status, "updated_at": updated_at}, doc_ids=[incident_id])
        return self.get_by_id(incident_id)

    def summary(self) -> dict[str, Any]:
        records = [dict(doc) for doc in self._table.all()]

        def count_by(field: str) -> dict[str, int]:
            counts: dict[str, int] = {}
            for record in records:
                key = str(record.get(field, ""))
                counts[key] = counts.get(key, 0) + 1
            return counts

        return {
            "total": len(records),
            "by_status": count_by("status"),
            "by_category": count_by("category"),
            "by_origin": count_by("origin"),
            "by_branch": count_by("branch"),
        }

    def existing_seed_source_ids(self) -> set[str]:
        return {str(doc["source_id"]) for doc in self._table.all() if "source_id" in doc}

    def insert_seed(self, payload: dict[str, Any]) -> None:
        self._table.insert(dict(payload))

    @staticmethod
    def _to_entity(doc: Any) -> dict[str, Any]:
        payload = dict(doc)
        # source_id is an internal seed-idempotency marker, not part of the model contract.
        payload.pop("source_id", None)
        payload["id"] = doc.doc_id
        return payload
