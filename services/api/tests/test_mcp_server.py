"""Servidor MCP de HealthCore (mcps/healthcore) contra el Incidents Manager real.

    cd services/api && python -m pytest tests/test_mcp_server.py

Cadena completa en memoria: cliente MCP → MCP Auth → FastMCP → API FastAPI
(TinyDB temporal + SQLite de inventario de conftest.py). Sin red ni Logto:
los tokens los firma una clave RSA local (tests/mcp_harness.py) y el servidor
los verifica con el mismo código de MCP Auth que usa en producción.
"""

from __future__ import annotations

import ast
import asyncio
import json
import logging
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from main import app as api_app
from mcps.healthcore.api_client import HealthCoreApi, InventoryReader, ReadOnlyViolation
from mcps.healthcore.errors import ERROR_CODES
from mcps.healthcore.scopes import INCIDENTS_READ, INCIDENTS_WRITE, INVENTORY_READ, TOOL_SCOPES
from mcps.healthcore.tools import classify_inventory_action
import mcp_harness as h

ROOT = Path(__file__).resolve().parents[3]
INCIDENT_PAYLOAD = {
    "title": "Monitor de constantes sin señal",
    "description": "Sala 3, sin datos de pacientes",
    "category": "clinical_equipment",
    "origin": "branch",
    "branch": "london_city",
}
EXPECTED_TOOLS = {"incidents_get", "incidents_search", "incidents_create", "incidents_update_status", "inventory_query"}


@pytest.fixture()
def supply(client: TestClient, auth_headers) -> dict:
    created = client.post(
        "/inventory/products",
        json={"name": "Guantes de nitrilo (caja de 100)", "sku": "HCR-PPE-001", "category": "ppe", "unit": "box", "country": "US"},
        headers=auth_headers,
    ).json()
    client.post(
        "/inventory/orders/inbound",
        json={"supply_id": created["id"], "quantity": 120, "vendor_name": "MedLine Industries", "clinic_id": 1},
        headers=auth_headers,
    )
    return created


def raw_post(token=None, **client_kwargs):
    """POST JSON-RPC a /mcp sin el SDK cliente: para ver la respuesta HTTP tal cual."""
    mcp_app = h.build_app(api_app)

    async def go():
        async with h.http_client(mcp_app, token, **client_kwargs) as http:
            return await http.post(
                "/mcp",
                json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
                headers={"Accept": "application/json, text/event-stream", "Content-Type": "application/json"},
            )

    return asyncio.run(h.with_lifespan(mcp_app, go))


# --- OAuth: MCP Auth como resource server -------------------------------------


def test_protected_resource_metadata_is_public_and_points_to_the_issuer():
    mcp_app = h.build_app(api_app)

    async def go():
        async with h.http_client(mcp_app) as http:
            return await http.get("/.well-known/oauth-protected-resource/mcp")

    response = asyncio.run(h.with_lifespan(mcp_app, go))
    assert response.status_code == 200
    body = response.json()
    assert body["resource"] == h.MCP_URL
    assert body["authorization_servers"] == [h.ISSUER]
    assert set(body["scopes_supported"]) == {INCIDENTS_READ, INCIDENTS_WRITE, INVENTORY_READ}


def test_without_token_tools_cannot_even_be_listed():
    response = raw_post()
    assert response.status_code == 401
    assert response.json()["error"] == "missing_auth_header"
    challenge = response.headers["www-authenticate"]
    assert challenge.startswith("Bearer") and "resource_metadata=" in challenge
    assert "/.well-known/oauth-protected-resource/mcp" in challenge


@pytest.mark.parametrize(
    "token, expected",
    [
        (lambda: h.make_token(other_key=True), "invalid_token"),
        (lambda: h.make_token(issuer="https://evil.example/oidc"), "invalid_issuer"),
        (lambda: h.make_token(audience="https://otra-api.healthcore.com"), "invalid_audience"),
        (lambda: h.make_token(expires_in=-3600), "invalid_token"),
        (lambda: "no-es-un-jwt", "invalid_token"),
    ],
    ids=["firma-ajena", "emisor-ajeno", "audiencia-ajena", "caducado", "basura"],
)
def test_invalid_tokens_are_rejected_with_401_and_their_own_code(token, expected):
    response = raw_post(token())
    assert response.status_code == 401
    assert response.json()["error"] == expected


# --- Clientes en navegador (MCP Playground): CORS + Origin -------------------

PLAYGROUND = "https://www.mcpplayground.tech"


def browser_request(method, token=None, origin=PLAYGROUND, allowed=(PLAYGROUND,)):
    import dataclasses

    mcp_app = h.build_app(api_app, dataclasses.replace(h.SETTINGS, allowed_origins=list(allowed)))
    headers = {"Origin": origin}
    if method == "OPTIONS":
        headers.update({"Access-Control-Request-Method": "POST", "Access-Control-Request-Headers": "authorization, content-type"})

    async def go():
        async with h.http_client(mcp_app, token) as http:
            if method == "OPTIONS":
                return await http.options("/mcp", headers=headers)
            return await http.post(
                "/mcp",
                json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
                headers={**headers, "Accept": "application/json, text/event-stream", "Content-Type": "application/json"},
            )

    return asyncio.run(h.with_lifespan(mcp_app, go))


def test_browser_preflight_is_answered_without_a_token():
    response = browser_request("OPTIONS")
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == PLAYGROUND
    assert "authorization" in response.headers["access-control-allow-headers"].lower()


def test_browser_request_still_needs_a_token_and_can_read_the_challenge():
    response = browser_request("POST")
    assert response.status_code == 401
    assert response.headers["access-control-allow-origin"] == PLAYGROUND
    assert "www-authenticate" in response.headers["access-control-expose-headers"].lower()


def test_allowed_browser_origin_with_token_can_list_tools():
    response = browser_request("POST", token=h.make_token())
    assert response.status_code == 200
    assert {tool["name"] for tool in response.json()["result"]["tools"]} == EXPECTED_TOOLS


def test_unlisted_browser_origin_is_rejected_even_with_a_valid_token():
    response = browser_request("POST", token=h.make_token(), origin="https://evil.example")
    assert response.status_code == 403
    assert "access-control-allow-origin" not in response.headers


def test_without_allowed_origins_there_is_no_cors_at_all():
    response = browser_request("OPTIONS", allowed=())
    assert "access-control-allow-origin" not in response.headers


# --- Discovery ----------------------------------------------------------------


def test_discovery_describes_every_tool_without_reading_the_code():
    async def fn(session):
        return (await session.list_tools()).tools

    tools = {tool.name: tool for tool in h.run_session(api_app, h.make_token(), fn)}

    assert set(tools) == EXPECTED_TOOLS
    for name, tool in tools.items():
        assert tool.title and len(tool.description) > 80, name
        assert tool.inputSchema["type"] == "object", name
        assert tool.outputSchema and tool.outputSchema["type"] == "object", name
        assert "scope" in tool.description.lower(), f"{name} debe decir qué scope necesita"
    create_props = tools["incidents_create"].inputSchema["properties"]
    assert "billing_error" in create_props["category"]["enum"]
    assert "london_city" in create_props["branch"]["enum"]
    assert tools["inventory_query"].annotations.readOnlyHint is True
    assert "read_only_resource" in tools["inventory_query"].description
    assert tools["incidents_create"].annotations.readOnlyHint is False


def test_server_instructions_explain_scopes_and_error_codes():
    mcp_app = h.build_app(api_app)
    instructions = mcp_app.state.mcp.instructions
    for word in ("incidents:read", "incidents:write", "inventory:read", *ERROR_CODES):
        assert word in instructions


# --- Incidents Manager ----------------------------------------------------------


def test_ticket_lifecycle_goes_through_the_real_incident_manager(client, auth_headers, service_account):
    token = h.make_token()

    created = h.call(api_app, token, "incidents_create", INCIDENT_PAYLOAD)
    assert not created.isError, created
    ticket = created.structuredContent
    assert ticket["status"] == "open" and ticket["allowed_next_statuses"] == ["in_progress", "discarded"]
    assert "title" not in ticket and "description" not in ticket

    # Existe de verdad en el Incidents Manager (vía la API, no vía el MCP).
    stored = client.get(f"/api/incidents/{ticket['id']}", headers=auth_headers).json()
    assert stored["title"] == INCIDENT_PAYLOAD["title"] and stored["status"] == "open"

    moved = h.call(api_app, token, "incidents_update_status", {"incident_id": ticket["id"], "status": "in_progress"})
    assert moved.structuredContent["status"] == "in_progress"
    done = h.call(api_app, token, "incidents_update_status", {"incident_id": ticket["id"], "status": "resolved"})
    assert done.structuredContent["allowed_next_statuses"] == []

    fetched = h.call(api_app, token, "incidents_get", {"incident_id": ticket["id"]})
    assert fetched.structuredContent["status"] == "resolved"
    assert client.get(f"/api/incidents/{ticket['id']}", headers=auth_headers).json()["status"] == "resolved"


def test_status_changes_use_the_lifecycle_endpoint(client, auth_headers, service_account, monkeypatch):
    seen = []
    original = HealthCoreApi.request

    async def spy(self, method, path, **kwargs):
        seen.append((method, path))
        return await original(self, method, path, **kwargs)

    monkeypatch.setattr(HealthCoreApi, "request", spy)
    ticket = h.call(api_app, h.make_token(), "incidents_create", INCIDENT_PAYLOAD).structuredContent
    h.call(api_app, h.make_token(), "incidents_update_status", {"incident_id": ticket["id"], "status": "discarded"})

    assert ("PATCH", f"/api/incidents/{ticket['id']}/status") in seen
    assert not any(method == "PATCH" and path == f"/api/incidents/{ticket['id']}" for method, path in seen)


def test_invalid_transition_is_a_validation_error_with_the_api_detail(client, auth_headers, service_account):
    token = h.make_token()
    ticket = h.call(api_app, token, "incidents_create", INCIDENT_PAYLOAD).structuredContent

    error = h.error_of(h.call(api_app, token, "incidents_update_status", {"incident_id": ticket["id"], "status": "resolved"}))

    assert error["code"] == "validation_error"
    assert error["details"]["fields"][0]["field"] == "status"
    assert client.get(f"/api/incidents/{ticket['id']}", headers=auth_headers).json()["status"] == "open"


def test_invalid_category_is_rejected_by_the_incident_manager_rules(service_account):
    error = h.error_of(h.call(api_app, h.make_token(), "incidents_create", {**INCIDENT_PAYLOAD, "category": "gossip"}))
    assert error["code"] == "validation_error"
    assert [f["field"] for f in error["details"]["fields"]] == ["category"]


def test_unknown_ticket_is_not_found(service_account):
    error = h.error_of(h.call(api_app, h.make_token(), "incidents_get", {"incident_id": 999}))
    assert error["code"] == "not_found"


def test_search_filters_and_counts(client, auth_headers, service_account):
    token = h.make_token()
    for branch in ("london_city", "london_city", "central"):
        h.call(api_app, token, "incidents_create", {**INCIDENT_PAYLOAD, "branch": branch})

    found = h.call(api_app, token, "incidents_search", {"branch": "london_city", "limit": 1}).structuredContent
    assert found["total"] == 2 and found["returned"] == 1
    assert found["incidents"][0]["branch"] == "london_city"


# --- Mínimo privilegio ----------------------------------------------------------


def test_read_only_token_cannot_create_tickets(client, auth_headers, service_account):
    error = h.error_of(h.call(api_app, h.make_token([INCIDENTS_READ]), "incidents_create", INCIDENT_PAYLOAD))

    assert error["code"] == "insufficient_scope"
    assert error["details"]["missing_scopes"] == [INCIDENTS_WRITE]
    assert client.get("/api/incidents", headers=auth_headers).json() == []


def test_incident_scopes_do_not_open_the_inventory(service_account, supply):
    error = h.error_of(h.call(api_app, h.make_token([INCIDENTS_READ, INCIDENTS_WRITE]), "inventory_query", {}))
    assert error["code"] == "insufficient_scope"


def test_every_tool_has_a_scope_policy_and_no_inventory_write_scope_exists():
    assert set(TOOL_SCOPES) == EXPECTED_TOOLS
    assert all(TOOL_SCOPES[name] for name in EXPECTED_TOOLS), "ninguna tool puede quedar abierta"
    assert TOOL_SCOPES["inventory_query"] == frozenset({INVENTORY_READ})
    assert not any("inventory:write" in scopes for scopes in TOOL_SCOPES.values())
    # Leer incidencias no exige permiso de escritura (el agente vive con solo incidents:read).
    assert INCIDENTS_WRITE not in TOOL_SCOPES["incidents_get"] | TOOL_SCOPES["incidents_search"]


# --- Inventario de solo lectura -------------------------------------------------


def test_inventory_query_lists_and_gets_supplies_with_live_stock(service_account, supply):
    token = h.make_token([INVENTORY_READ])

    listed = h.call(api_app, token, "inventory_query", {"action": "list_supplies", "search": "nitrilo"}).structuredContent
    assert listed["total"] == 1 and listed["supplies"][0]["current_stock"] == 120

    one = h.call(api_app, token, "inventory_query", {"action": "get_supply", "supply_id": supply["id"]}).structuredContent
    assert one["supplies"][0]["sku"] == "HCR-PPE-001"


@pytest.mark.parametrize("action", ["register_inbound", "adjust_stock", "delete_supply", "set_stock", "createSupply"])
def test_inventory_writes_are_explicitly_rejected_and_nothing_changes(client, auth_headers, service_account, supply, action):
    token = h.make_token([INVENTORY_READ, "inventory:write"])  # ni con un scope inventado

    error = h.error_of(h.call(api_app, token, "inventory_query", {"action": action, "supply_id": supply["id"]}))

    assert error["code"] == "read_only_resource"
    assert error["details"]["allowed_actions"] == ["list_supplies", "get_supply"]
    assert client.get(f"/inventory/products/{supply['id']}", headers=auth_headers).json()["current_stock"] == 120


def test_unknown_inventory_action_is_a_validation_error_not_a_write(service_account, supply):
    error = h.error_of(h.call(api_app, h.make_token([INVENTORY_READ]), "inventory_query", {"action": "forecast"}))
    assert error["code"] == "validation_error"


def test_action_classifier():
    assert classify_inventory_action("list_supplies") == "read"
    assert classify_inventory_action(" GET_SUPPLY ") == "read"
    assert classify_inventory_action("register_outbound") == "write"
    assert classify_inventory_action("update_anything_new") == "write"
    assert classify_inventory_action("forecast") == "unknown"


@pytest.mark.parametrize("method", ["POST", "PATCH", "PUT", "DELETE"])
def test_inventory_reader_cannot_write_even_if_a_tool_had_a_bug(method):
    reader = InventoryReader(HealthCoreApi("http://unused", "x", "y"))
    with pytest.raises(ReadOnlyViolation):
        asyncio.run(reader.request(method, "/inventory/products"))


def test_inventory_reader_is_confined_to_inventory_paths():
    reader = InventoryReader(HealthCoreApi("http://unused", "x", "y"))
    with pytest.raises(ReadOnlyViolation):
        asyncio.run(reader.request("GET", "/api/incidents"))


# --- Errores y auditoría ----------------------------------------------------------


def test_auth_authorization_and_validation_errors_are_distinguishable(service_account, supply):
    unauthenticated = raw_post()
    authorization = h.error_of(h.call(api_app, h.make_token([INCIDENTS_READ]), "incidents_create", INCIDENT_PAYLOAD))
    read_only = h.error_of(h.call(api_app, h.make_token([INVENTORY_READ]), "inventory_query", {"action": "adjust_stock"}))
    validation = h.error_of(h.call(api_app, h.make_token(), "incidents_create", {**INCIDENT_PAYLOAD, "branch": "mars"}))

    assert unauthenticated.status_code == 401
    codes = [authorization["code"], read_only["code"], validation["code"]]
    assert codes == ["insufficient_scope", "read_only_resource", "validation_error"]
    messages = {authorization["message"], read_only["message"], validation["message"]}
    assert len(messages) == 3


def test_upstream_down_is_upstream_unavailable(monkeypatch):
    mcp_app = h.create_app(
        h.SETTINGS,
        auth_server=h.AUTH_SERVER,
        verify=h.build_jwt_verifier(h.AUTH_SERVER, key=h._public_jwk()),
        api=HealthCoreApi("http://127.0.0.1:9", "x", "y", timeout_s=0.5),
    )

    async def go():
        from mcp import ClientSession
        from mcp.client.streamable_http import streamable_http_client

        async with h.http_client(mcp_app, h.make_token()) as http:
            async with streamable_http_client(h.MCP_URL, http_client=http) as (read, write, _):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    return await session.call_tool("incidents_get", {"incident_id": 1})

    error = h.error_of(asyncio.run(h.with_lifespan(mcp_app, go)))
    assert error["code"] == "upstream_unavailable"


def test_every_invocation_is_logged_with_tool_client_and_outcome(service_account, caplog):
    caplog.set_level(logging.INFO, logger="healthcore.mcp.audit")
    h.call(api_app, h.make_token(client_id="agent-healthcore"), "incidents_create", INCIDENT_PAYLOAD)
    h.call(api_app, h.make_token([INCIDENTS_READ], client_id="partner-x"), "incidents_create", INCIDENT_PAYLOAD)

    lines = [json.loads(r.getMessage()) for r in caplog.records if r.name == "healthcore.mcp.audit"]
    assert [(l["tool"], l["client_id"], l["outcome"]) for l in lines] == [
        ("incidents_create", "agent-healthcore", "ok"),
        ("incidents_create", "partner-x", "insufficient_scope"),
    ]
    # Nunca el texto libre de la incidencia (posibles datos de pacientes).
    assert all(INCIDENT_PAYLOAD["title"] not in r.getMessage() for r in caplog.records)
    assert all(INCIDENT_PAYLOAD["description"] not in r.getMessage() for r in caplog.records)


# --- El agente como cliente MCP -------------------------------------------------


def test_agent_reads_tickets_through_the_mcp_server(client, auth_headers, service_account):
    from services.agent.tools.incidents import IncidentLookupInput, lookup_incident

    ticket = client.post("/api/incidents", json={**INCIDENT_PAYLOAD, "status": "open"}, headers=auth_headers).json()
    client.patch(f"/api/incidents/{ticket['id']}/status", json={"status": "in_progress"}, headers=auth_headers)

    result = lookup_incident(IncidentLookupInput(ticket_id=ticket["id"]), mcp_call=h.agent_mcp_call(h.make_token([INCIDENTS_READ])))

    assert result.status == "ok" and result.via == "mcp"
    assert result.data["incidents"][0]["status_label_es"] == "en curso"
    missing = lookup_incident(IncidentLookupInput(ticket_id=999), mcp_call=h.agent_mcp_call(h.make_token([INCIDENTS_READ])))
    assert missing.status == "not_found"


def test_agent_without_the_read_scope_falls_back_instead_of_answering(service_account):
    from services.agent.tools.incidents import IncidentLookupInput, lookup_incident

    result = lookup_incident(IncidentLookupInput(status="open"), mcp_call=h.agent_mcp_call(h.make_token([INVENTORY_READ])))
    assert result.status == "unavailable"


def test_agent_has_no_direct_path_to_the_incident_manager():
    """Ningún módulo del agente importa el repositorio ni la ruta de incidencias."""
    forbidden = {"incident_repository", "routes.incidents", "IncidentRepository"}
    for path in (ROOT / "services" / "agent").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported |= {alias.name for alias in node.names}
            elif isinstance(node, ast.ImportFrom):
                imported |= {node.module or ""} | {alias.name for alias in node.names}
        assert not imported & forbidden, f"{path.relative_to(ROOT)} importa {imported & forbidden}"
