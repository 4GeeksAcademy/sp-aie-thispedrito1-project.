"""Datos semilla de proveedores y contratos (BRIEFING, tablas "Proveedores",
"Contratos" e "Histórico de precios").

1. Genera, con la fecha de carga (hoy, o --load-date):
   data/raw/supplier-contracts/{suppliers.csv, price_history.csv, contracts/CT-HC-0X.pdf}
2. Ingiere los tres en Supabase (tablas sc_*), sustituyendo la carga anterior.
3. Crea el directorio de aprobadores (Marcus, Claire, Sandra) y sus cuentas en
   una COPIA de la TinyDB (data/supplier_contracts/demo-users.db.json, fuera de
   git). Contraseña aleatoria (o SUPPLIER_SEED_PASSWORD), impresa una vez.
4. Ejecuta el barrido de alertas.

Uso (desde la raíz):

    services/api/.venv/bin/python scripts/seed_supplier_contracts.py [--load-date 2026-11-07] [--no-accounts]

Es idempotente: recargar sustituye proveedores, precios y contratos; las
alertas tienen clave determinista y no se duplican.
"""

from __future__ import annotations

import argparse
import os
import secrets
import shutil
import sys
from datetime import date
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
API_DIR = ROOT_DIR / "services" / "api"
DATA_DIR = ROOT_DIR / "data" / "raw" / "supplier-contracts"
DEMO_TINYDB = ROOT_DIR / "data" / "supplier_contracts" / "demo-users.db.json"
os.environ.setdefault("SUPPLIERS_DB_PATH", str(DEMO_TINYDB))
for path in (str(ROOT_DIR), str(API_DIR)):
    if path not in sys.path:
        sys.path.insert(0, path)

EMAIL_DOMAIN = "healthcore.test"
STAFF = [
    ("marcus", "clinical_ops_director"),
    ("claire", "compliance_officer"),
    ("sandra", "ceo"),
]


def ensure_accounts(password: str) -> dict:
    from auth_repository import AuthRepository
    from models import UserRole
    from security import hash_password

    if not DEMO_TINYDB.exists():
        DEMO_TINYDB.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(API_DIR / "data" / "suppliers.db.json", DEMO_TINYDB)
    repo = AuthRepository()
    hashed = hash_password(password)
    ids = {}
    for key, _ in STAFF:
        email = f"{key}@{EMAIL_DOMAIN}"
        user = repo.get_user_by_email(email)
        if user is None:
            user, _ = repo.create_user(email=email, hashed_password=hashed, role=UserRole.user, profile_data={"name": key})
        else:
            repo.set_password(user["id"], hashed)
        ids[key] = user["id"]
    return ids


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--load-date", type=date.fromisoformat, default=date.today())
    parser.add_argument("--out", type=Path, default=DATA_DIR)
    parser.add_argument("--no-accounts", action="store_true", help="No crear cuentas de demo.")
    args = parser.parse_args()

    from dotenv import load_dotenv

    load_dotenv(API_DIR / ".env")
    from sqlmodel import Session, SQLModel

    from database import get_inventory_engine
    from services.supplier_contracts import alerts, ingestion, repository, seed_data
    from services.supplier_contracts.catalog import PEOPLE
    from services.supplier_contracts.models import SC_TABLES

    try:
        seed_data.generate_all(args.out, args.load_date)
        dataset = ingestion.read_dataset(args.out, args.load_date)
    except (OSError, ValueError) as exc:
        print(f"Error generando o leyendo los datos: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    print(f"Datos generados en {args.out.relative_to(ROOT_DIR) if args.out.is_relative_to(ROOT_DIR) else args.out} "
          f"(fecha de carga {args.load_date})")
    print(f"  {len(dataset.suppliers)} proveedores, {len(dataset.prices)} precios, {len(dataset.contracts)} contratos, "
          f"{len(dataset.rejected)} filas rechazadas")
    for contract in dataset.contracts:
        doubtful = [t.term for t in contract.terms.values() if t.status != "ok"]
        print(f"  {contract.contract_id}: términos no OK = {doubtful or 'ninguno'}")

    engine = get_inventory_engine()
    SQLModel.metadata.create_all(engine, tables=SC_TABLES)
    with Session(engine) as session:
        repository.replace_dataset(session, dataset)
        if not args.no_accounts:
            password = os.getenv("SUPPLIER_SEED_PASSWORD") or secrets.token_urlsafe(12)
            ids = ensure_accounts(password)
            for key, role in STAFF:
                repository.upsert_staff(session, ids[key], PEOPLE[role], role)
        created = alerts.run_alert_sweep(session, today=args.load_date)
        print(f"\nAlertas nuevas: {len(created)}")
        for alert in created:
            print(f"  {alert.alert_key:42} → {alert.recipient_role}")

    if not args.no_accounts:
        print(f"\nCuentas en {DEMO_TINYDB.relative_to(ROOT_DIR)} (contraseña de esta ejecución: {password})")
        for key, role in STAFF:
            print(f"  {key}@{EMAIL_DOMAIN}  ({role})")
        print(f"\nArranca la API con: SUPPLIERS_DB_PATH={DEMO_TINYDB} .venv/bin/uvicorn main:app --port 8000")
    return 0


if __name__ == "__main__":
    sys.exit(main())
