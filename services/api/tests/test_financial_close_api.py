"""API del cierre financiero (/financial-close).

Misma SQLite en memoria de la fixture `client`. Los datos del BRIEFING se
generan en un directorio temporal y el informe se prepara sin modelo. La cola
de Celery se sustituye por una lista (`enqueued`).
"""

from __future__ import annotations

import json
from datetime import date
from typing import Dict, List

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

from auth_repository import AuthRepository
from models import UserRole
from security import create_access_token, hash_password
from services.financial_close import repository, seed_data, workflow
from services.financial_close.catalog import PEOPLE

LOAD_DATE = date(2026, 11, 7)
PERIOD = date(2026, 10, 1)
PASSWORD_HASH = hash_password("Secret12345")
ROLES = {"tom": "revenue_cycle_director", "sandra": "ceo", "claire": "compliance_officer"}
PATIENT_NAME = "Emily Hartwell"


@pytest.fixture()
def setup(client: TestClient, inventory_engine, tmp_path, monkeypatch):
    seed_data.generate_all(tmp_path, LOAD_DATE)
    repo = AuthRepository()
    people: Dict[str, Dict[str, str]] = {}
    for key in list(ROLES) + ["outsider"]:
        user, _ = repo.create_user(email=f"{key}@healthcore.test", hashed_password=PASSWORD_HASH, role=UserRole.user,
                                   profile_data={"name": key})
        people[key] = {"id": user["id"],
                       "headers": {"Authorization": f"Bearer {create_access_token(subject=user['id'], role='user')}"}}
    admin, _ = repo.create_user(email="admin@healthcore.test", hashed_password=PASSWORD_HASH, role=UserRole.admin,
                                profile_data={"name": "admin"})
    people["admin"] = {"id": admin["id"],
                       "headers": {"Authorization": f"Bearer {create_access_token(subject=admin['id'], role='admin')}"}}
    enqueued: List[tuple] = []
    import services.tasks.financial_close_tasks as tasks

    monkeypatch.setattr(tasks, "enqueue_revision", lambda report_id: enqueued.append(("revise", str(report_id))) or "t1")
    monkeypatch.setattr(tasks, "enqueue_close", lambda period, trigger: enqueued.append(("close", period, trigger)) or "t2")
    with Session(inventory_engine) as session:
        for key, role in ROLES.items():
            repository.upsert_staff(session, people[key]["id"], PEOPLE[role], role)
        workflow.ingest_period(session, tmp_path, PERIOD)
        report = workflow.run_close(session, inventory_engine, PERIOD, trigger="manual", use_model=False)
        report_id = str(report.id)
    return {"people": people, "report_id": report_id, "enqueued": enqueued}


def _get(client, path, who, setup):
    return client.get(path, headers=setup["people"][who]["headers"])


def test_requires_authentication(client):
    assert client.get("/financial-close/reports").status_code == 401


def test_only_close_directory_reads_reports(client, setup):
    assert _get(client, "/financial-close/reports", "outsider", setup).status_code == 403
    listing = _get(client, "/financial-close/reports", "sandra", setup).json()
    assert [(r["version"], r["status"]) for r in listing] == [(1, "pending_review")]


def test_report_in_both_languages(client, setup):
    rid = setup["report_id"]
    en = _get(client, f"/financial-close/reports/{rid}", "tom", setup).json()
    es = client.get(f"/financial-close/reports/{rid}?lang=es", headers=setup["people"]["tom"]["headers"]).json()
    assert en["markdown"].startswith("# HealthCore — Monthly financial close, October 2026")
    assert es["markdown"].startswith("# HealthCore — Cierre financiero mensual, octubre de 2026")
    assert en["figures"] == es["figures"] and en["can_decide"] is True
    assert _get(client, f"/financial-close/reports/{rid}", "sandra", setup).json()["can_decide"] is False
    bad = client.get(f"/financial-close/reports/{rid}?lang=fr", headers=setup["people"]["tom"]["headers"])
    assert bad.status_code == 400


def test_trace_of_a_figure_lists_its_ledger_records(client, setup):
    rid = setup["report_id"]
    body = _get(client, f"/financial-close/reports/{rid}/figures/line:US.n/a.laboratory:actual", "tom", setup).json()
    assert body["value"] == "48150.00"
    assert [r["entry_id"] for r in body["ledger_records"]] == ["JE-2026OCT-0027", "JE-2026OCT-0028", "JE-2026OCT-0029"]
    assert body["ledger_records"][0]["source_file"] == "ledger.csv" and body["ledger_records"][0]["source_row"] == 28
    assert "32.10 USD" in body["ledger_records"][0]["description"]
    missing = _get(client, f"/financial-close/reports/{rid}/figures/nope", "tom", setup)
    assert missing.status_code == 404


def test_reject_needs_reviewer_and_comments_and_enqueues_a_revision(client, setup):
    rid = setup["report_id"]
    url = f"/financial-close/reports/{rid}/decision"
    sandra = setup["people"]["sandra"]["headers"]
    tom = setup["people"]["tom"]["headers"]
    assert client.post(url, json={"decision": "approve"}, headers=sandra).status_code == 403
    assert client.post(url, json={"decision": "reject"}, headers=tom).status_code == 400
    phi = client.post(url, json={"decision": "reject", "comments": "Refund to patient Emily Hartwell"}, headers=tom)
    assert phi.status_code == 400 and PATIENT_NAME not in phi.text
    assert client.post(url, json={"decision": "reject", "x": 1}, headers=tom).status_code == 400
    ok = client.post(url, json={"decision": "reject", "comments": "Shorten the summary."}, headers=tom)
    assert ok.status_code == 200 and ok.json()["status"] == "rejected"
    assert setup["enqueued"] == [("revise", rid)]
    assert client.post(url, json={"decision": "approve"}, headers=tom).status_code == 409


def test_approval_distributes_and_fills_inboxes(client, setup):
    rid = setup["report_id"]
    tom = setup["people"]["tom"]["headers"]
    response = client.post(f"/financial-close/reports/{rid}/decision", json={"decision": "approve"}, headers=tom)
    assert response.json()["status"] == "distributed"
    report = _get(client, f"/financial-close/reports/{rid}", "sandra", setup).json()
    assert [d["recipient_name"] for d in report["distribution"]] == [
        "Dr. Sandra Okonkwo", "James Osei", "Dr. Marcus Reid", "Priya Nair", "Tom Callahan", "Claire Whitfield",
        "Diane Foster"]
    sandra_inbox = _get(client, "/financial-close/inbox", "sandra", setup).json()
    assert [i["kind"] for i in sandra_inbox] == ["report_distributed"]
    claire_inbox = _get(client, "/financial-close/inbox", "claire", setup).json()
    assert {i["kind"] for i in claire_inbox} == {"report_distributed", "patient_data_in_source"}
    patient = next(i for i in claire_inbox if i["kind"] == "patient_data_in_source")
    assert patient["entry_id"] == "JE-2026OCT-0053"


def test_manual_run_is_admin_only_and_enqueued(client, setup):
    assert client.post("/financial-close/runs", json={"period": "2026-10"},
                       headers=setup["people"]["tom"]["headers"]).status_code == 403
    response = client.post("/financial-close/runs", json={"period": "2026-10"},
                           headers=setup["people"]["admin"]["headers"])
    assert response.status_code == 202 and response.json()["status"] == "queued"
    assert setup["enqueued"][-1] == ("close", PERIOD, "manual")
    bad = client.post("/financial-close/runs", json={"period": "octubre"}, headers=setup["people"]["admin"]["headers"])
    assert bad.status_code == 400


def test_action_log_is_admin_only_and_marks_automated_actions(client, setup):
    assert _get(client, "/financial-close/actions", "tom", setup).status_code == 403
    actions = _get(client, "/financial-close/actions", "admin", setup).json()
    assert {a["actor_type"] for a in actions} == {"automated_process"}
    assert {a["actor"] for a in actions} == {"process:agent:financial-close"}


def test_no_response_carries_patient_data(client, setup):
    rid = setup["report_id"]
    bodies = [
        _get(client, "/financial-close/reports", "tom", setup).text,
        _get(client, f"/financial-close/reports/{rid}", "tom", setup).text,
        client.get(f"/financial-close/reports/{rid}?lang=es", headers=setup["people"]["tom"]["headers"]).text,
        _get(client, "/financial-close/inbox", "claire", setup).text,
        _get(client, "/financial-close/actions", "admin", setup).text,
    ]
    report = json.loads(bodies[1])
    for figure_id in report["figures"]:
        bodies.append(_get(client, f"/financial-close/reports/{rid}/figures/{figure_id}", "tom", setup).text)
    joined = "\n".join(bodies)
    assert PATIENT_NAME not in joined and "Hartwell" not in joined


def test_me_reports_role_and_next_run(client, setup):
    me = _get(client, "/financial-close/me", "tom", setup).json()
    assert me["is_reviewer"] is True and me["display_name"] == "Tom Callahan"
    assert _get(client, "/financial-close/me", "outsider", setup).json()["org_role"] is None
