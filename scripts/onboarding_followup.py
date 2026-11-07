"""Barrido de plazos del onboarding: recordatorios (24 h antes) y escalados.

    services/api/.venv/bin/python scripts/onboarding_followup.py

Pensado para cron cada hora (p. ej. `0 * * * *` en el servicio `scheduler`).
Idempotente: cada aviso tiene clave propia, así que repetirlo no duplica nada.
Todo lo que hace queda auditado con actor_type=agent.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
for path in (str(ROOT_DIR), str(ROOT_DIR / "services" / "api")):
    if path not in sys.path:
        sys.path.insert(0, path)


def main() -> int:
    from dotenv import load_dotenv

    load_dotenv(ROOT_DIR / "services" / "api" / ".env")
    from sqlmodel import Session

    from database import get_inventory_engine
    from services.onboarding.followup import sweep

    try:
        with Session(get_inventory_engine()) as session:
            counts = sweep(session)
    except Exception as exc:  # noqa: BLE001
        print(f"Barrido fallido ({type(exc).__name__})", file=sys.stderr)
        return 1
    print(json.dumps(counts))
    return 0


if __name__ == "__main__":
    sys.exit(main())
