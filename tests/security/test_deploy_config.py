"""Auditoría OWASP A05 (configuración incorrecta) sobre los archivos de despliegue.

Lee docker-compose.yml y los Dockerfiles como texto/YAML: no arranca Docker.
Fija dos reglas de la auditoría (docs/security/owasp-top10-audit.md):

- ningún puerto publicado escucha en todas las interfaces del host;
- los contenedores que ejecutan el agente y los jobs no corren como root.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]

# Contenedores Python: la API (agente, MCP-cliente, RAG), el worker de Celery
# (Partes 1-3 de RFP, pipeline) y el scheduler (job nocturno).
PYTHON_DOCKERFILES = (
    "services/api/Dockerfile",
    "services/worker/Dockerfile",
    "services/scheduler/Dockerfile",
)


def _published_ports():
    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    for name, service in compose["services"].items():
        for port in service.get("ports", []):
            yield name, str(port)


@pytest.mark.parametrize("service,port", list(_published_ports()))
def test_published_ports_only_listen_on_loopback(service: str, port: str) -> None:
    assert port.startswith("127.0.0.1:"), f"{service} publica {port} en todas las interfaces"


def _final_user(dockerfile: str) -> str:
    users = re.findall(r"(?m)^USER\s+(\S+)", (ROOT / dockerfile).read_text(encoding="utf-8"))
    return users[-1] if users else "root"


@pytest.mark.parametrize("dockerfile", PYTHON_DOCKERFILES)
def test_python_containers_do_not_run_as_root(dockerfile: str) -> None:
    user = _final_user(dockerfile)
    assert user.split(":")[0] not in {"root", "0"}, f"{dockerfile} corre como {user}"
