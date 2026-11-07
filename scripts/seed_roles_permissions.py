"""Siembra Roles y Permisos (BRIEFING): 3 roles, 6 departamentos, 5 usuarios
y 2 informes internos.

    services/api/.venv/bin/python scripts/seed_roles_permissions.py

Desde la raíz. Escribe en una COPIA de la TinyDB
(data/roles_permissions/demo-users.db.json, fuera de git) salvo que se pase
SUPPLIERS_DB_PATH. Contraseña de desarrollo compartida: aleatoria en cada
ejecución (o ROLES_SEED_PASSWORD), y se reinicia en las cuentas de la
semilla para que la impresa siempre funcione. Idempotente para lo demás.
"""

from __future__ import annotations

import os
import secrets
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
API_DIR = ROOT_DIR / "services" / "api"
DEMO_TINYDB = ROOT_DIR / "data" / "roles_permissions" / "demo-users.db.json"
os.environ.setdefault("SUPPLIERS_DB_PATH", str(DEMO_TINYDB))
sys.path[:0] = [str(ROOT_DIR), str(API_DIR)]


def main() -> int:
    db_path = Path(os.environ["SUPPLIERS_DB_PATH"])
    db_path.parent.mkdir(parents=True, exist_ok=True)

    from access_seed import seed
    from auth_repository import AuthRepository
    from security import hash_password

    password = os.getenv("ROLES_SEED_PASSWORD") or secrets.token_urlsafe(12)
    hashed = hash_password(password)
    try:
        result = seed(hashed)
    except Exception as exc:  # TinyDB ilegible, disco lleno…
        print(f"No se pudo sembrar Roles y Permisos: {type(exc).__name__}", file=sys.stderr)
        return 1

    repo = AuthRepository()
    for user_id in result.user_ids.values():
        repo.set_password(user_id, hashed)

    print(f"TinyDB: {db_path}")
    print(f"Usuarios creados: {len(result.users_created)} · ya existían: {len(result.users_existing)}")
    print(f"Informes internos creados: {len(result.reports_created)}")
    for email in result.user_ids:
        print(f"  - {email}")
    print(f"\nContraseña de esta ejecución (todas las cuentas de la semilla): {password}")
    print(f"Arranca la API con: SUPPLIERS_DB_PATH={db_path} .venv/bin/uvicorn main:app --port 8000")
    return 0


if __name__ == "__main__":
    sys.exit(main())
