"""Fixtures de proveedores y contratos (sin red, sin Supabase).

    services/api/.venv/bin/python -m pytest tests/supplier_contracts

- Datos: los genera `seed_data` una vez por sesión con la fecha de carga
  fija LOAD_DATE (las fechas de los contratos son relativas a ella).
- Base de datos: SQLite en memoria por test con SOLO las tablas sc_*.
- Modelo: `FakeLLM` graba cada llamada (para comprobar qué llevaba el prompt)
  y devuelve lo que el test le indique.
"""

from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, Iterator, List, Optional

ROOT_DIR = Path(__file__).resolve().parents[2]
for path in (str(ROOT_DIR), str(ROOT_DIR / "services" / "api")):
    if path not in sys.path:
        sys.path.insert(0, path)

import pytest  # noqa: E402
from sqlmodel import Session, SQLModel, create_engine  # noqa: E402
from sqlmodel.pool import StaticPool  # noqa: E402

from services.supplier_contracts import ingestion, repository, seed_data  # noqa: E402
from services.supplier_contracts.models import SC_TABLES  # noqa: E402

LOAD_DATE = date(2026, 11, 7)


@pytest.fixture(scope="session")
def data_dir(tmp_path_factory) -> Path:
    out = tmp_path_factory.mktemp("supplier-contracts")
    seed_data.generate_all(out, LOAD_DATE)
    return out


@pytest.fixture(scope="session")
def dataset(data_dir):
    return ingestion.read_dataset(data_dir, LOAD_DATE)


@pytest.fixture()
def engine():
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(eng, tables=SC_TABLES)
    yield eng
    eng.dispose()


@pytest.fixture()
def session(engine, dataset) -> Iterator[Session]:
    with Session(engine) as s:
        repository.replace_dataset(s, dataset)
        yield s


class FakeLLM:
    """Doble del cliente OpenAI: `chat.completions.create(**kwargs)`."""

    def __init__(self, *, tool_calls: Optional[List[Dict[str, Any]]] = None, content: Optional[str] = None,
                 error: Optional[Exception] = None) -> None:
        self.calls: List[Dict[str, Any]] = []
        self._tool_calls = tool_calls
        self._content = content
        self._error = error
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs: Any):
        self.calls.append(kwargs)
        if self._error is not None:
            raise self._error
        tool_calls = [
            SimpleNamespace(function=SimpleNamespace(name=c["name"], arguments=json.dumps(c.get("arguments", {}))))
            for c in (self._tool_calls or [])
        ]
        message = SimpleNamespace(tool_calls=tool_calls or None, content=self._content)
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])

    def prompts_text(self) -> str:
        return json.dumps([c.get("messages") for c in self.calls], ensure_ascii=False)


@pytest.fixture()
def fake_llm_factory():
    return FakeLLM


@pytest.fixture(autouse=True)
def _no_real_model(monkeypatch):
    """Ningún test llama al proxy real: si alguien olvida pasar `client`,
    `get_llm_client` falla y el código cae a su respaldo."""
    import data.process.rag as rag_module

    def _offline():
        raise RuntimeError("Sin red en los tests")

    monkeypatch.setattr(rag_module, "get_llm_client", _offline)
    import data.pipelines.rag as rag_pipeline

    monkeypatch.setattr(rag_pipeline, "get_generation_model", lambda: "fake-model")
