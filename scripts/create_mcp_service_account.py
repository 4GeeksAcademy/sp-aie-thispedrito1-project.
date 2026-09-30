"""Crea la cuenta de servicio con la que el servidor MCP llama a la API.

    services/api/.venv/bin/python scripts/create_mcp_service_account.py

Desde la raíz. Lee HEALTHCORE_API_EMAIL / HEALTHCORE_API_PASSWORD de
`mcps/healthcore/.env` y crea el usuario con rol `user` (nunca admin: el
servidor MCP no necesita más para incidencias e inventario) en la TinyDB que
use la API (`SUPPLIERS_DB_PATH`, o la de services/api/data por defecto).
Idempotente: si el usuario ya existe, no hace nada. La contraseña nunca se
imprime.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "services" / "api")]


def main() -> int:
    from dotenv import load_dotenv

    load_dotenv(ROOT / "services" / "api" / ".env")  # SUPPLIERS_DB_PATH, si lo hay

    from auth_repository import AuthRepository
    from mcps.healthcore.config import McpConfigError, load_settings
    from models import UserRole
    from security import hash_password

    try:
        settings = load_settings()
    except McpConfigError as exc:
        print(exc, file=sys.stderr)
        return 1

    repo = AuthRepository()
    if repo.get_user_by_email(settings.api_email):
        print(f"La cuenta de servicio {settings.api_email} ya existe: nada que hacer.")
        return 0
    repo.create_user(
        email=settings.api_email,
        hashed_password=hash_password(settings.api_password),
        role=UserRole.user,
        profile_data={"name": "Servidor MCP (cuenta de servicio)"},
    )
    print(f"Cuenta de servicio {settings.api_email} creada con rol user.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
