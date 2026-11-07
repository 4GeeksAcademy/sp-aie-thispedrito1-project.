"""Roles y Permisos (BRIEFING de HealthCore): rol y departamento como ejes
independientes, verificación centralizada y 403 explícitos.

Las 5 "pruebas que pedirá Compliance" van primero, numeradas como en el
BRIEFING, y se ejecutan sobre los usuarios REALES de la semilla
(access_seed): Marcus y Tom (supervisor, distinto departamento), Priya y
Claire (employee) y James (admin). Todas son llamadas directas a la API.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import access_seed
from access_models import ROLES_SEED, department_id_for, role_id_for
from security import create_access_token, hash_password

API_DIR = Path(__file__).resolve().parents[1]
SEED_HASH = hash_password("SemillaRbac-2026")  # un solo bcrypt por módulo

MARCUS = "marcus.clinical@healthcore.example"
TOM = "tom.billing@healthcore.example"
PRIYA = "priya.access@healthcore.example"
CLAIRE = "claire.compliance@healthcore.example"
JAMES = "james.admin@healthcore.example"


@pytest.fixture()
def seeded(client: TestClient) -> access_seed.SeedResult:
    return access_seed.seed(SEED_HASH)


def headers_for(seeded: access_seed.SeedResult, email: str) -> dict[str, str]:
    """Token de sesión directo (sin bcrypt de login): el rol del token es
    informativo, la API relee rol y departamento de la base en cada llamada."""
    return {"Authorization": f"Bearer {create_access_token(seeded.user_ids[email], 'n/a')}"}


def make_incident(client: TestClient, headers: dict[str, str], category: str) -> dict:
    payload = {
        "title": "Cola en recepción",
        "description": "Retraso operativo sin datos de pacientes",
        "category": category,
        "status": "open",
        "origin": "internal",
        "branch": "austin_north",
    }
    response = client.post("/api/incidents", json=payload, headers=headers)
    assert response.status_code == 201, response.text
    return response.json()


# --- Pruebas de Compliance (BRIEFING §"Pruebas que pedirá Compliance") ------


def test_compliance_1_employee_calling_a_supervisor_only_update_gets_403(client, seeded) -> None:
    priya = headers_for(seeded, PRIYA)
    report_id = client.post(
        "/internal-reports", json={"title": "Reminder campaign", "body": "SMS reminders resent."}, headers=priya
    ).json()["id"]

    response = client.put(f"/internal-reports/{report_id}", json={"title": "Edited"}, headers=priya)

    assert response.status_code == 403
    detail = response.json()["detail"]
    assert detail["code"] == "insufficient_role"
    assert detail["required_role"] == "supervisor"
    assert detail["current_role"] == "employee"
    # Y no ha cambiado nada.
    assert client.get(f"/internal-reports/{report_id}", headers=priya).json()["title"] == "Reminder campaign"


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("get", "/roles"),
        ("get", "/departments"),
        ("get", "/users"),
        ("patch", "/users/{tom}/assignment"),
        ("post", "/roles"),
        ("post", "/departments"),
    ],
)
def test_compliance_2_supervisor_calling_admin_only_endpoints_gets_403(client, seeded, method, path) -> None:
    marcus = headers_for(seeded, MARCUS)
    body = {"role_id": role_id_for("admin")} if "assignment" in path else {"code": "x_new", "name": "X", "rank": 1}
    kwargs = {"headers": marcus} if method == "get" else {"headers": marcus, "json": body}

    response = getattr(client, method)(path.format(tom=seeded.user_ids[TOM]), **kwargs)

    assert response.status_code == 403
    assert response.json()["detail"]["required_role"] == "admin"


def test_compliance_3_marcus_cannot_read_toms_revenue_report_by_id(client, seeded) -> None:
    tom_report = seeded.report_ids["revenue_cycle"]

    response = client.get(f"/internal-reports/{tom_report}", headers=headers_for(seeded, MARCUS))

    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "department_out_of_scope"
    # El cuerpo no revela de qué departamento es ni nada de su contenido.
    assert "revenue" not in response.text and "denial" not in response.text
    # Mismo rol, su departamento: sí. Y el admin, de cualquiera.
    assert client.get(f"/internal-reports/{tom_report}", headers=headers_for(seeded, TOM)).status_code == 200
    assert client.get(f"/internal-reports/{tom_report}", headers=headers_for(seeded, JAMES)).status_code == 200


def test_compliance_4_role_and_department_change_independently(client, seeded) -> None:
    james = headers_for(seeded, JAMES)
    tom_id, marcus_id = seeded.user_ids[TOM], seeded.user_ids[MARCUS]

    moved = client.patch(
        f"/users/{tom_id}/assignment", json={"department_id": department_id_for("compliance")}, headers=james
    )
    assert moved.status_code == 200, moved.text
    assert moved.json()["department"] == "compliance"
    assert moved.json()["role"] == "supervisor"

    demoted = client.patch(f"/users/{marcus_id}/assignment", json={"role_id": role_id_for("employee")}, headers=james)
    assert demoted.status_code == 200, demoted.text
    assert demoted.json()["role"] == "employee"
    assert demoted.json()["department"] == "clinical_operations"

    # Lo que ven ellos mismos en /auth/me, y con el MISMO token de antes: el
    # cambio se aplica sin volver a iniciar sesión.
    tom_me = client.get("/auth/me", headers=headers_for(seeded, TOM)).json()
    marcus_me = client.get("/auth/me", headers=headers_for(seeded, MARCUS)).json()
    assert (tom_me["role"], tom_me["department"]) == ("supervisor", "compliance")
    assert (marcus_me["role"], marcus_me["department"]) == ("employee", "clinical_operations")


def test_compliance_4b_role_and_department_are_two_separate_foreign_keys(client, seeded) -> None:
    from auth_repository import AuthRepository

    record = AuthRepository().get_user_by_email(TOM)
    assert record["role_id"] == role_id_for("supervisor")
    assert record["department_id"] == department_id_for("revenue_cycle")
    assert "role" not in record  # ningún segundo origen de verdad


ROUTE_FILES = sorted((API_DIR / "routes").glob("*.py"))


def _compares_role_or_department(node: ast.Compare) -> bool:
    for side in [node.left, *node.comparators]:
        if isinstance(side, ast.Subscript) and isinstance(side.slice, ast.Constant):
            if side.slice.value in {"role", "department", "department_id", "role_id"}:
                return True
        if (
            isinstance(side, ast.Call)
            and isinstance(side.func, ast.Attribute)
            and side.func.attr == "get"
            and side.args
            and isinstance(side.args[0], ast.Constant)
            and side.args[0].value in {"role", "department", "department_id", "role_id"}
        ):
            return True
    return False


@pytest.mark.parametrize("route_file", ROUTE_FILES, ids=lambda path: path.name)
def test_compliance_5_no_route_compares_roles_or_departments_by_hand(route_file: Path) -> None:
    tree = ast.parse(route_file.read_text(encoding="utf-8"))
    offenders = [node.lineno for node in ast.walk(tree) if isinstance(node, ast.Compare) and _compares_role_or_department(node)]
    assert offenders == [], f"{route_file.name}: comparación manual de rol/departamento en líneas {offenders}"


PROTECTED = {
    ("POST", "/users"): "require_users_manage",
    ("GET", "/users"): "require_users_manage",
    ("PATCH", "/users/{user_id}/assignment"): "require_users_manage",
    ("DELETE", "/users/{user_id}"): "require_users_manage",
    ("GET", "/roles"): "require_access_manage",
    ("POST", "/roles"): "require_access_manage",
    ("GET", "/roles/{role_id}"): "require_access_manage",
    ("PUT", "/roles/{role_id}"): "require_access_manage",
    ("GET", "/departments"): "require_access_manage",
    ("POST", "/departments"): "require_access_manage",
    ("GET", "/departments/{department_id}"): "require_access_manage",
    ("PUT", "/departments/{department_id}"): "require_access_manage",
    ("GET", "/internal-reports"): "require_records_read",
    ("POST", "/internal-reports"): "require_records_create",
    ("GET", "/internal-reports/{report_id}"): "require_records_read",
    ("PUT", "/internal-reports/{report_id}"): "require_records_update",
    ("POST", "/internal-reports/{report_id}/approve"): "require_records_approve",
    ("DELETE", "/internal-reports/{report_id}"): "require_records_delete",
    ("PATCH", "/api/incidents/{incident_id}/status"): "require_records_update",
    ("PATCH", "/suppliers/{supplier_id}/rate"): "require_records_update",
    ("PATCH", "/suppliers/{supplier_id}/status"): "require_records_update",
    ("DELETE", "/suppliers/{supplier_id}"): "require_records_delete",
    ("PATCH", "/inventory/products/{supply_id}/stock"): "require_records_update",
}


def _dependency_names(dependant) -> set[str]:
    names = set()
    for sub in dependant.dependencies:
        names.add(getattr(sub.call, "__name__", ""))
        names |= _dependency_names(sub)
    return names


def test_compliance_5b_permission_checks_are_dependencies_not_route_code() -> None:
    from main import app

    found = {}
    for route in app.routes:
        for method in getattr(route, "methods", ()):
            if (method, route.path) in PROTECTED:
                found[(method, route.path)] = _dependency_names(route.dependant)
    assert set(found) == set(PROTECTED), set(PROTECTED) - set(found)
    for key, expected in PROTECTED.items():
        assert expected in found[key], f"{key} sin la dependencia {expected}"


# --- Jerarquía de roles -----------------------------------------------------


def test_three_seeded_roles_have_strictly_growing_capabilities(client, seeded) -> None:
    roles = client.get("/roles", headers=headers_for(seeded, JAMES)).json()
    by_code = {role["code"]: role for role in roles}

    assert [role["code"] for role in roles] == [role["code"] for role in ROLES_SEED]
    employee, supervisor, admin = (set(by_code[code]["capabilities"]) for code in ("employee", "supervisor", "admin"))
    assert employee < supervisor < admin
    assert "records:update" not in employee and "records:update" in supervisor
    assert "records:delete" not in supervisor and "records:delete" in admin


def test_seed_departments_and_users_match_the_briefing(client, seeded) -> None:
    james = headers_for(seeded, JAMES)
    departments = {item["code"] for item in client.get("/departments", headers=james).json()}
    assert departments == {
        "clinical_operations",
        "patient_experience",
        "revenue_cycle",
        "compliance",
        "people_workforce",
        "technology",
    }
    users = {item["name"]: (item["role"], item["department"]) for item in client.get("/users", headers=james).json()}
    assert users == {
        "Dr. Marcus Reid": ("supervisor", "clinical_operations"),
        "Tom Callahan": ("supervisor", "revenue_cycle"),
        "Priya Nair": ("employee", "patient_experience"),
        "Claire Whitfield": ("employee", "compliance"),
        "James Osei": ("admin", "technology"),
    }


def test_seed_is_idempotent(client, seeded) -> None:
    again = access_seed.seed(SEED_HASH)
    assert again.users_created == [] and again.reports_created == []
    assert len(client.get("/internal-reports", headers=headers_for(seeded, JAMES)).json()) == 2


def test_auth_me_returns_role_and_department(client, seeded) -> None:
    body = client.get("/auth/me", headers=headers_for(seeded, JAMES)).json()
    assert body["role"] == "admin" and body["department"] == "technology"
    assert body["role_rank"] == 3


# --- Informes internos: alcance y capacidades -------------------------------


def test_listing_is_scoped_to_own_department_and_admin_sees_all(client, seeded) -> None:
    def titles(email: str) -> set[str]:
        return {item["department"] for item in client.get("/internal-reports", headers=headers_for(seeded, email)).json()}

    assert titles(MARCUS) == {"clinical_operations"}
    assert titles(TOM) == {"revenue_cycle"}
    assert titles(CLAIRE) == set()  # alcance: su departamento no tiene informes
    assert titles(JAMES) == {"clinical_operations", "revenue_cycle"}


def test_asking_for_another_departments_list_is_403_not_empty(client, seeded) -> None:
    response = client.get(
        "/internal-reports",
        params={"department_id": department_id_for("revenue_cycle")},
        headers=headers_for(seeded, MARCUS),
    )
    assert response.status_code == 403


def test_employee_creates_only_in_own_department(client, seeded) -> None:
    priya = headers_for(seeded, PRIYA)
    created = client.post("/internal-reports", json={"title": "No-show note", "body": "Weekly no-show trend."}, headers=priya)
    assert created.status_code == 201
    assert created.json()["department"] == "patient_experience"

    elsewhere = client.post(
        "/internal-reports",
        json={"title": "x", "body": "y", "department_id": department_id_for("revenue_cycle")},
        headers=priya,
    )
    assert elsewhere.status_code == 403


def test_supervisor_updates_and_approves_own_department_but_cannot_delete(client, seeded) -> None:
    marcus = headers_for(seeded, MARCUS)
    report_id = seeded.report_ids["clinical_operations"]

    assert client.put(f"/internal-reports/{report_id}", json={"body": "Up 11%."}, headers=marcus).status_code == 200
    approved = client.post(f"/internal-reports/{report_id}/approve", headers=marcus)
    assert approved.status_code == 200
    assert approved.json()["approved_by"] == seeded.user_ids[MARCUS]

    deleted = client.delete(f"/internal-reports/{report_id}", headers=marcus)
    assert deleted.status_code == 403
    assert deleted.json()["detail"]["required_role"] == "admin"


def test_supervisor_cannot_update_or_approve_another_department(client, seeded) -> None:
    tom = headers_for(seeded, TOM)
    report_id = seeded.report_ids["clinical_operations"]
    assert client.put(f"/internal-reports/{report_id}", json={"body": "x"}, headers=tom).status_code == 403
    assert client.post(f"/internal-reports/{report_id}/approve", headers=tom).status_code == 403


def test_admin_deletes_in_any_department(client, seeded) -> None:
    report_id = seeded.report_ids["revenue_cycle"]
    assert client.delete(f"/internal-reports/{report_id}", headers=headers_for(seeded, JAMES)).status_code == 204
    assert client.get(f"/internal-reports/{report_id}", headers=headers_for(seeded, JAMES)).status_code == 404


def test_patient_data_is_rejected_in_internal_reports(client, seeded) -> None:
    response = client.post(
        "/internal-reports",
        json={"title": "Follow-up", "body": "Patient John Smith, DOB 01/02/1980, MRN 1234567 missed."},
        headers=headers_for(seeded, PRIYA),
    )
    assert response.status_code == 400
    assert response.json()["detail"]["errors"][0]["field"] == "body"
    assert "John Smith" not in response.text


def test_internal_reports_require_authentication(client) -> None:
    assert client.get("/internal-reports").status_code == 401


# --- Incidencias con alcance departamental ----------------------------------


def test_incidents_are_scoped_by_department(client, seeded) -> None:
    from incident_repository import IncidentRepository

    # Histórico sin department_id (como el seed del CSV): se resuelve por categoría.
    IncidentRepository().insert_seed(
        {
            "title": "Factura duplicada",
            "description": "Sin datos de pacientes",
            "category": "billing_error",
            "status": "open",
            "origin": "customer",
            "branch": "austin_north",
            "created_at": "2026-01-01T00:00:00+00:00",
            "updated_at": "2026-01-01T00:00:00+00:00",
        }
    )
    marcus_incident = make_incident(client, headers_for(seeded, MARCUS), "other")
    assert marcus_incident["department_id"] == department_id_for("clinical_operations")

    tom_list = client.get("/api/incidents", headers=headers_for(seeded, TOM)).json()
    marcus_list = client.get("/api/incidents", headers=headers_for(seeded, MARCUS)).json()
    james_list = client.get("/api/incidents", headers=headers_for(seeded, JAMES)).json()
    assert [item["category"] for item in tom_list] == ["billing_error"]
    assert [item["id"] for item in marcus_list] == [marcus_incident["id"]]
    assert len(james_list) == 2

    billing_id = tom_list[0]["id"]
    assert client.get(f"/api/incidents/{billing_id}", headers=headers_for(seeded, MARCUS)).status_code == 403
    assert client.patch(
        f"/api/incidents/{billing_id}/status", json={"status": "in_progress"}, headers=headers_for(seeded, MARCUS)
    ).status_code == 403
    assert client.patch(
        f"/api/incidents/{billing_id}/status", json={"status": "in_progress"}, headers=headers_for(seeded, TOM)
    ).status_code == 200


def test_employee_cannot_change_incident_status(client, seeded) -> None:
    priya = headers_for(seeded, PRIYA)
    incident = make_incident(client, priya, "patient_experience")

    response = client.patch(f"/api/incidents/{incident['id']}/status", json={"status": "in_progress"}, headers=priya)

    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "insufficient_role"


def test_incident_summary_cache_is_per_department(client, seeded) -> None:
    make_incident(client, headers_for(seeded, MARCUS), "other")
    marcus = client.get("/api/incidents/summary", headers=headers_for(seeded, MARCUS)).json()
    tom = client.get("/api/incidents/summary", headers=headers_for(seeded, TOM)).json()
    assert (marcus["total"], tom["total"]) == (1, 0)

    make_incident(client, headers_for(seeded, TOM), "billing_error")
    assert client.get("/api/incidents/summary", headers=headers_for(seeded, TOM)).json()["total"] == 1


# --- Administración y casos límite ------------------------------------------


def test_last_admin_cannot_be_demoted(client, seeded) -> None:
    response = client.patch(
        f"/users/{seeded.user_ids[JAMES]}/assignment",
        json={"role_id": role_id_for("supervisor")},
        headers=headers_for(seeded, JAMES),
    )
    assert response.status_code == 409
    assert client.get("/auth/me", headers=headers_for(seeded, JAMES)).json()["role"] == "admin"


def test_assignment_validates_ids_and_requires_a_field(client, seeded) -> None:
    james = headers_for(seeded, JAMES)
    target = f"/users/{seeded.user_ids[TOM]}/assignment"
    assert client.patch(target, json={}, headers=james).status_code == 400
    unknown = client.patch(target, json={"department_id": "nope"}, headers=james)
    assert unknown.status_code == 400
    assert unknown.json()["detail"]["errors"][0]["field"] == "department_id"


def test_admin_manages_roles_and_departments(client, seeded) -> None:
    james = headers_for(seeded, JAMES)
    role = client.post("/roles", json={"code": "lead_coordinator", "name": "Lead coordinator", "rank": 2}, headers=james)
    assert role.status_code == 201
    assert "records:approve" in role.json()["capabilities"]
    assert "records:delete" not in role.json()["capabilities"]

    renamed = client.put(f"/roles/{role_id_for('employee')}", json={"name": "Empleado"}, headers=james)
    assert renamed.status_code == 200 and renamed.json()["rank"] == 1
    assert client.put(f"/roles/{role_id_for('supervisor')}", json={"rank": 3}, headers=james).status_code == 409
    assert client.post("/roles", json={"code": "admin", "name": "Dup", "rank": 3}, headers=james).status_code == 409

    department = client.post("/departments", json={"code": "research", "name": "Research"}, headers=james)
    assert department.status_code == 201
    assert client.put(f"/departments/{department.json()['id']}", json={"name": "R&D"}, headers=james).json()["name"] == "R&D"


def test_legacy_account_with_text_role_resolves_to_employee(client) -> None:
    from security import auth_repo, user_from_token

    auth_repo._users.insert({"id": "legacy-1", "email": "old@healthcore.example", "hashed_password": "x", "is_active": True, "role": "user"})
    resolved = user_from_token(create_access_token("legacy-1", "user"))
    assert (resolved["role"], resolved["department"]) == ("employee", None)


def test_account_without_department_gets_explicit_403_on_departmental_lists(client) -> None:
    from security import auth_repo

    auth_repo._users.insert({"id": "nodept-1", "email": "nodept@healthcore.example", "hashed_password": "x", "is_active": True, "role_id": role_id_for("employee")})
    response = client.get("/internal-reports", headers={"Authorization": f"Bearer {create_access_token('nodept-1', 'x')}"})
    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "department_required"


def test_unknown_role_fails_closed(client) -> None:
    from security import auth_repo

    auth_repo._users.insert({"id": "ghost-1", "email": "ghost@healthcore.example", "hashed_password": "x", "is_active": True, "role_id": "deleted-role", "department_id": department_id_for("technology")})
    headers = {"Authorization": f"Bearer {create_access_token('ghost-1', 'x')}"}
    assert client.get("/internal-reports", headers=headers).status_code == 403
    assert client.get("/roles", headers=headers).status_code == 403


def test_concurrent_reads_of_the_shared_tinydb_do_not_corrupt(client, seeded) -> None:
    """Regresión (lo destapó /admin/access en Chrome): tres peticiones a la
    vez leían el MISMO manejador de TinyDB desde hilos distintos y una
    recibía JSON a medias (500). database.LockedJSONStorage lo serializa."""
    from concurrent.futures import ThreadPoolExecutor

    from access_repository import AccessRepository
    from auth_repository import AuthRepository

    access_repo = AccessRepository()
    user = AuthRepository().get_user_by_email(TOM)

    def hammer(_: int) -> str:
        for _ in range(40):
            resolved = access_repo.resolve_user_access(user)
        return resolved["department"]

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(hammer, range(8)))

    assert results == ["revenue_cycle"] * 8
