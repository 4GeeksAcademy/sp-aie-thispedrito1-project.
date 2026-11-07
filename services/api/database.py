from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import Any, Dict, Iterator, Optional

from dotenv import load_dotenv
from sqlmodel import Session, create_engine
from sqlmodel.pool import StaticPool
from tinydb import TinyDB
from tinydb.storages import JSONStorage

load_dotenv(Path(__file__).resolve().parent / ".env")

DEFAULT_DB_PATH = Path(__file__).resolve().parent / "data" / "suppliers.db.json"


# Un solo candado para toda la TinyDB del proceso. Cada repositorio abre UN
# manejador de archivo que comparten todos los hilos de FastAPI (los
# handlers síncronos corren en un threadpool): dos lecturas simultáneas
# movían la posición del mismo archivo y una de ellas leía JSON a medias
# (JSONDecodeError → 500). Lo destapó Roles y Permisos: cada petición
# resuelve rol y departamento, y /admin/access pide tres listados a la vez.
_TINYDB_LOCK = threading.RLock()


class LockedJSONStorage(JSONStorage):
    """JSONStorage con lectura y escritura serializadas entre hilos."""

    def read(self) -> Optional[Dict[str, Dict[str, Any]]]:
        with _TINYDB_LOCK:
            return super().read()

    def write(self, data: Dict[str, Dict[str, Any]]) -> None:
        with _TINYDB_LOCK:
            super().write(data)


def get_db() -> TinyDB:
    db_path = Path(os.getenv("SUPPLIERS_DB_PATH", str(DEFAULT_DB_PATH))).resolve()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    return TinyDB(db_path, storage=LockedJSONStorage)


def get_suppliers_table():
    return get_db().table("suppliers")


# --- Inventory module (Hito 5): Supabase (PostgreSQL) via SQLModel ---
# Business data (products, orders) lives here. Users stay in TinyDB above;
# only their user_uuid crosses over as a plain string reference.

_inventory_engine = None


def get_inventory_engine():
    """Lazily build and cache the SQLModel engine (a connection pool, not a session)."""
    global _inventory_engine
    if _inventory_engine is None:
        database_url = os.getenv("DATABASE_URL")
        if not database_url:
            raise RuntimeError(
                "DATABASE_URL environment variable is required for the inventory module. "
                "Set it in services/api/.env with your Supabase connection string."
            )
        connect_args = {"check_same_thread": False} if database_url.startswith("sqlite") else {}
        _inventory_engine = create_engine(
            database_url,
            connect_args=connect_args,
            poolclass=StaticPool if database_url.startswith("sqlite") else None,
        )
    return _inventory_engine


def get_inventory_db() -> Iterator[Session]:
    """FastAPI dependency: yields one SQLModel session per request."""
    with Session(get_inventory_engine()) as session:
        yield session


def get_inventory_db_optional() -> Iterator[Session | None]:
    """Same session as get_inventory_db, but degrades to None instead of
    raising when Supabase isn't reachable (DATABASE_URL missing).

    For call sites where Supabase is a side-channel, not the point of the
    request — e.g. persisting a login telemetry event. /inventory and
    /telemetry/events use get_inventory_db directly on purpose: there,
    Supabase being unavailable IS a real failure the caller should see.
    Login must keep working even if Supabase is down, so it depends on this
    variant instead."""
    try:
        engine = get_inventory_engine()
    except RuntimeError:
        yield None
        return
    with Session(engine) as session:
        yield session
