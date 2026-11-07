"""Semilla de Roles y Permisos: usuarios e informes internos del BRIEFING.

Literal del BRIEFING (emails, rol y departamento de cada persona, y los dos
informes de Marcus y Tom). Roles y departamentos los siembra el propio
repositorio (AccessRepository.ensure_catalog). Idempotente: una cuenta o un
informe que ya existe no se vuelve a crear ni se modifica.

La usan `scripts/seed_roles_permissions.py` y los tests: así las pruebas de
Compliance se ejecutan sobre los usuarios reales de la semilla.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from access_models import department_id_for
from access_repository import AccessRepository
from auth_repository import AuthRepository

USERS_SEED = [
    {
        "email": "marcus.clinical@healthcore.example",
        "role": "supervisor",
        "department": "clinical_operations",
        "name": "Dr. Marcus Reid",
    },
    {
        "email": "tom.billing@healthcore.example",
        "role": "supervisor",
        "department": "revenue_cycle",
        "name": "Tom Callahan",
    },
    {
        "email": "priya.access@healthcore.example",
        "role": "employee",
        "department": "patient_experience",
        "name": "Priya Nair",
    },
    {
        "email": "claire.compliance@healthcore.example",
        "role": "employee",
        "department": "compliance",
        "name": "Claire Whitfield",
    },
    {
        "email": "james.admin@healthcore.example",
        "role": "admin",
        "department": "technology",
        "name": "James Osei",
    },
]

# Autor de cada informe: el responsable de su departamento en la semilla.
INTERNAL_REPORTS_SEED = [
    {
        "department": "clinical_operations",
        "title": "Austin North — documentation time spike",
        "body": "Average documentation minutes per encounter up 12% week over week. No patient identifiers.",
        "author": "marcus.clinical@healthcore.example",
    },
    {
        "department": "revenue_cycle",
        "title": "US denial rate — coding mismatch cluster",
        "body": "14% denial rate driven by three CPT mismatch patterns. No member IDs in this note.",
        "author": "tom.billing@healthcore.example",
    },
]


@dataclass
class SeedResult:
    users_created: list[str] = field(default_factory=list)
    users_existing: list[str] = field(default_factory=list)
    reports_created: list[str] = field(default_factory=list)
    user_ids: dict[str, str] = field(default_factory=dict)
    report_ids: dict[str, str] = field(default_factory=dict)


def seed(hashed_password: str) -> SeedResult:
    """Siembra el BRIEFING con una contraseña de desarrollo compartida (ya
    con hash: este módulo nunca ve la contraseña en claro)."""
    auth_repo = AuthRepository()
    access_repo = AccessRepository()
    access_repo.ensure_catalog()
    result = SeedResult()

    for entry in USERS_SEED:
        existing = auth_repo.get_user_by_email(entry["email"])
        if existing:
            result.users_existing.append(entry["email"])
            result.user_ids[entry["email"]] = existing["id"]
            continue
        user, _profile = auth_repo.create_user(
            email=entry["email"],
            hashed_password=hashed_password,
            role=entry["role"],
            department=entry["department"],
            profile_data={"name": entry["name"]},
        )
        result.users_created.append(entry["email"])
        result.user_ids[entry["email"]] = user["id"]

    for report in INTERNAL_REPORTS_SEED:
        department_id = department_id_for(report["department"])
        found = access_repo.find_report_by_title(department_id, report["title"])
        if not found:
            found = access_repo.create_report(
                department_id=department_id,
                title=report["title"],
                body=report["body"],
                created_by=result.user_ids[report["author"]],
            )
            result.reports_created.append(report["title"])
        result.report_ids[report["department"]] = found["id"]

    return result
