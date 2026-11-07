"""API de proveedores y contratos (/supplier-contracts).

Misma SQLite en memoria de la fixture `client`, con los datos del BRIEFING
generados en un directorio temporal y la fecha de carga fija. Sin modelo
(`router.USE_MODEL = False`): plan por reglas y borradores de plantilla.
"""

from __future__ import annotations

from datetime import date
from typing import Dict

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

from auth_repository import AuthRepository
from models import UserRole
from security import create_access_token, hash_password
from services.supplier_contracts import ingestion, repository, router as sc_router, seed_data
from services.supplier_contracts.catalog import PEOPLE, SENSITIVE_BILLING

TODAY = date(2026, 11, 7)
PASSWORD_HASH = hash_password("Secret12345")
ROLES = {"marcus": "clinical_ops_director", "claire": "compliance_officer", "sandra": "ceo"}


@pytest.fixture()
def people(client: TestClient, inventory_engine, tmp_path, monkeypatch) -> Dict[str, Dict[str, str]]:
    seed_data.generate_all(tmp_path, TODAY)
    repo = AuthRepository()
    accounts: Dict[str, Dict[str, str]] = {}
    for key in list(ROLES) + ["analyst"]:
        user, _ = repo.create_user(email=f"{key}@healthcore.test", hashed_password=PASSWORD_HASH, role=UserRole.user,
                                   profile_data={"name": key})
        accounts[key] = {"id": user["id"],
                         "headers": {"Authorization": f"Bearer {create_access_token(subject=user['id'], role='user')}"}}
    with Session(inventory_engine) as session:
        repository.replace_dataset(session, ingestion.read_dataset(tmp_path, TODAY))
        for key, role in ROLES.items():
            repository.upsert_staff(session, accounts[key]["id"], PEOPLE[role], role)
    monkeypatch.setattr(sc_router, "USE_MODEL", False)
    monkeypatch.setattr(sc_router, "_today", lambda: TODAY)
    return accounts


def test_requires_authentication(client: TestClient):
    assert client.get("/supplier-contracts/suppliers").status_code == 401


def test_suppliers_list_carries_source_reference(client, people):
    response = client.get("/supplier-contracts/suppliers", headers=people["analyst"]["headers"])
    assert response.status_code == 200
    rows = {r["supplier_id"]: r for r in response.json()}
    assert len(rows) == 7 and rows["HC-S05"]["contract_id"] is None
    assert rows["HC-S03"]["source"] == "suppliers.csv fila 4"


def test_contract_terms_endpoint(client, people):
    body = client.get("/supplier-contracts/suppliers/HC-S06/contract", headers=people["analyst"]["headers"]).json()
    terms = {t["term"]: t for t in body["terms"]}
    assert terms["data_protection"]["status"] == "missing"
    assert terms["price_review"]["clause"] == "4.2" and terms["price_review"]["page"] == 2
    no_contract = client.get("/supplier-contracts/suppliers/HC-S05/contract", headers=people["analyst"]["headers"]).json()
    assert no_contract["contract"] is None and "No hay contrato" in no_contract["message"]


def test_sweep_requires_role_and_routes_alerts(client, people):
    assert client.post("/supplier-contracts/alerts/sweep", headers=people["analyst"]["headers"]).status_code == 403
    response = client.post("/supplier-contracts/alerts/sweep", headers=people["marcus"]["headers"])
    assert response.status_code == 200 and response.json()["created"] == 8
    mine = client.get("/supplier-contracts/alerts?mine=true", headers=people["claire"]["headers"]).json()
    assert {a["kind"] for a in mine} == {"data_protection_missing"}
    assert all(a["recipient"] == "Claire Whitfield" for a in mine)


def test_ask_validates_input(client, people):
    headers = people["analyst"]["headers"]
    assert client.post("/supplier-contracts/ask", json={"question": ""}, headers=headers).status_code == 400
    response = client.post("/supplier-contracts/ask", json={"question": "x", "iban": "GB29"}, headers=headers)
    assert response.status_code == 400 and response.json()["detail"]["errors"][0]["field"] == "iban"
    assert "GB29" not in response.text  # el error nombra el campo, nunca repite su valor


def test_ask_answers_with_citations(client, people):
    response = client.post("/supplier-contracts/ask", json={"question": "¿ClearPath Labs puede subirnos un 7%?"},
                           headers=people["analyst"]["headers"])
    body = response.json()
    assert response.status_code == 200 and "No permitida" in body["answer"]
    assert "CT-HC-02.pdf §4.2, p. 2" in body["citations"]


def test_brief_for_supplier_without_dpa_is_409_until_cleared(client, people):
    headers = people["marcus"]["headers"]
    blocked = client.post("/supplier-contracts/briefs", json={"supplier_id": "HC-S06"}, headers=headers)
    assert blocked.status_code == 409 and blocked.json()["status"] == "blocked"
    # Solo Cumplimiento puede autorizar a continuar.
    assert client.post("/supplier-contracts/compliance-clearances", json={"supplier_id": "HC-S06"},
                       headers=headers).status_code == 403
    assert client.post("/supplier-contracts/compliance-clearances", json={"supplier_id": "HC-S06"},
                       headers=people["claire"]["headers"]).status_code == 201


def test_draft_approval_flow_over_http(client, people):
    created = client.post("/supplier-contracts/drafts", json={"supplier_id": "HC-S03", "purpose": "non_renewal_notice"},
                          headers=people["analyst"]["headers"])
    assert created.status_code == 201
    draft = created.json()
    assert [a["role"] for a in draft["approvals"]] == ["clinical_ops_director", "compliance_officer", "ceo"]
    draft_id = draft["draft_id"]
    send = f"/supplier-contracts/drafts/{draft_id}/send"
    assert client.post(send, headers=people["marcus"]["headers"]).status_code == 409
    decision = f"/supplier-contracts/drafts/{draft_id}/decision"
    assert client.post(decision, json={"decision": "approved"}, headers=people["analyst"]["headers"]).status_code == 403
    for key in ("marcus", "claire", "sandra"):
        assert client.post(decision, json={"decision": "approved"}, headers=people[key]["headers"]).status_code == 200
    sent = client.post(send, headers=people["marcus"]["headers"])
    assert sent.status_code == 200 and sent.json()["status"] == "sent"


def test_responses_never_contain_sensitive_billing_data(client, people):
    headers = people["marcus"]["headers"]
    texts = [client.post("/supplier-contracts/alerts/sweep", headers=headers).text]
    for sid in ("HC-S01", "HC-S03", "HC-S06", "HC-S07"):
        texts.append(client.get(f"/supplier-contracts/suppliers/{sid}/contract", headers=headers).text)
    texts.append(client.get("/supplier-contracts/alerts", headers=headers).text)
    texts.append(client.get("/supplier-contracts/actions", headers=headers).text)
    blob = "\n".join(texts)
    for billing in SENSITIVE_BILLING.values():
        for value in billing.values():
            assert value not in blob


def test_actions_log_is_restricted_to_approvers(client, people):
    assert client.get("/supplier-contracts/actions", headers=people["analyst"]["headers"]).status_code == 403
    client.post("/supplier-contracts/ask", json={"question": "¿Qué dice el contrato de BritMed Supplies?"},
                headers=people["analyst"]["headers"])
    actions = client.get("/supplier-contracts/actions", headers=people["sandra"]["headers"]).json()
    assert {"agent:supplier-supervisor", "agent:contracts-specialist"} <= {a["actor"] for a in actions}
    assert all(not a["actor"].startswith("human:") or len(a["actor"]) == len("human:") + 12 for a in actions)
