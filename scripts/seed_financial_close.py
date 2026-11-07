"""Datos de prueba del cierre financiero (BRIEFING, "Fuentes de datos").

1. Genera ledger.csv, budget.csv y operations.csv del MES DE CIERRE (último
   mes completo antes de --load-date) en data/raw/financial-close/<YYYY-MM>/.
2. Los ingiere en Supabase (tablas fc_*, sustituyendo ese periodo) y avisa a
   Cumplimiento del registro con datos de paciente.
3. Crea el directorio del cierre (los 7 destinatarios; Tom es el revisor) y sus
   cuentas en una COPIA de la TinyDB (data/financial_close/demo-users.db.json,
   fuera de git). Contraseña aleatoria (o FINANCIAL_CLOSE_SEED_PASSWORD).

Uso (desde la raíz):

    services/api/.venv/bin/python scripts/seed_financial_close.py [--load-date 2026-11-07] [--incomplete] [--no-accounts]
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
DATA_ROOT = ROOT_DIR / "data" / "raw" / "financial-close"
DEMO_TINYDB = ROOT_DIR / "data" / "financial_close" / "demo-users.db.json"
os.environ.setdefault("SUPPLIERS_DB_PATH", str(DEMO_TINYDB))
for path in (str(ROOT_DIR), str(API_DIR)):
    if path not in sys.path:
        sys.path.insert(0, path)

EMAIL_DOMAIN = "healthcore.test"
# usuario → rol del catálogo
STAFF = [
    ("sandra", "ceo"), ("james", "cto"), ("marcus", "clinical_ops_director"), ("priya", "patient_coordination_lead"),
    ("tom", "revenue_cycle_director"), ("claire", "compliance_officer"), ("diane", "people_director"),
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
    parser.add_argument("--incomplete", action="store_true", help="Escenario sin indicadores de Medicare/Medicaid.")
    parser.add_argument("--no-accounts", action="store_true")
    args = parser.parse_args()

    from dotenv import load_dotenv

    load_dotenv(API_DIR / ".env")
    from sqlmodel import Session, SQLModel

    from database import get_inventory_engine
    from services.financial_close import repository, seed_data, workflow
    from services.financial_close.catalog import PEOPLE
    from services.financial_close.models import FC_TABLES

    month = seed_data.closing_month(args.load_date)
    out = DATA_ROOT / month.strftime("%Y-%m")
    try:
        seed_data.generate_all(out, args.load_date, incomplete=args.incomplete)
    except (OSError, ValueError) as exc:
        print(f"Error generando los datos: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    print(f"Mes de cierre {month:%Y-%m}: CSV en {out.relative_to(ROOT_DIR)}"
          f"{' (escenario incompleto)' if args.incomplete else ''}")

    engine = get_inventory_engine()
    SQLModel.metadata.create_all(engine, tables=FC_TABLES)
    with Session(engine) as session:
        created = workflow.ingest_period(session, out, month)
        print(f"Ingerido en Supabase; avisos nuevos a Cumplimiento: {created}")
        if not args.no_accounts:
            password = os.getenv("FINANCIAL_CLOSE_SEED_PASSWORD") or secrets.token_urlsafe(12)
            ids = ensure_accounts(password)
            for key, role in STAFF:
                repository.upsert_staff(session, ids[key], PEOPLE[role], role)
    if not args.no_accounts:
        print(f"\nCuentas en {DEMO_TINYDB.relative_to(ROOT_DIR)} (contraseña de esta ejecución: {password})")
        for key, role in STAFF:
            print(f"  {key}@{EMAIL_DOMAIN}  ({role})")
        print(f"\nArranca la API con: SUPPLIERS_DB_PATH={DEMO_TINYDB} .venv/bin/uvicorn main:app --port 8000")
    return 0


if __name__ == "__main__":
    sys.exit(main())
