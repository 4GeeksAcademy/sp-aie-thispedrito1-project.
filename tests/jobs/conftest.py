"""Configuración de los tests del job nocturno (scripts/nightly_export.py).

Se ejecutan desde la raíz del monorepo con el venv de la API:

    services/api/.venv/bin/python -m pytest tests/jobs

A propósito fuera de services/api/tests: aquel conftest importa `main`
(FastAPI), y el ticket exige que el job sea un proceso independiente de la
API. Estos tests nunca abren conexión con Supabase: usan SQLite en memoria.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT_DIR = Path(__file__).resolve().parents[2]
API_DIR = ROOT_DIR / "services" / "api"
for path in (str(ROOT_DIR), str(API_DIR)):
    if path not in sys.path:
        sys.path.insert(0, path)

from sqlmodel import create_engine  # noqa: E402
from sqlmodel.pool import StaticPool  # noqa: E402

from services.job_runner import ensure_job_runs_table  # noqa: E402
from telemetry_models import TelemetryEventRecord  # noqa: E402

SCRIPT_PATH = ROOT_DIR / "scripts" / "nightly_export.py"  # mismo valor en test_nightly_export.py


@pytest.fixture(scope="session")
def nightly_export():
    """Carga scripts/nightly_export.py como módulo (scripts/ no es paquete)."""
    spec = importlib.util.spec_from_file_location("nightly_export", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules["nightly_export"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def engine():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    TelemetryEventRecord.__table__.create(engine)
    ensure_job_runs_table(engine)
    yield engine
    engine.dispose()


@pytest.fixture
def pipeline_counter(tmp_path) -> Path:
    return tmp_path / "pipeline_calls.txt"


@pytest.fixture
def ok_pipeline(pipeline_counter):
    """Sustituto del pipeline: anota cada llamada en un archivo y sale con 0."""
    return (
        sys.executable,
        "-c",
        "import sys; open(sys.argv[1], 'a').write('x')",
        str(pipeline_counter),
    )
