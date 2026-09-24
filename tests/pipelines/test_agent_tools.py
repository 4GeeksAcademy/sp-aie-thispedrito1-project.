"""Tests de las tools en vivo y del enrutamiento entre fuentes (Agente de Soporte, Parte 2).

    services/api/.venv/bin/python -m pytest tests/pipelines/test_agent_tools.py

Sin red. La tool de inventario se prueba con el repositorio real
(`inventory_repository`) sobre una SQLite en memoria; la de incidencias con
repositorios dobles que imitan a `IncidentRepository` (su integración real con
TinyDB está en services/api/tests/test_agent.py). El modelo del planificador
es un doble que devuelve las tool calls indicadas.
"""

from __future__ import annotations

import json
import time
from datetime import date
from types import SimpleNamespace
from typing import Any, Dict, List

import pytest
from pydantic import ValidationError
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

import data.pipelines  # noqa: F401  (añade services/api a sys.path)
from inventory_models import MedicalSupply, SupplyConsumption, SupplyDelivery
from services.agent import planner
from services.agent.evidence import INCIDENTS_DOCUMENT, INVENTORY_DOCUMENT, fallback_message, tool_evidence
from services.agent.graph import (
    CHECK_INVENTORY_STOCK,
    GENERATE,
    LOOKUP_INCIDENT,
    NO_INFORMATION,
    PLAN_SOURCES,
    RECEIVE_QUESTION,
    RETRIEVE,
    TOOL_FALLBACK,
    AgentNodes,
    build_agent_graph,
    compile_agent_graph,
    route_next_source,
)
from services.agent.planner import Plan, PlannedCall
from services.agent.tools import incidents, inventory
from services.agent.tools.base import ToolResult
from services.agent.tools.incidents import IncidentLookupInput, lookup_incident
from services.agent.tools.inventory import InventoryLookupInput, check_inventory_stock
from services.agent.tracing import run_agent

INCIDENT = {
    "id": 12,
    "title": "Paciente Ana Pérez se queja del cobro",
    "description": "La paciente con DNI 12345678 dice que le cobraron dos veces",
    "category": "billing_error",
    "status": "resolved",
    "origin": "customer",
    "branch": "central",
    "created_at": "2026-08-01T10:00:00+00:00",
    "updated_at": "2026-08-03T09:30:00+00:00",
}
CHUNK = {"source_document": "appointment-policy", "section": "Política de cancelación", "chunk_index": 1, "text": "50 USD", "score": 0.7}


class ReadOnlyRepository:
    """Doble de IncidentRepository: cualquier método que no sea de lectura revienta."""

    READ_METHODS = {"get_by_id", "list"}

    def __init__(self, incidents: List[Dict[str, Any]]) -> None:
        self._incidents = incidents
        self.calls: List[str] = []

    def get_by_id(self, incident_id: int):
        self.calls.append("get_by_id")
        return next((dict(item) for item in self._incidents if item["id"] == incident_id), None)

    def list(self, status=None, origin=None, branch=None, category=None):
        self.calls.append("list")
        wanted = {"status": status, "origin": origin, "branch": branch, "category": category}
        return [dict(i) for i in self._incidents if all(v is None or i[k] == v for k, v in wanted.items())]

    def __getattr__(self, name):
        raise AssertionError(f"La tool no debe llamar a IncidentRepository.{name} (solo lectura)")


def slow(seconds: float, value: Any):
    def factory():
        time.sleep(seconds)
        return value

    return factory


# --- Tool de incidencias ----------------------------------------------------


@pytest.mark.parametrize(
    "payload",
    [{}, {"ticket_id": 12, "status": "open"}, {"status": "cerrado"}, {"ticket_id": 0}, {"ticket_id": 12, "sql": "drop"}],
    ids=["sin-nada", "id-y-filtro", "estado-invalido", "id-no-positivo", "campo-extra"],
)
def test_incident_input_contract_rejects_invalid_payloads(payload):
    with pytest.raises(ValidationError):
        IncidentLookupInput.model_validate(payload)


def test_incident_by_id_returns_operational_fields_without_free_text():
    repo = ReadOnlyRepository([INCIDENT])
    result = lookup_incident(IncidentLookupInput(ticket_id=12), repository_factory=lambda: repo)

    assert result.status == "ok"
    assert result.data["mode"] == "by_id"
    record = result.data["incidents"][0]
    assert record["status"] == "resolved" and record["status_label_es"] == "resuelta"
    assert set(record) == {"id", "status", "status_label_es", "category", "origin", "branch", "created_at", "updated_at"}
    dumped = json.dumps(result.model_dump(mode="json"), ensure_ascii=False)
    assert "Ana Pérez" not in dumped and "12345678" not in dumped
    assert repo.calls == ["get_by_id"]


def test_unknown_ticket_is_not_found():
    result = lookup_incident(IncidentLookupInput(ticket_id=482), repository_factory=lambda: ReadOnlyRepository([INCIDENT]))
    assert result.status == "not_found"
    assert result.data is None


def test_incident_search_counts_all_matches_and_caps_the_list():
    many = [{**INCIDENT, "id": n, "status": "open"} for n in range(1, 16)]
    repo = ReadOnlyRepository(many)
    result = lookup_incident(IncidentLookupInput(status="open"), repository_factory=lambda: repo)

    assert result.data["mode"] == "search"
    assert result.data["total"] == 15
    assert len(result.data["incidents"]) == incidents.MAX_RESULTS
    assert repo.calls == ["list"]


def test_tools_declare_explicit_numeric_timeouts():
    assert incidents.TIMEOUT_S == 3.0
    assert inventory.TIMEOUT_S == 5.0


def test_slow_incident_manager_times_out_as_unavailable():
    started = time.perf_counter()
    result = lookup_incident(
        IncidentLookupInput(ticket_id=12),
        repository_factory=slow(1.0, ReadOnlyRepository([INCIDENT])),
        timeout_s=0.1,
    )
    assert result.status == "unavailable"
    assert result.error_type == "TimeoutError"
    assert result.timeout_s == 0.1
    assert time.perf_counter() - started < 0.5, "el grafo no debe esperar a la consulta colgada"


def test_crashing_incident_manager_is_unavailable_without_its_message():
    def broken():
        raise OSError("disk /srv/tinydb full for ana@example.com")

    result = lookup_incident(IncidentLookupInput(ticket_id=12), repository_factory=broken)
    assert result.status == "unavailable"
    assert result.error_type == "OSError"
    assert "ana@example.com" not in json.dumps(result.model_dump(mode="json"))


# --- Tool de inventario (repositorio real sobre SQLite) ----------------------


@pytest.fixture()
def inventory_session_factory():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    # Solo las tablas de la tool: el metadata compartido también registra las
    # reporting.* del pipeline cuando otro test las importa, y SQLite no tiene
    # ese esquema (fallaba solo en la batería completa, no en solitario).
    SQLModel.metadata.create_all(
        engine, tables=[MedicalSupply.__table__, SupplyDelivery.__table__, SupplyConsumption.__table__]
    )
    with Session(engine) as session:
        gloves = MedicalSupply(name="Guantes de nitrilo M", sku="PPE-GLV-M", category="ppe", unit="caja", country="US")
        gauze = MedicalSupply(name="Gasa estéril", sku="WND-GAU-10", category="wound_care", unit="paquete", country="UK", expiry_date=date(2027, 1, 31))
        session.add_all([gloves, gauze])
        session.commit()
        session.add_all([
            SupplyDelivery(supply_id=gloves.id, quantity=40, vendor_name="Acme", clinic_id=1, user_uuid="u1"),
            SupplyDelivery(supply_id=gloves.id, quantity=10, vendor_name="Acme", clinic_id=2, user_uuid="u1"),
            SupplyConsumption(supply_id=gloves.id, quantity=12, consumption_type="patient_care", clinic_id=1, user_uuid="u2"),
        ])
        session.commit()

    class ReadOnlySession(Session):
        def commit(self):  # la tool nunca escribe
            raise AssertionError("check_inventory_stock no debe hacer commit")

        def add(self, *_args, **_kwargs):
            raise AssertionError("check_inventory_stock no debe añadir filas")

    yield lambda: ReadOnlySession(engine)
    engine.dispose()


def test_inventory_stock_is_the_computed_network_total(inventory_session_factory):
    result = check_inventory_stock(InventoryLookupInput(product="guantes"), session_factory=inventory_session_factory)

    assert result.status == "ok"
    assert result.data["total_matches"] == 1
    item = result.data["items"][0]
    assert item["sku"] == "PPE-GLV-M"
    assert item["current_stock"] == 38  # 40 + 10 − 12, todas las clínicas


def test_inventory_matches_by_sku_case_insensitively(inventory_session_factory):
    result = check_inventory_stock(InventoryLookupInput(product="wnd-gau"), session_factory=inventory_session_factory)
    assert result.data["items"][0]["name"] == "Gasa estéril"
    assert result.data["items"][0]["current_stock"] == 0


def test_unknown_product_is_not_found(inventory_session_factory):
    result = check_inventory_stock(InventoryLookupInput(product="respirador"), session_factory=inventory_session_factory)
    assert result.status == "not_found"


def test_inventory_without_database_is_unavailable():
    def no_database():
        raise RuntimeError("DATABASE_URL environment variable is required")

    result = check_inventory_stock(InventoryLookupInput(product="guantes"), session_factory=no_database)
    assert result.status == "unavailable"
    assert result.error_type == "RuntimeError"


@pytest.mark.parametrize("product", ["", " ", "x", "a" * 81])
def test_inventory_input_contract_rejects_invalid_products(product):
    with pytest.raises(ValidationError):
        InventoryLookupInput(product=product)


# --- Planificador -----------------------------------------------------------


def tool_call(name: str, arguments: Any) -> SimpleNamespace:
    raw = arguments if isinstance(arguments, str) else json.dumps(arguments)
    return SimpleNamespace(function=SimpleNamespace(name=name, arguments=raw))


class PlannerLLM:
    def __init__(self, calls=None, error: Exception = None) -> None:
        self._calls = calls or []
        self._error = error
        self.requests: List[Dict[str, Any]] = []
        self.options: Dict[str, Any] = {}
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def with_options(self, **options):
        self.options = options
        return self

    def _create(self, **kwargs):
        self.requests.append(kwargs)
        if self._error:
            raise self._error
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(tool_calls=self._calls))])


def test_planner_orders_live_tools_before_the_knowledge_base():
    llm = PlannerLLM([tool_call("search_knowledge_base", {}), tool_call("get_incident", {"ticket_id": 12})])
    plan = planner.plan_sources("¿El ticket 12 está resuelto y qué política aplica?", client=llm, model="m")

    assert plan.status == "model"
    assert plan.sources == ["incidents", "knowledge_base"]
    assert plan.calls[0].args == {"ticket_id": 12}
    request = llm.requests[0]
    assert request["tool_choice"] == "required"
    assert {t["function"]["name"] for t in request["tools"]} == {
        "search_knowledge_base", "get_incident", "search_incidents", "check_inventory_stock",
    }
    assert llm.options == {"timeout": planner.PLANNER_TIMEOUT_S, "max_retries": 0}


def test_planner_drops_calls_that_break_the_tool_contract():
    llm = PlannerLLM([
        tool_call("get_incident", {"ticket_id": 12, "status": "open"}),
        tool_call("delete_incident", {"ticket_id": 12}),
        tool_call("check_inventory_stock", {"product": "guantes"}),
    ])
    plan = planner.plan_sources("¿...?", client=llm, model="m")

    assert plan.sources == ["inventory"]
    assert plan.rejected_tools == ["get_incident", "delete_incident"]


def test_search_accepts_explicit_nulls_for_unmentioned_filters():
    """Regresión (grabación real): el modelo debe poder decir "no lo menciona" con null."""
    llm = PlannerLLM([tool_call("search_incidents", {"status": "open", "category": None, "branch": "manchester_central", "origin": None})])
    plan = planner.plan_sources("¿Cuántas abiertas en Manchester Central?", client=llm, model="m")

    assert plan.status == "model"
    assert plan.calls[0].args == {"status": "open", "branch": "manchester_central"}


def test_incident_by_id_function_offers_no_filters_to_fill_in():
    """Regresión (grabación real): con todos los campos opcionales en una sola
    función, el modelo real los rellenaba todos con valores inventados."""
    schemas = {t["function"]["name"]: t["function"]["parameters"] for t in planner._tool_schemas()}

    assert set(schemas["get_incident"]["properties"]) == {"ticket_id"}
    assert schemas["get_incident"]["required"] == ["ticket_id"]
    search = schemas["search_incidents"]
    assert set(search["required"]) == set(search["properties"]) == {"status", "category", "branch", "origin"}
    assert all(None in field["enum"] for field in search["properties"].values())


def test_planner_keeps_one_call_per_source():
    llm = PlannerLLM([tool_call("get_incident", {"ticket_id": 1}), tool_call("search_incidents", {"status": "open"})])
    plan = planner.plan_sources("¿...?", client=llm, model="m")
    assert [call.args for call in plan.calls] == [{"ticket_id": 1}]


@pytest.mark.parametrize(
    "llm",
    [PlannerLLM([tool_call("get_incident", "{roto")]), PlannerLLM([]), PlannerLLM(error=TimeoutError("proxy"))],
    ids=["json-roto", "sin-llamadas", "modelo-caido"],
)
def test_planner_falls_back_to_the_knowledge_base(llm):
    plan = planner.plan_sources("¿...?", client=llm, model="m")
    assert plan.status == "fallback"
    assert plan.sources == ["knowledge_base"]


# --- Enrutamiento ------------------------------------------------------------


def ok_result(tool: str, args: Dict[str, Any], data: Dict[str, Any]) -> Dict[str, Any]:
    return ToolResult(tool=tool, status="ok", args=args, data=data, timeout_s=3.0, duration_ms=1.0).model_dump(mode="json")


def failed_result(tool: str, args: Dict[str, Any], status: str) -> Dict[str, Any]:
    return ToolResult(tool=tool, status=status, args=args, error_type="X", timeout_s=3.0, duration_ms=1.0).model_dump(mode="json")


INCIDENT_DATA = lookup_incident(IncidentLookupInput(ticket_id=12), repository_factory=lambda: ReadOnlyRepository([INCIDENT])).data
BOTH_PLAN = [{"source": "incidents", "args": {"ticket_id": 12}}, {"source": "knowledge_base", "args": {}}]


def test_route_goes_to_the_next_pending_source():
    assert route_next_source({"plan": BOTH_PLAN, "completed_sources": []}) == LOOKUP_INCIDENT
    state = {"plan": BOTH_PLAN, "completed_sources": ["incidents"], "tool_results": [ok_result("lookup_incident", {"ticket_id": 12}, INCIDENT_DATA)]}
    assert route_next_source(state) == RETRIEVE


def test_route_short_circuits_to_fallback_when_a_tool_fails():
    state = {"plan": BOTH_PLAN, "completed_sources": ["incidents"], "tool_results": [failed_result("lookup_incident", {"ticket_id": 12}, "unavailable")]}
    assert route_next_source(state) == TOOL_FALLBACK


def test_route_generates_from_live_data_even_without_rag_context():
    state = {
        "plan": BOTH_PLAN,
        "completed_sources": ["incidents", "knowledge_base"],
        "tool_results": [ok_result("lookup_incident", {"ticket_id": 12}, INCIDENT_DATA)],
        "context": [],
    }
    assert route_next_source(state) == GENERATE


def test_route_without_any_evidence_is_no_information():
    state = {"plan": [{"source": "knowledge_base", "args": {}}], "completed_sources": ["knowledge_base"], "context": []}
    assert route_next_source(state) == NO_INFORMATION


# --- Recorridos completos con dobles ----------------------------------------


class Harness:
    def __init__(self, plan_sources: List[str], args: Dict[str, Dict[str, Any]] = None, chunks=None,
                 incident_status: str = "ok", inventory_status: str = "ok") -> None:
        self.plan_sources = plan_sources
        self.args = args or {"incidents": {"ticket_id": 12}, "inventory": {"product": "guantes"}}
        self.chunks = [CHUNK] if chunks is None else chunks
        self.incident_status = incident_status
        self.inventory_status = inventory_status
        self.calls: List[str] = []
        self.generated_with: List[Dict[str, Any]] = []

    def plan(self, question):
        return Plan(calls=[PlannedCall(source=s, args=self.args.get(s, {})) for s in self.plan_sources], status="model")

    def incident_tool(self, payload):
        self.calls.append("incidents")
        if self.incident_status == "ok":
            return lookup_incident(payload, repository_factory=lambda: ReadOnlyRepository([INCIDENT]))
        return ToolResult(**failed_result("lookup_incident", payload.model_dump(exclude_none=True), self.incident_status))

    def inventory_tool(self, payload):
        self.calls.append("inventory")
        if self.inventory_status == "ok":
            data = {"total_matches": 1, "items": [{"id": 1, "name": "Guantes de nitrilo M", "sku": "PPE-GLV-M", "category": "ppe",
                                                   "unit": "caja", "country": "US", "current_stock": 38, "expiry_date": None}]}
            return ToolResult(**ok_result("check_inventory_stock", payload.model_dump(), data))
        return ToolResult(**failed_result("check_inventory_stock", payload.model_dump(), self.inventory_status))

    def retrieve(self, question, *, k, min_score):
        self.calls.append("knowledge_base")
        return [dict(c) for c in self.chunks]

    def generate(self, question, context):
        self.calls.append("generate")
        self.generated_with = context
        return "Respuesta del modelo."

    def run(self, tmp_path, question="¿...?"):
        nodes = AgentNodes(retrieve_fn=self.retrieve, generate_fn=self.generate, min_score_fn=lambda: 0.38, k=5,
                           planner_fn=self.plan, incident_tool_fn=self.incident_tool, inventory_tool_fn=self.inventory_tool)
        return run_agent(compile_agent_graph(build_agent_graph(nodes)), question, trace_dir=tmp_path)


def test_ticket_question_uses_only_the_incident_tool(tmp_path):
    harness = Harness(["incidents"])
    result = harness.run(tmp_path)

    assert result.trace["node_sequence"] == [RECEIVE_QUESTION, PLAN_SOURCES, LOOKUP_INCIDENT, GENERATE]
    assert result.trace["sources_used"] == ["incidents"]
    assert harness.calls == ["incidents", "generate"]
    assert harness.generated_with[0]["source_document"] == INCIDENTS_DOCUMENT
    assert "estado resolved (resuelta)" in harness.generated_with[0]["text"]


def test_mixed_question_uses_the_tool_then_the_rag(tmp_path):
    harness = Harness(["incidents", "knowledge_base"])
    result = harness.run(tmp_path)

    assert result.trace["node_sequence"] == [RECEIVE_QUESTION, PLAN_SOURCES, LOOKUP_INCIDENT, RETRIEVE, GENERATE]
    assert result.trace["sources_used"] == ["incidents", "knowledge_base"]
    assert [item["source_document"] for item in harness.generated_with] == [INCIDENTS_DOCUMENT, "appointment-policy"]


def test_stock_question_uses_only_the_inventory_tool(tmp_path):
    harness = Harness(["inventory"])
    result = harness.run(tmp_path)

    assert result.trace["sources_used"] == ["inventory"]
    assert harness.generated_with[0]["source_document"] == INVENTORY_DOCUMENT
    assert "stock total en la red 38 caja" in harness.generated_with[0]["text"]


def test_missing_ticket_answers_honestly_without_the_model(tmp_path):
    harness = Harness(["incidents"], args={"incidents": {"ticket_id": 482}}, incident_status="not_found")
    result = harness.run(tmp_path)

    assert result.outcome == "tool_fallback"
    assert result.trace["node_sequence"] == [RECEIVE_QUESTION, PLAN_SOURCES, LOOKUP_INCIDENT, TOOL_FALLBACK]
    assert "No encuentro la incidencia #482" in result.answer
    assert "generate" not in harness.calls


def test_unavailable_tool_short_circuits_before_the_rag(tmp_path):
    harness = Harness(["incidents", "knowledge_base"], incident_status="unavailable")
    result = harness.run(tmp_path)

    assert result.trace["node_sequence"] == [RECEIVE_QUESTION, PLAN_SOURCES, LOOKUP_INCIDENT, TOOL_FALLBACK]
    assert "No pude confirmar el estado de la incidencia #12" in result.answer
    assert harness.calls == ["incidents"]
    tool_step = result.trace["steps"][2]["output"]["tool_results"][0]
    assert tool_step["status"] == "unavailable"


def test_policy_question_never_touches_the_tools(tmp_path):
    harness = Harness(["knowledge_base"])
    result = harness.run(tmp_path)

    assert result.trace["sources_used"] == ["knowledge_base"]
    assert harness.calls == ["knowledge_base", "generate"]


def test_fallback_messages_never_state_a_status_or_stock():
    messages = [
        fallback_message(failed_result("lookup_incident", {"ticket_id": 7}, "not_found")),
        fallback_message(failed_result("lookup_incident", {"ticket_id": 7}, "unavailable")),
        fallback_message(failed_result("lookup_incident", {"status": "open"}, "unavailable")),
        fallback_message(failed_result("check_inventory_stock", {"product": "gasa"}, "not_found")),
        fallback_message(failed_result("check_inventory_stock", {"product": "gasa"}, "unavailable")),
    ]
    for message in messages:
        lowered = message.lower()
        assert not any(word in lowered for word in ("resuelta", "abierta", "en curso", "descartada", "unidades"))
    assert "#7" in messages[0] and "«gasa»" in messages[3]


def test_search_evidence_reports_the_total_and_what_is_shown():
    many = [{**INCIDENT, "id": n, "status": "open"} for n in range(1, 16)]
    result = lookup_incident(IncidentLookupInput(status="open", branch="central"), repository_factory=lambda: ReadOnlyRepository(many))
    evidence = tool_evidence([result.model_dump(mode="json")])[0]

    assert evidence["section"] == "Búsqueda (branch=central, status=open)"
    assert "15 en total" in evidence["text"] and "las 10 más recientes" in evidence["text"]
