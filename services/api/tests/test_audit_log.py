"""Registro de Auditoría (BRIEFING de HealthCore + README de la clase).

Mapa de "Qué vamos a evaluar" → tests:

1. Una entrada no puede modificarse ni eliminarse desde la aplicación:
   `test_orm_*_is_refused`, `test_bulk_sql_*_is_refused_by_the_database`,
   `test_the_audit_api_has_no_write_routes`, `test_no_code_path_updates_or_deletes_entries`.
2. Las acciones sensibles quedan registradas (actor, acción, recurso, fecha):
   tests de login, usuarios, roles y `/patients`.
3. Procesos/agentes diferenciados: `test_documentation_assistant_is_recorded_as_a_process`
   (+ los de onboarding y cierre financiero en tests/ de la raíz).
4. Visor con filtros y restricciones por rol y departamento: `test_viewer_*`.
5. Alterar manualmente una entrada rompe la cadena: `test_manual_tampering_*`.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, update
from sqlalchemy.exc import DBAPIError
from sqlmodel import Session, select

from access_models import department_id_for, role_id_for
from auth_repository import AuthRepository
from main import app
from security import create_access_token, hash_password
from services.audit import catalog
from services.audit.chain import BROKEN_LINK, BROKEN_MISSING, BROKEN_TAMPERED, compute_entry_hash, verify_chain
from services.audit.models import AuditLogEntry, AuditLogImmutableError
from services.audit.writer import human_actor, process_actor, record
from services.patients import seed_data

PASSWORD = "Semilla-Audit-2026"
SEED_HASH = hash_password(PASSWORD)  # un solo bcrypt por módulo

STAFF = {
    # clave: (rol, departamento, nombre)
    "claire": ("admin", "compliance", "Claire Whitfield"),
    "marcus": ("supervisor", "clinical_operations", "Dr. Marcus Reid"),
    "nina": ("employee", "clinical_operations", "Nina Patel"),
    "priya": ("supervisor", "patient_experience", "Priya Nair"),
    "tom": ("supervisor", "revenue_cycle", "Tom Callahan"),
    "leo": ("employee", "revenue_cycle", "Leo Grant"),
}

REF_1 = seed_data.patient_ref_for(1)  # clinical_operations, asignado a Nina
REF_2 = seed_data.patient_ref_for(2)  # clinical_operations, asignado a Nina
REF_6 = seed_data.patient_ref_for(6)  # patient_experience


@dataclass
class World:
    client: TestClient
    engine: object
    ids: dict[str, str]

    def headers(self, who: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {create_access_token(self.ids[who], STAFF[who][0])}"}

    def entries(self) -> list[AuditLogEntry]:
        with Session(self.engine) as session:
            return list(session.exec(select(AuditLogEntry).order_by(AuditLogEntry.sequence)).all())


@pytest.fixture()
def world(client: TestClient, inventory_engine) -> World:
    repo = AuthRepository()
    ids = {}
    for key, (role, department, name) in STAFF.items():
        user, _ = repo.create_user(
            email=f"{key}@healthcore-demo.com",
            hashed_password=SEED_HASH,
            role=role,
            department=department,
            profile_data={"name": name},
        )
        ids[key] = user["id"]
    with Session(inventory_engine) as session:
        for patient, claims in seed_data.build_patients({1: ids["nina"], 2: ids["nina"]}):
            session.add(patient)
            session.flush()
            session.add_all(claims)
        session.commit()
    return World(client=client, engine=inventory_engine, ids=ids)


def view(world: World, who: str, ref: str, purpose: str = "routine_review"):
    return world.client.get(f"/patients/{ref}", params={"purpose": purpose}, headers=world.headers(who))


def write_entries(world: World, count: int) -> None:
    with Session(world.engine) as session:
        for _ in range(count):
            record(session, actor=human_actor(world.ids["nina"], "ip:10.0.0.1"), action="login_succeeded")
        session.commit()


# --- 1. Inmutabilidad --------------------------------------------------------------------


def test_orm_update_is_refused(world: World) -> None:
    write_entries(world, 1)
    with Session(world.engine) as session:
        entry = session.exec(select(AuditLogEntry)).one()
        entry.outcome = "denied"
        session.add(entry)
        with pytest.raises(AuditLogImmutableError):
            session.commit()


def test_orm_delete_is_refused(world: World) -> None:
    write_entries(world, 1)
    with Session(world.engine) as session:
        session.delete(session.exec(select(AuditLogEntry)).one())
        with pytest.raises(AuditLogImmutableError):
            session.commit()


def test_bulk_sql_update_is_refused_by_the_database(world: World) -> None:
    """El SQL masivo no pasa por el mapper: lo para el trigger."""
    write_entries(world, 2)
    with Session(world.engine) as session:
        with pytest.raises(DBAPIError, match="append-only"):
            session.execute(update(AuditLogEntry).values(outcome="denied"))
    assert {entry.outcome for entry in world.entries()} == {"success"}


def test_bulk_sql_delete_is_refused_by_the_database(world: World) -> None:
    write_entries(world, 2)
    with Session(world.engine) as session:
        with pytest.raises(DBAPIError, match="append-only"):
            session.execute(delete(AuditLogEntry))
    assert len(world.entries()) == 2


def test_the_audit_api_has_no_write_routes() -> None:
    methods = {
        method
        for route in app.routes
        if getattr(route, "path", "").startswith("/audit-log")
        for method in getattr(route, "methods", set())
    }
    assert methods and methods <= {"GET", "HEAD"}


def test_no_code_path_updates_or_deletes_entries() -> None:
    """Ningún módulo de la aplicación construye un UPDATE/DELETE sobre la tabla."""
    root = Path(__file__).resolve().parents[3]
    offenders = []
    for folder in (root / "services", root / "data", root / "scripts", root / "mcps"):
        for path in folder.rglob("*.py"):
            if ".venv" in path.parts or "tests" in path.parts:
                continue
            text = path.read_text(encoding="utf-8")
            if any(
                needle in text
                for needle in ("update(AuditLogEntry", "delete(AuditLogEntry", "UPDATE audit_log_entries", "DELETE FROM audit_log_entries")
            ):
                offenders.append(str(path.relative_to(root)))
    assert offenders == []


def test_writer_refuses_entries_dated_in_the_future(world: World) -> None:
    with Session(world.engine) as session:
        with pytest.raises(ValueError, match="future"):
            record(
                session,
                actor=human_actor(world.ids["nina"], "ip:10.0.0.1"),
                action="login_succeeded",
                occurred_at=datetime.now(timezone.utc) + timedelta(minutes=5),
            )


# --- 2. Sin PHI en el registro (BRIEFING §4) -------------------------------------------------


@pytest.mark.parametrize(
    "details",
    [
        {"free_text": "lo que sea"},  # clave fuera de lista
        {"reason_code": "paciente@correo.com"},  # forma de email
        {"reason_code": "+44 7700 900123"},  # forma de teléfono
        {"patient_refs": ["Jordan Testwell"]},  # no es una ref opaca
        {"target_user": "ana@x.com"},
    ],
)
def test_writer_refuses_details_that_could_carry_patient_data(world: World, details) -> None:
    with Session(world.engine) as session:
        with pytest.raises(ValueError):
            record(session, actor=human_actor(world.ids["nina"], "ip:10.0.0.1"), action="login_succeeded", details=details)


def test_writer_requires_an_opaque_patient_ref_for_patient_actions(world: World) -> None:
    with Session(world.engine) as session:
        actor = human_actor(world.ids["nina"], "ip:10.0.0.1")
        with pytest.raises(ValueError, match="patient_ref"):
            record(session, actor=actor, action="patient_record_viewed")
        with pytest.raises(ValueError, match="opaque"):
            record(session, actor=actor, action="patient_record_viewed", patient_ref="Jordan Testwell")


def test_writer_only_accepts_known_processes(world: World) -> None:
    with Session(world.engine) as session:
        with pytest.raises(ValueError, match="process"):
            record(session, actor=process_actor("rogue_script"), action="login_succeeded")


def test_department_ids_match_the_platform(world: World) -> None:
    for code in ("clinical_operations", "revenue_cycle", "people_workforce"):
        assert catalog.department_id(code) == department_id_for(code)


# --- 2. Instrumentación: autenticación y usuarios ----------------------------------------------


def test_successful_and_failed_logins_are_recorded_without_email(world: World) -> None:
    ok = world.client.post("/auth/login", json={"email": "nina@healthcore-demo.com", "password": PASSWORD})
    bad = world.client.post("/auth/login", json={"email": "nobody@healthcore-demo.com", "password": "wrong-password"})
    assert (ok.status_code, bad.status_code) == (200, 401)

    success, failure = world.entries()
    assert (success.action, success.actor_type, success.actor_id) == ("login_succeeded", "human", world.ids["nina"])
    assert success.department_id == department_id_for("clinical_operations")
    assert success.origin.startswith("ip:")
    assert (failure.action, failure.actor_type, failure.outcome) == ("login_failed", "anonymous", "denied")
    assert "identifier_hash" in json.loads(failure.details)
    for entry in (success, failure):
        assert "@healthcore-demo.com" not in json.dumps(entry.model_dump())


def test_user_lifecycle_is_recorded_in_the_target_department(world: World) -> None:
    created = world.client.post(
        "/users",
        json={
            "email": "new.hire@healthcore-demo.com",
            "password": "Temporal-2026!",
            "role_id": role_id_for("employee"),
            "department_id": department_id_for("clinical_operations"),
        },
        headers=world.headers("claire"),
    )
    assert created.status_code == 201
    new_id = created.json()["id"]
    moved = world.client.patch(
        f"/users/{new_id}/assignment",
        json={"department_id": department_id_for("revenue_cycle")},
        headers=world.headers("claire"),
    )
    removed = world.client.delete(f"/users/{new_id}", headers=world.headers("claire"))
    assert (moved.status_code, removed.status_code) == (200, 204)

    actions = [(e.action, e.resource_id, e.department_id, e.actor_id) for e in world.entries()]
    clinical, revenue = department_id_for("clinical_operations"), department_id_for("revenue_cycle")
    assert actions == [
        ("user_created", new_id, clinical, world.ids["claire"]),
        ("user_assignment_changed", new_id, revenue, world.ids["claire"]),
        ("user_deleted", new_id, revenue, world.ids["claire"]),
    ]
    change = json.loads(world.entries()[1].details)
    assert (change["from_department"], change["to_department"]) == ("clinical_operations", "revenue_cycle")


def test_role_changes_are_recorded_for_admins_only(world: World) -> None:
    response = world.client.post(
        "/roles", json={"code": "lead", "name": "Lead", "rank": 2}, headers=world.headers("claire")
    )
    assert response.status_code == 201
    (entry,) = world.entries()
    assert (entry.action, entry.resource_type, entry.department_id) == ("role_created", "role", None)


# --- 2 y 3. Instrumentación: pacientes --------------------------------------------------------


def test_opening_a_record_needs_a_purpose_and_is_recorded(world: World) -> None:
    missing = world.client.get(f"/patients/{REF_1}", headers=world.headers("nina"))
    assert missing.status_code == 400 and missing.json()["detail"]["errors"][0]["field"] == "purpose"

    response = view(world, "nina", REF_1, "referral_resolution")
    assert response.status_code == 200
    body = response.json()
    assert body["clinical"]["full_name"].endswith("(sintético)")

    (entry,) = world.entries()
    assert body["audit_sequence"] == entry.sequence
    assert (entry.action, entry.patient_ref, entry.context) == ("patient_record_viewed", REF_1, "referral_resolution")
    assert (entry.actor_type, entry.actor_id, entry.resource_type) == ("human", world.ids["nina"], "patient_record")
    assert json.loads(entry.details) == {"sections": ["clinical", "billing"]}
    # BRIEFING §4: ni nombre ni diagnóstico en el registro.
    dumped = json.dumps(entry.model_dump())
    assert "Jordan" not in dumped and "Hipertensión" not in dumped


def test_access_outside_the_care_team_is_denied_and_recorded(world: World) -> None:
    """Admin incluido: ve quién accedió a qué, no el contenido clínico."""
    for who in ("priya", "claire"):
        assert view(world, who, REF_1).status_code == 403
    entries = world.entries()
    assert [(e.actor_id, e.outcome) for e in entries] == [
        (world.ids["priya"], "denied"),
        (world.ids["claire"], "denied"),
    ]
    assert {e.department_id for e in entries} == {department_id_for("clinical_operations")}


def test_revenue_cycle_sees_billing_but_not_clinical_content(world: World) -> None:
    body = view(world, "leo", REF_1, "billing_review").json()
    assert body["clinical"] is None and body["billing"]["claims"]
    (entry,) = world.entries()
    assert entry.department_id == department_id_for("revenue_cycle")
    assert json.loads(entry.details) == {"sections": ["billing"]}


def test_clinical_update_records_which_fields_changed_but_not_the_values(world: World) -> None:
    response = world.client.patch(
        f"/patients/{REF_1}/clinical",
        json={"purpose": "care_coordination", "clinical_notes": "Nota nueva confidencial de prueba"},
        headers=world.headers("marcus"),
    )
    assert response.status_code == 200
    (entry,) = world.entries()
    assert entry.action == "patient_clinical_updated"
    assert json.loads(entry.details) == {"changed_fields": ["clinical_notes"]}
    assert "confidencial" not in json.dumps(entry.model_dump())


def test_clinical_update_needs_a_supervisor(world: World) -> None:
    response = world.client.patch(
        f"/patients/{REF_1}/clinical",
        json={"purpose": "care_coordination", "diagnosis": "x"},
        headers=world.headers("nina"),
    )
    assert response.status_code == 403 and response.json()["detail"]["code"] == "insufficient_role"


def test_billing_update_and_claim_decision_are_recorded_for_revenue_cycle(world: World) -> None:
    headers = world.headers("tom")
    billing = world.client.patch(
        f"/patients/{REF_2}/billing",
        json={"purpose": "billing_review", "outstanding_balance_cents": 5000},
        headers=headers,
    )
    claim = billing.json()["billing"]["claims"][0]["claim_ref"]
    decided = world.client.post(
        f"/patients/{REF_2}/claims/{claim}/decision",
        json={"decision": "deny", "reason_code": "missing_documentation"},
        headers=headers,
    )
    again = world.client.post(
        f"/patients/{REF_2}/claims/{claim}/decision",
        json={"decision": "approve", "reason_code": "covered_service"},
        headers=headers,
    )
    assert (billing.status_code, decided.status_code, again.status_code) == (200, 200, 409)

    billing_entry, claim_entry = world.entries()
    assert billing_entry.action == "patient_billing_updated"
    assert json.loads(billing_entry.details) == {"changed_fields": ["outstanding_balance_cents"]}
    assert (claim_entry.action, claim_entry.resource_type) == ("insurance_claim_denied", "insurance_claim")
    assert json.loads(claim_entry.details) == {"claim_ref": claim, "reason_code": "missing_documentation"}


def test_care_team_cannot_change_billing(world: World) -> None:
    response = world.client.patch(
        f"/patients/{REF_1}/billing",
        json={"purpose": "billing_review", "insurance_plan": "Otro"},
        headers=world.headers("marcus"),
    )
    assert response.status_code == 403
    (entry,) = world.entries()
    assert (entry.action, entry.outcome) == ("patient_billing_updated", "denied")


def test_documentation_assistant_is_recorded_as_a_process(world: World) -> None:
    response = world.client.post(
        f"/patients/{REF_1}/documentation-draft", json={"purpose": "care_coordination"}, headers=world.headers("nina")
    )
    assert response.status_code == 200
    assert response.json()["clinical"]["documentation_draft"].startswith("Borrador generado")
    (entry,) = world.entries()
    assert (entry.actor_type, entry.actor_id) == ("process", "clinical_documentation_assistant")
    assert entry.on_behalf_of == world.ids["nina"]
    assert entry.origin.startswith("process:clinical_documentation_assistant:pid")


def test_every_export_is_recorded_with_the_refs_it_contained(world: World) -> None:
    response = world.client.post("/patients/export", json={"purpose": "compliance_investigation"}, headers=world.headers("marcus"))
    assert response.status_code == 200 and response.headers["content-type"].startswith("text/csv")
    rows = response.text.strip().splitlines()
    assert len(rows) == 1 + 5  # cabecera + los 5 pacientes de Operaciones clínicas

    (entry,) = world.entries()
    details = json.loads(entry.details)
    assert entry.action == "patient_data_exported" and entry.resource_type == "patient_export"
    assert details["record_count"] == 5 and REF_1 in details["patient_refs"]
    assert response.headers["X-Audit-Sequence"] == str(entry.sequence)


def test_exporting_patients_outside_the_scope_is_denied_and_recorded(world: World) -> None:
    response = world.client.post(
        "/patients/export", json={"purpose": "routine_review", "patient_refs": [REF_6]}, headers=world.headers("marcus")
    )
    assert response.status_code == 403
    (entry,) = world.entries()
    assert (entry.action, entry.outcome) == ("patient_data_exported", "denied")


# --- 4. Visor: filtros y restricciones -----------------------------------------------------------


def _activity(world: World) -> None:
    view(world, "nina", REF_1)  # clinical_operations
    view(world, "priya", REF_6)  # patient_experience
    view(world, "leo", REF_2, "billing_review")  # revenue_cycle


def test_viewer_is_closed_to_employees(world: World) -> None:
    response = world.client.get("/audit-log", headers=world.headers("nina"))
    assert response.status_code == 403 and response.json()["detail"]["code"] == "insufficient_role"


def test_viewer_scopes_supervisors_to_their_department(world: World) -> None:
    _activity(world)
    marcus = world.client.get("/audit-log", headers=world.headers("marcus")).json()
    priya = world.client.get("/audit-log", headers=world.headers("priya")).json()
    assert marcus["scope"] == "clinical_operations"
    assert [item["patient_ref"] for item in marcus["items"]] == [REF_1]
    assert [item["patient_ref"] for item in priya["items"]] == [REF_6]


def test_viewer_shows_everything_to_admins_with_labels(world: World) -> None:
    _activity(world)
    body = world.client.get("/audit-log", headers=world.headers("claire")).json()
    assert body["scope"] == "all" and body["total"] == 3
    assert {item["actor_label"] for item in body["items"]} == {"Nina Patel", "Priya Nair", "Leo Grant"}


def test_viewer_filters_by_actor_action_resource_and_date(world: World) -> None:
    _activity(world)
    world.client.post("/auth/login", json={"email": "nina@healthcore-demo.com", "password": PASSWORD})
    headers = world.headers("claire")

    def refs(**params):
        return [item["sequence"] for item in world.client.get("/audit-log", params=params, headers=headers).json()["items"]]

    assert refs(actor_id=world.ids["nina"]) == [4, 1]
    assert refs(action="login_succeeded") == [4]
    assert refs(resource_type="patient_record", patient_ref=REF_6) == [2]
    tomorrow = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    assert refs(date_from=yesterday, date_to=tomorrow) == [4, 3, 2, 1]
    assert refs(date_from=tomorrow) == []
    bad = world.client.get("/audit-log", params={"date_from": "ayer"}, headers=headers)
    assert bad.status_code == 400


def test_viewer_paginates_with_a_cursor(world: World) -> None:
    write_entries(world, 7)
    headers = world.headers("claire")
    first = world.client.get("/audit-log", params={"limit": 3}, headers=headers).json()
    second = world.client.get("/audit-log", params={"limit": 3, "before": first["next_cursor"]}, headers=headers).json()
    last = world.client.get("/audit-log", params={"limit": 3, "before": second["next_cursor"]}, headers=headers).json()
    assert [i["sequence"] for i in first["items"]] == [7, 6, 5]
    assert [i["sequence"] for i in second["items"]] == [4, 3, 2]
    assert [i["sequence"] for i in last["items"]] == [1] and last["next_cursor"] is None
    assert world.client.get("/audit-log", params={"limit": 500}, headers=headers).status_code == 422


def test_employees_see_who_accessed_their_assigned_patients(world: World) -> None:
    view(world, "marcus", REF_1)  # paciente de Nina
    view(world, "marcus", seed_data.patient_ref_for(3))  # no asignado a Nina
    body = world.client.get("/audit-log/my-patients", headers=world.headers("nina")).json()
    assert [(i["patient_ref"], i["actor_label"]) for i in body["items"]] == [(REF_1, "Dr. Marcus Reid")]
    assert "origin" not in body["items"][0]


def test_integrity_check_is_admin_only(world: World) -> None:
    assert world.client.get("/audit-log/integrity", headers=world.headers("marcus")).status_code == 403


# --- 5. Evidencia de alteración ----------------------------------------------------------------


def _as_dba(world: World, *statements: str) -> None:
    """Lo que haría alguien con acceso directo a la base de datos: quitar el
    trigger y editar a mano. La aplicación no tiene esta ruta."""
    with world.engine.begin() as connection:
        connection.exec_driver_sql("DROP TRIGGER IF EXISTS audit_log_entries_no_update")
        connection.exec_driver_sql("DROP TRIGGER IF EXISTS audit_log_entries_no_delete")
        for statement in statements:
            connection.exec_driver_sql(statement)


def _integrity(world: World) -> dict:
    return world.client.get("/audit-log/integrity", headers=world.headers("claire")).json()


def test_an_untouched_chain_verifies(world: World) -> None:
    _activity(world)
    result = _integrity(world)
    assert result["ok"] is True and result["checked"] == 3 and result["head_sequence"] == 3


def test_manual_tampering_of_an_entry_breaks_the_chain(world: World) -> None:
    _activity(world)
    _as_dba(world, "UPDATE audit_log_entries SET context = 'compliance_investigation' WHERE sequence = 2")
    result = _integrity(world)
    assert (result["ok"], result["broken_at"], result["reason"]) == (False, 2, BROKEN_TAMPERED)


def test_recomputing_the_tampered_hash_still_breaks_the_next_link(world: World) -> None:
    _activity(world)
    _as_dba(world, "UPDATE audit_log_entries SET actor_id = 'someone-else' WHERE sequence = 2")
    with Session(world.engine) as session:
        forged = session.exec(select(AuditLogEntry).where(AuditLogEntry.sequence == 2)).one()
        new_hash = compute_entry_hash(forged)
    _as_dba(world, f"UPDATE audit_log_entries SET entry_hash = '{new_hash}' WHERE sequence = 2")
    result = _integrity(world)
    assert (result["ok"], result["broken_at"], result["reason"]) == (False, 3, BROKEN_LINK)


def test_deleting_an_entry_in_the_middle_is_detected(world: World) -> None:
    _activity(world)
    _as_dba(world, "DELETE FROM audit_log_entries WHERE sequence = 2")
    result = _integrity(world)
    assert (result["ok"], result["broken_at"], result["reason"]) == (False, 3, BROKEN_MISSING)


def test_verify_chain_on_an_empty_log_is_ok() -> None:
    verdict = verify_chain([])
    assert verdict.ok and verdict.checked == 0 and verdict.head_sequence is None


# --- Accesos inusuales (BRIEFING §5) -------------------------------------------------------------

from services.audit.anomalies import MIN_RECENT_ACCESSES, detect_unusual_access, is_unusual_access  # noqa: E402


@pytest.mark.parametrize(
    "recent, baseline, expected",
    [
        (24, 0.3, True),  # la ráfaga de Leo
        (2, 0.0, False),  # primer día de alguien sin historial
        (MIN_RECENT_ACCESSES - 1, 0.0, False),  # por debajo del mínimo, nunca
        (40, 0.0, True),  # sin historial pero volumen muy alto
        (25, 20.0, False),  # mucho volumen habitual
        (60, 20.0, True),  # el triple de su media
    ],
)
def test_unusual_access_rule(recent: int, baseline: float, expected: bool) -> None:
    assert is_unusual_access(recent, baseline) is expected


def _views(world: World, who: str, ref: str, department: str, moments: list[datetime]) -> None:
    with Session(world.engine) as session:
        for moment in moments:
            record(
                session,
                actor=human_actor(world.ids[who], "ip:10.0.0.9"),
                action="patient_record_viewed",
                patient_ref=ref,
                department_id=department_id_for(department),
                context="routine_review",
                occurred_at=moment,
            )
        session.commit()


def test_detection_flags_a_burst_against_the_actors_own_history(world: World) -> None:
    now = datetime.now(timezone.utc)
    # Nina: 2 al día durante 30 días y 3 hoy → normal.
    _views(world, "nina", REF_1, "clinical_operations", [now - timedelta(days=d, hours=h) for d in range(2, 31) for h in (1, 2)])
    _views(world, "nina", REF_1, "clinical_operations", [now - timedelta(hours=h) for h in (1, 2, 3)])
    # Leo: casi nada antes y 24 en las últimas 3 horas → inusual.
    _views(world, "leo", REF_2, "revenue_cycle", [now - timedelta(days=10)])
    _views(world, "leo", REF_2, "revenue_cycle", [now - timedelta(minutes=5 * m + 1) for m in range(24)])

    with Session(world.engine) as session:
        flagged = detect_unusual_access(session, now=now)
    assert [(item.actor_id, item.recent_count) for item in flagged] == [(world.ids["leo"], 24)]

    marcus = world.client.get("/audit-log/anomalies", headers=world.headers("marcus")).json()
    tom = world.client.get("/audit-log/anomalies", headers=world.headers("tom")).json()
    assert marcus["items"] == []  # el de Leo es de Revenue Cycle
    assert [item["actor_label"] for item in tom["items"]] == ["Leo Grant"]
