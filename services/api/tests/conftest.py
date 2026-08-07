"""Shared pytest fixtures for the HealthCore API test suite.

The app reads its configuration (JWT secret, DB path) at import time, so the
test environment MUST be set before importing `main`. Every test runs against
a throwaway TinyDB file — the real data/suppliers.db.json is never touched.
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

API_DIR = Path(__file__).resolve().parents[1]
ROOT_DIR = API_DIR.parents[1]

_TEST_DB_DIR = tempfile.mkdtemp(prefix="healthcore-tests-")

os.environ["JWT_SECRET_KEY"] = "test-secret-key-not-for-production"
os.environ["JWT_ALGORITHM"] = "HS256"
os.environ["ACCESS_TOKEN_EXPIRE_MINUTES"] = "30"
os.environ["RESET_TOKEN_EXPIRE_MINUTES"] = "30"
os.environ["SUPPLIERS_DB_PATH"] = str(Path(_TEST_DB_DIR) / "test.db.json")

for path in (str(ROOT_DIR), str(API_DIR)):
    if path not in sys.path:
        sys.path.insert(0, path)

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import database  # noqa: E402
from main import app  # noqa: E402

TEST_PASSWORD = "SuperSecure123"


@pytest.fixture(autouse=True)
def clean_db():
    """Every test starts from an empty database."""
    database.get_db().drop_tables()
    yield


@pytest.fixture()
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture()
def registered_user(client: TestClient) -> dict[str, str]:
    """A user already registered through the real endpoint."""
    payload = {"email": "ana.perez@healthcore.com", "password": TEST_PASSWORD, "name": "Ana Perez"}
    response = client.post("/users", json=payload)
    assert response.status_code == 201, response.text
    payload["id"] = response.json()["id"]
    return payload


@pytest.fixture()
def auth_token(client: TestClient, registered_user: dict[str, str]) -> str:
    response = client.post(
        "/auth/login",
        json={"email": registered_user["email"], "password": registered_user["password"]},
    )
    assert response.status_code == 200, response.text
    return response.json()["access_token"]


@pytest.fixture()
def auth_headers(auth_token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {auth_token}"}
