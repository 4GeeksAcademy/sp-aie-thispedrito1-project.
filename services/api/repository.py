from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from tinydb import Query

from database import get_db


class SupplierRepository:
    TABLE_NAME = "suppliers"

    def __init__(self) -> None:
        self._db = get_db()
        self._table = self._db.table(self.TABLE_NAME)

    def list(self, country: str | None = None, category: str | None = None) -> list[dict[str, Any]]:
        records = [self._to_entity(doc) for doc in self._table.all()]

        if country:
            records = [item for item in records if item["country"] == country]

        if category:
            records = [item for item in records if category in item.get("categories", [])]

        return records

    def get_by_id(self, supplier_id: int) -> dict[str, Any] | None:
        doc = self._table.get(doc_id=supplier_id)
        if not doc:
            return None
        return self._to_entity(doc)

    def create(self, payload: dict[str, Any]) -> dict[str, Any]:
        record = dict(payload)
        record["updated_at"] = datetime.now(timezone.utc).isoformat()
        doc_id = self._table.insert(record)
        return self.get_by_id(doc_id)  # type: ignore[return-value]

    def update_rate(self, supplier_id: int, monthly_rate: float) -> dict[str, Any] | None:
        if not self._table.contains(doc_id=supplier_id):
            return None

        updated_at = datetime.now(timezone.utc).isoformat()
        self._table.update({"monthly_rate": monthly_rate, "updated_at": updated_at}, doc_ids=[supplier_id])
        return self.get_by_id(supplier_id)

    def update_status(self, supplier_id: int, status: str) -> dict[str, Any] | None:
        if not self._table.contains(doc_id=supplier_id):
            return None

        self._table.update({"status": status}, doc_ids=[supplier_id])
        return self.get_by_id(supplier_id)

    def delete(self, supplier_id: int) -> bool:
        if not self._table.contains(doc_id=supplier_id):
            return False
        self._table.remove(doc_ids=[supplier_id])
        return True

    def exists_by_natural_key(self, name: str, country: str) -> bool:
        supplier = Query()
        return self._table.contains((supplier.name == name) & (supplier.country == country))

    def insert_seed(self, payload: dict[str, Any]) -> bool:
        if self.exists_by_natural_key(payload["name"], payload["country"]):
            return False
        self._table.insert(payload)
        return True

    @staticmethod
    def _to_entity(doc: Any) -> dict[str, Any]:
        payload = dict(doc)
        payload["id"] = doc.doc_id
        return payload
