"""Tests del cliente OAuth del agente hacia el servidor MCP (`ClientCredentialsAuth`).

    services/api/.venv/bin/python -m pytest tests/pipelines/test_agent_mcp_client.py

Sin red: `httpx.MockTransport` hace de Logto (token endpoint) y del servidor
MCP. Existen porque el resto de tests inyectan el access token ya hecho y
no pasaban por esta clase: un `httpx.Request(auth=...)` inválido solo
apareció en la prueba contra Logto real.
"""

from __future__ import annotations

import base64
import time
from typing import List
from urllib.parse import parse_qs

import httpx
import pytest

from services.agent.mcp_client import ClientCredentialsAuth, McpClientConfigError

TOKEN_URL = "https://tenant.logto.app/oidc/token"
MCP_URL = "http://localhost:8765/mcp"


class FakeLogtoAndMcp:
    """Emite tokens `t1`, `t2`, ... y responde en el MCP según el token recibido."""

    def __init__(self, *, expires_in: int = 3600, rejected_tokens=(), token_status: int = 200) -> None:
        self.expires_in = expires_in
        self.rejected_tokens = set(rejected_tokens)
        self.token_status = token_status
        self.token_requests: List[httpx.Request] = []
        self.mcp_bearers: List[str] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if str(request.url) == TOKEN_URL:
            self.token_requests.append(request)
            if self.token_status != 200:
                return httpx.Response(self.token_status, json={"error": "invalid_client", "error_description": "secret de la app X"})
            return httpx.Response(200, json={"access_token": f"t{len(self.token_requests)}", "expires_in": self.expires_in, "token_type": "Bearer"})
        bearer = request.headers.get("authorization", "")
        self.mcp_bearers.append(bearer)
        if bearer.removeprefix("Bearer ") in self.rejected_tokens:
            return httpx.Response(401, json={"error": "invalid_token"})
        return httpx.Response(200, json={"ok": True})


def make_auth() -> ClientCredentialsAuth:
    return ClientCredentialsAuth(TOKEN_URL, "agent-id", "s3cr:et", resource=MCP_URL, scopes="incidents:read")


def client(fake: FakeLogtoAndMcp, auth: ClientCredentialsAuth) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(fake), auth=auth)


def test_token_request_is_client_credentials_with_basic_auth_resource_and_scope():
    fake = FakeLogtoAndMcp()
    with client(fake, make_auth()) as http:
        assert http.post(MCP_URL, json={}).status_code == 200

    token_request = fake.token_requests[0]
    user, secret = base64.b64decode(token_request.headers["authorization"].removeprefix("Basic ")).decode().split(":")
    assert (user, secret) == ("agent-id", "s3cr%3Aet")  # RFC 6749 §2.3.1: codificado antes de Basic
    form = parse_qs(token_request.content.decode())
    assert form == {"grant_type": ["client_credentials"], "resource": [MCP_URL], "scope": ["incidents:read"]}
    assert fake.mcp_bearers == ["Bearer t1"]


def test_token_is_reused_until_it_is_about_to_expire():
    fake = FakeLogtoAndMcp()
    auth = make_auth()
    with client(fake, auth) as http:
        http.post(MCP_URL, json={})
        http.post(MCP_URL, json={})
        assert len(fake.token_requests) == 1

        auth._expires_at = time.time() + 30  # dentro del margen de 60 s
        http.post(MCP_URL, json={})
    assert len(fake.token_requests) == 2
    assert fake.mcp_bearers == ["Bearer t1", "Bearer t1", "Bearer t2"]


def test_a_401_from_the_mcp_server_renews_the_token_once():
    fake = FakeLogtoAndMcp(rejected_tokens={"t1"})
    with client(fake, make_auth()) as http:
        assert http.post(MCP_URL, json={}).status_code == 200
    assert fake.mcp_bearers == ["Bearer t1", "Bearer t2"]


def test_works_with_the_async_client_used_by_langchain_mcp_adapters():
    import asyncio

    fake = FakeLogtoAndMcp()

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(fake), auth=make_auth()) as http:
            return await http.post(MCP_URL, json={})

    assert asyncio.run(go()).status_code == 200
    assert fake.mcp_bearers == ["Bearer t1"]


def test_rejected_credentials_fail_without_repeating_logto_response():
    fake = FakeLogtoAndMcp(token_status=401)
    with client(fake, make_auth()) as http, pytest.raises(McpClientConfigError) as error:
        http.post(MCP_URL, json={})
    assert "401" in str(error.value)
    assert "secret de la app" not in str(error.value)
    assert fake.mcp_bearers == []
