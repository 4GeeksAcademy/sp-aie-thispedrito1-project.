from __future__ import annotations

import os
from pathlib import Path

from tinydb import TinyDB

DEFAULT_DB_PATH = Path(__file__).resolve().parent / "data" / "suppliers.db.json"


def get_db() -> TinyDB:
    db_path = Path(os.getenv("SUPPLIERS_DB_PATH", str(DEFAULT_DB_PATH))).resolve()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    return TinyDB(db_path)


def get_suppliers_table():
    return get_db().table("suppliers")
