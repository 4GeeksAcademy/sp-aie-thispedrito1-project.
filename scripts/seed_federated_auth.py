"""Siembra la Autenticación Federada (BRIEFING §6), sobre la de Roles y Permisos.

    services/api/.venv/bin/python scripts/seed_federated_auth.py

Desde la raíz. Escribe en una COPIA de la TinyDB
(data/federated_auth/demo-users.db.json, fuera de git) salvo que se pase
SUPPLIERS_DB_PATH. Datos 100 % sintéticos:

- Dr. Marcus Reid  (marcus.clinical@…) → contraseña + Microsoft vinculado (ms-oid-marcus-001).
- Priya Nair       (priya.access@…)    → contraseña, SIN vincular (para la demo de vinculación).
- Alex Morgan      (alex.frontdesk@…)  → SIN contraseña, Microsoft vinculado (ms-oid-alex-003):
  la desvinculación debe avisar y rechazarse.
- James Osei       (james.admin@…)     → admin: puede consultar /auth/federated/audit.

`ms-oid-unknown-999` no se vincula a nadie: es el fixture de login rechazado.
Todas las cuentas quedan con `provisioned_by_it = true`. Idempotente.
"""

from __future__ import annotations

import os
import secrets
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
API_DIR = ROOT_DIR / "services" / "api"
DEMO_TINYDB = ROOT_DIR / "data" / "federated_auth" / "demo-users.db.json"
os.environ.setdefault("SUPPLIERS_DB_PATH", str(DEMO_TINYDB))
sys.path[:0] = [str(ROOT_DIR), str(API_DIR)]

LINKS = {
    "marcus.clinical@healthcore.example": "ms-oid-marcus-001",
    "alex.frontdesk@healthcore.example": "ms-oid-alex-003",
}
PASSWORDLESS = {
    "email": "alex.frontdesk@healthcore.example",
    "name": "Alex Morgan",
    "role": "employee",
    "department": "patient_experience",
}


def main() -> int:
    db_path = Path(os.environ["SUPPLIERS_DB_PATH"])
    db_path.parent.mkdir(parents=True, exist_ok=True)

    from access_seed import seed
    from auth_repository import AuthRepository
    from federated_repository import FederatedRepository, IdentityAlreadyLinked, ProviderAlreadyLinked
    from security import hash_password

    password = os.getenv("FEDERATED_SEED_PASSWORD") or secrets.token_urlsafe(12)
    hashed = hash_password(password)
    try:
        result = seed(hashed)
    except Exception as exc:  # TinyDB ilegible, disco lleno…
        print(f"No se pudo sembrar la base de Roles y Permisos: {type(exc).__name__}", file=sys.stderr)
        return 1

    repo = AuthRepository()
    user_ids = dict(result.user_ids)
    for user_id in user_ids.values():
        repo.set_password(user_id, hashed)

    alex = repo.get_user_by_email(PASSWORDLESS["email"])
    if alex is None:
        alex, _profile = repo.create_user(
            email=PASSWORDLESS["email"],
            hashed_password="",
            role=PASSWORDLESS["role"],
            department=PASSWORDLESS["department"],
            profile_data={"name": PASSWORDLESS["name"]},
        )
    repo.set_password(alex["id"], None)  # sin contraseña: solo entra con Microsoft
    user_ids[PASSWORDLESS["email"]] = alex["id"]

    for user_id in user_ids.values():
        repo.set_federation_profile(user_id, provisioned_by_it=True)

    federated = FederatedRepository()
    for email, oid in LINKS.items():
        try:
            federated.create_link(
                user_id=user_ids[email],
                provider="microsoft",
                provider_user_id=oid,
                email_at_link_time=email,
                provisioned_by_it=True,
            )
        except (IdentityAlreadyLinked, ProviderAlreadyLinked):
            print(f"Aviso: {oid} ya estaba vinculado a otra cuenta; no se toca.", file=sys.stderr)

    print(f"TinyDB: {db_path}")
    for email, user_id in user_ids.items():
        links = [link["provider_user_id"] for link in federated.links_for_user(user_id)]
        has_password = bool(repo.get_user_by_id(user_id).get("hashed_password"))
        print(f"  - {email:40} contraseña={'sí' if has_password else 'NO'}  microsoft={links or '—'}")
    print("  · ms-oid-unknown-999 sin vincular (login rechazado)")
    print(f"\nContraseña de esta ejecución (cuentas con contraseña): {password}")
    print(
        "Arranca la API con:\n"
        f"  cd services/api && MICROSOFT_IDP_MODE=mock SUPPLIERS_DB_PATH={db_path} .venv/bin/uvicorn main:app --port 8000"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
