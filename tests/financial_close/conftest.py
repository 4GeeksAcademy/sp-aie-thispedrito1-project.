"""Fixtures del cierre financiero (sin red, sin Supabase).

    services/api/.venv/bin/python -m pytest tests/financial_close

- Datos: `seed_data` con la fecha de carga fija LOAD_DATE (mes de cierre 2026-10).
- Base de datos: SQLite en memoria por test con SOLO las tablas fc_*.
- Modelo: `FakeLLM` graba cada llamada (para ver qué llevaba el prompt) y
  devuelve lo que el test indique; sin él, `get_llm_client` falla y el código
  cae a su respaldo.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Iterator

ROOT_DIR = Path(__file__).resolve().parents[2]
for path in (str(ROOT_DIR), str(ROOT_DIR / "services" / "api")):
    if path not in sys.path:
        sys.path.insert(0, path)

import pytest  # noqa: E402
from sqlmodel import Session, SQLModel, create_engine  # noqa: E402
from sqlmodel.pool import StaticPool  # noqa: E402

from services.financial_close import ingestion, seed_data, workflow  # noqa: E402
from services.financial_close.models import FC_TABLES  # noqa: E402

from fc_helpers import LOAD_DATE, PERIOD, FakeLLM  # noqa: E402


@pytest.fixture(scope="session")
def data_dir(tmp_path_factory) -> Path:
    out = tmp_path_factory.mktemp("financial-close")
    seed_data.generate_all(out, LOAD_DATE)
    return out


@pytest.fixture(scope="session")
def incomplete_dir(tmp_path_factory) -> Path:
    out = tmp_path_factory.mktemp("financial-close-incomplete")
    seed_data.generate_all(out, LOAD_DATE, incomplete=True)
    return out


@pytest.fixture(scope="session")
def dataset(data_dir):
    return ingestion.read_dataset(data_dir, PERIOD)


@pytest.fixture()
def engine():
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(eng, tables=FC_TABLES)
    yield eng
    eng.dispose()


@pytest.fixture()
def session(engine, data_dir) -> Iterator[Session]:
    with Session(engine) as s:
        workflow.ingest_period(s, data_dir, PERIOD)
        yield s


@pytest.fixture()
def fake_llm():
    return FakeLLM


@pytest.fixture(autouse=True)
def _no_real_model(monkeypatch):
    import data.pipelines.rag as rag_pipeline
    import data.process.rag as rag_module

    def _offline():
        raise RuntimeError("Sin red en los tests")

    monkeypatch.setattr(rag_module, "get_llm_client", _offline)
    monkeypatch.setattr(rag_pipeline, "get_generation_model", lambda: "fake-model")
