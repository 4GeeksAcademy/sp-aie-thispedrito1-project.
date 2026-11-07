"""Datos semilla del orquestador de incorporaciones (BRIEFING, "Datos semilla").

Crea, con datos ficticios:
- 2 responsables directos de departamentos distintos (Digital y Ciclo de Ingresos),
- 1 usuaria de Cumplimiento con permiso para aprobar la etapa 4 (Claire),
- Diane Foster (VP of People) y una persona del equipo de Personas,
- 3 incorporaciones: Austin (inglés), Londres (UK GDPR) y una tercera de
  Austin en español que, con --blocked-attempt, intenta pedir acceso a Epic
  sin la etapa 4 aprobada (el intento queda bloqueado y auditado).

Uso (desde la raíz, con la API apagada o encendida):

    services/api/.venv/bin/python scripts/seed_onboarding.py [--blocked-attempt]

Cuentas: van a una COPIA de la TinyDB (data/onboarding/demo-users.db.json,
fuera de git), nunca a services/api/data/suppliers.db.json. Arranca la API
con SUPPLIERS_DB_PATH apuntando a esa copia para entrar con ellas. La
contraseña se genera al azar (o ONBOARDING_SEED_PASSWORD) y se imprime una
vez: nunca hay una contraseña escrita en el repo.

Incorporaciones y directorio: en Supabase (DATABASE_URL de services/api/.env).
El grafo arranca en línea (sin Celery) hasta la primera pausa. Es idempotente:
si una cuenta o una incorporación ya existe, se reutiliza
(a las cuentas se les pone la contraseña nueva de esta ejecución).
"""

from __future__ import annotations

import argparse
import os
import secrets
import shutil
import sys
from datetime import date, timedelta
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
API_DIR = ROOT_DIR / "services" / "api"
DEMO_TINYDB = ROOT_DIR / "data" / "onboarding" / "demo-users.db.json"
os.environ.setdefault("SUPPLIERS_DB_PATH", str(DEMO_TINYDB))
for path in (str(ROOT_DIR), str(API_DIR)):
    if path not in sys.path:
        sys.path.insert(0, path)

STAFF = [
    # (clave, nombre, rol organizativo, departamento, sede)
    ("diane", "Diane Foster", "vp_people", "people", "austin"),
    ("ruth", "Ruth Okafor", "people_team", "people", "austin"),
    ("claire", "Claire Whitfield", "compliance_approver", "compliance", "london"),
    ("marco", "Marco Ruiz", "line_manager", "digital", "austin"),
    ("hannah", "Hannah Price", "line_manager", "revenue_cycle", "london"),
]

HIRES = [
    # (clave, nombre, puesto, departamento, variante, sede, marco, idioma, responsable)
    ("jordan", "Jordan Lee", "Software Engineer", "digital", "digital_engineer", "austin", "hipaa", "en", "marco"),
    ("amelia", "Amelia Shaw", "Revenue Cycle Analyst", "revenue_cycle", "revenue_cycle_analyst", "london", "uk_gdpr", "en", "hannah"),
    ("sofia", "Sofía Herrera", "Ingeniera de datos", "digital", "digital_engineer", "austin", "hipaa", "es", "marco"),
]
BLOCKED_HIRE = "sofia"
EMAIL_DOMAIN = "healthcore-demo.example"


def this_monday() -> date:
    today = date.today()
    return today - timedelta(days=today.weekday())


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
    for key in [s[0] for s in STAFF] + [h[0] for h in HIRES]:
        email = f"{key}@{EMAIL_DOMAIN}"
        user = repo.get_user_by_email(email)
        if user is None:
            user, _ = repo.create_user(email=email, hashed_password=hashed, role=UserRole.user, profile_data={"name": key})
        else:
            # Cuentas de demo fuera de git: cada ejecución deja válida la contraseña que imprime.
            repo.set_password(user["id"], hashed)
        ids[key] = user["id"]
    return ids


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--blocked-attempt", action="store_true", help="La tercera incorporación intenta pedir acceso a Epic.")
    args = parser.parse_args()

    from dotenv import load_dotenv

    load_dotenv(API_DIR / ".env")
    from sqlmodel import Session, SQLModel

    from database import get_inventory_engine
    from services.onboarding import access, repository, runner
    from services.onboarding import models as onboarding_models
    from services.onboarding.models import OnboardingHire, OnboardingStaff

    password = os.getenv("ONBOARDING_SEED_PASSWORD") or secrets.token_urlsafe(12)
    ids = ensure_accounts(password)
    engine = get_inventory_engine()
    onboarding_tables = [
        model.__table__
        for model in (
            onboarding_models.OnboardingStaff, onboarding_models.OnboardingHire, onboarding_models.OnboardingAuditEvent,
            onboarding_models.OnboardingNotification, onboarding_models.OnboardingQuestion, onboarding_models.OnboardingAccessRequest,
        )
    ]
    SQLModel.metadata.create_all(engine, tables=onboarding_tables)
    with Session(engine) as session:
        for key, name, role, department, site in STAFF:
            if session.get(OnboardingStaff, ids[key]) is None:
                session.add(OnboardingStaff(user_id=ids[key], display_name=name, org_role=role, department=department, site=site))
        session.commit()

        created = []
        for key, name, title, department, variant, site, framework, language, manager in HIRES:
            hire = repository.get_hire_by_user(session, ids[key])
            if hire is None:
                hire = OnboardingHire(
                    hire_user_id=ids[key], full_name=name, job_title=title, department=department, role_variant=variant,
                    site=site, framework=framework, language=language, manager_user_id=ids[manager], start_date=this_monday(),
                )
                session.add(hire)
                session.commit()
            created.append((key, hire))

        with runner.open_saver() as saver:
            for key, hire in created:
                runner.advance(session, hire.hire_id, saver=saver)
                session.refresh(hire)
                print(f"  {key:7} {hire.full_name:15} etapa {hire.current_stage} · {hire.phase}")

        if args.blocked_attempt:
            hire = dict(created)[BLOCKED_HIRE]
            try:
                access.issue_access_request(session, hire, "ehr_epic", requested_by_type="human", actor_user_id=hire.hire_user_id)
                print("  ¡El acceso NO se bloqueó!", file=sys.stderr)
                return 1
            except access.AccessBlockedError:
                print(f"  {BLOCKED_HIRE}: solicitud de acceso a Epic BLOQUEADA, evento de seguridad registrado y Claire avisada.")

    print(f"\nCuentas en {DEMO_TINYDB.relative_to(ROOT_DIR)} (contraseña de esta ejecución: {password})")
    for key in [s[0] for s in STAFF] + [h[0] for h in HIRES]:
        print(f"  {key}@{EMAIL_DOMAIN}")
    print(f"\nArranca la API con: SUPPLIERS_DB_PATH={DEMO_TINYDB} .venv/bin/uvicorn main:app --port 8000")
    return 0


if __name__ == "__main__":
    sys.exit(main())
