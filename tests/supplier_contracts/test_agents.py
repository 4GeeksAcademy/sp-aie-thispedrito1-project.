"""Supervisor y especialistas: escenarios del BRIEFING, identidad y herramientas."""

from __future__ import annotations

from datetime import date

import pytest
from sqlmodel import select

from services.supplier_contracts import supervisor as supervisor_module
from services.supplier_contracts.catalog import (
    AGENT_CONTRACTS,
    AGENT_PRICING,
    AGENT_SUPERVISOR,
    AGENT_TOOLS,
    MAX_CONSULTATIONS_PER_REQUEST,
)
from services.supplier_contracts.contracts import ContractsSpecialist, evaluate_price_review_clause, get_contract_terms
from services.supplier_contracts.models import ScAgentAction
from services.supplier_contracts.pricing import PricingSpecialist, get_price_history
from services.supplier_contracts.runtime import (
    ActionLog,
    AgentRuntime,
    ContractsRequest,
    PricingRequest,
    ToolNotAllowed,
)
from services.supplier_contracts.supervisor import Supervisor, ask

TODAY = date(2026, 11, 7)


def _ask(session, question, **kwargs):
    return ask(session, question, requester="human:test", today=TODAY, use_model=False, **kwargs)


# --- Especialista de precios ---------------------------------------------------------


def test_pricing_detects_scenario_a_with_row_references(session):
    result = PricingSpecialist(session, ActionLog()).handle(PricingRequest(intent="detect_increases", supplier_id="HC-S03"))
    assert result.fact("change_pct").value == "7.00"
    assert result.fact("exceeds_threshold").value is True
    assert {r.kind for r in result.fact("change_pct").refs} == {"price_record"}
    assert result.alerts == [{"kind": "price_increase", "supplier_id": "HC-S03", "month": "2026-11"}]


def test_pricing_flags_only_scenarios_a_and_b(session):
    result = PricingSpecialist(session, ActionLog()).handle(PricingRequest(intent="detect_increases"))
    assert sorted(a["supplier_id"] for a in result.alerts) == ["HC-S01", "HC-S03"]


def test_pricing_impact_is_computed_from_csv_rows(session):
    from decimal import Decimal

    result = PricingSpecialist(session, ActionLog()).handle(PricingRequest(intent="increase_impact", supplier_id="HC-S03"))
    prev, last = Decimal(result.fact("previous_price").value), Decimal(result.fact("latest_price").value)
    assert Decimal(result.fact("annual_impact").value) == ((last - prev) * 2500 * 12).quantize(Decimal("0.01"))
    assert result.fact("annual_impact").refs[0].kind == "calculation"


def test_gbp_impact_is_also_converted_to_usd(session):
    result = PricingSpecialist(session, ActionLog()).handle(PricingRequest(intent="increase_impact", supplier_id="HC-S06"))
    assert result.fact("annual_impact").unit == "GBP"
    assert result.fact("annual_impact_usd").unit == "USD"


def test_comparison_stays_in_same_category_and_country(session):
    specialist = PricingSpecialist(session, ActionLog())
    clearpath = specialist.handle(PricingRequest(intent="compare_alternatives", supplier_id="HC-S03"))
    assert [f.key for f in clearpath.facts if f.key.startswith("alt_price:")] == ["alt_price:HC-S04"]
    # Thames (laboratorio, R. U.) no tiene alternativa: ClearPath y Lone Star son de EE. UU.
    thames = specialist.handle(PricingRequest(intent="compare_alternatives", supplier_id="HC-S06"))
    assert thames.status == "no_data" and "no existe una alternativa" in thames.missing[0]
    # MedSupply (material, EE. UU.) se compara con CareSource, nunca con BritMed (R. U.).
    medsupply = specialist.handle(PricingRequest(intent="compare_alternatives", supplier_id="HC-S01"))
    assert [f.key for f in medsupply.facts if f.key.startswith("alt_price:")] == ["alt_price:HC-S02"]


# --- Especialista de contratos ---------------------------------------------------------


@pytest.mark.parametrize("supplier_id,increase,verdict", [
    ("HC-S03", "7.00", "not_permitted"),  # A: tope IPC 2,9 %
    ("HC-S01", "4.50", "permitted"),  # B: tope 5 %
    ("HC-S06", "2.00", "permitted"),  # C: tope 3 %
    ("HC-S07", "0.00", "permitted"),  # D: precio fijo, sin cambio
    ("HC-S07", "0.50", "not_permitted"),  # precio fijo: cualquier subida
])
def test_increase_permission_against_price_review_clause(session, supplier_id, increase, verdict):
    result = ContractsSpecialist(session, ActionLog(), today=TODAY).handle(
        ContractsRequest(intent="increase_permission", supplier_id=supplier_id, increase_pct=increase))
    fact = result.fact("permission")
    assert fact.value["verdict"] == verdict
    assert any(r.kind == "contract_clause" and "§4.2, p. 2" in r.locator for r in fact.refs)


def test_cpi_cap_cites_the_reference_data():
    verdict = evaluate_price_review_clause(__import__("decimal").Decimal("7"), {"type": "cap_us_cpi"},
                                           __import__("decimal").Decimal("2.9"))
    assert verdict == {"verdict": "not_permitted", "cap_pct": "2.9", "excess_pct": "4.10",
                       "basis": "tope anual del IPC de EE. UU. (2,90 %)"}


def test_missing_review_clause_is_undetermined_not_guessed():
    from decimal import Decimal

    assert evaluate_price_review_clause(Decimal("7"), None, Decimal("2.9"))["verdict"] == "undetermined"


def test_notice_deadline_for_clearpath(session):
    result = ContractsSpecialist(session, ActionLog(), today=TODAY).handle(
        ContractsRequest(intent="notice_deadline", supplier_id="HC-S03"))
    assert result.fact("notice_deadline").value == "2026-11-22"
    assert result.fact("days_to_notice_deadline").value == 15
    kinds = {a["kind"] for a in result.alerts}
    assert kinds == {"notice_deadline", "expiry_90"}


def test_britmed_has_no_contract_and_the_system_says_so(session):
    result = ContractsSpecialist(session, ActionLog(), today=TODAY).handle(
        ContractsRequest(intent="contract_terms", supplier_id="HC-S05"))
    assert result.status == "no_contract"
    assert result.facts == [] and "No hay contrato" in result.findings[0]


def test_data_protection_status_lists_thames_and_lone_star(session):
    result = ContractsSpecialist(session, ActionLog(), today=TODAY).handle(
        ContractsRequest(intent="data_protection_status"))
    assert sorted(a["supplier_id"] for a in result.alerts) == ["HC-S04", "HC-S06"]
    assert any("Thames Pathology" in f and "no incluye DPA" in f for f in result.findings)


# --- Identidad y herramientas ------------------------------------------------------------


def test_each_agent_only_has_its_specialty_tools():
    assert AGENT_TOOLS[AGENT_PRICING].isdisjoint(AGENT_TOOLS[AGENT_CONTRACTS])
    assert not any("contract" in t for t in AGENT_TOOLS[AGENT_PRICING])
    assert not any("price" in t and "review" not in t for t in AGENT_TOOLS[AGENT_CONTRACTS])
    # El supervisor no tiene herramientas de cálculo de precios ni de lectura de contratos.
    assert AGENT_TOOLS[AGENT_SUPERVISOR].isdisjoint(AGENT_TOOLS[AGENT_PRICING] | AGENT_TOOLS[AGENT_CONTRACTS])


def test_using_another_agents_tool_is_denied_and_logged(session):
    log = ActionLog()
    pricing = AgentRuntime(AGENT_PRICING, log)
    with pytest.raises(ToolNotAllowed):
        pricing.call("get_contract_terms", get_contract_terms, session, "HC-S03")
    contracts = AgentRuntime(AGENT_CONTRACTS, log)
    with pytest.raises(ToolNotAllowed):
        contracts.call("get_price_history", get_price_history, session, "HC-S03")
    assert [(a.actor, a.outcome) for a in log.actions] == [(AGENT_PRICING, "denied"), (AGENT_CONTRACTS, "denied")]


def test_every_action_is_recorded_with_the_agent_that_did_it(session):
    result = _ask(session, "¿Cuánto nos cuesta al año la subida de ClearPath Labs?")
    actions = session.exec(select(ScAgentAction).where(ScAgentAction.request_id == result.request_id)).all()
    by_tool = {(a.actor, a.tool) for a in actions if a.tool}
    assert (AGENT_SUPERVISOR, "consult_pricing_specialist") in by_tool
    assert (AGENT_PRICING, "compute_increase_impact") in by_tool
    assert (AGENT_CONTRACTS, "check_data_protection_agreement") in by_tool
    assert all(a.actor.startswith(("agent:", "human:")) for a in actions)


# --- Supervisor ---------------------------------------------------------------------------


def test_q1_clearpath_seven_percent_is_not_permitted(session):
    result = _ask(session, "¿ClearPath Labs puede subirnos un 7%?")
    assert "No permitida" in result.answer and "CT-HC-02 §4.2, p. 2" in result.answer
    assert "CT-HC-02.pdf §4.2, p. 2" in result.citations


def test_q2_annual_cost_of_clearpath_increase(session):
    result = _ask(session, "¿Cuánto nos cuesta al año la subida de ClearPath Labs?")
    assert "Impacto anual" in result.answer and any("price_history.csv fila" in c for c in result.citations)


def test_q3_alternative_to_clearpath(session):
    result = _ask(session, "¿Qué alternativa tenemos a ClearPath Labs y a qué precio?")
    assert "Lone Star Diagnostics" in result.answer and "solo cotización" in result.answer


def test_q4_patient_data_suppliers_without_agreement_is_not_refused(session):
    result = _ask(session, "¿Qué proveedores procesan datos de pacientes sin acuerdo de protección de datos firmado?")
    assert not result.refused
    assert "Thames Pathology" in result.answer and "Lone Star Diagnostics" in result.answer


def test_q5_when_to_notify_clearpath(session):
    result = _ask(session, "¿Cuándo tenemos que avisar a ClearPath Labs si no queremos renovar?")
    assert "2026-11-22" in result.answer and "CT-HC-02 §3.1, p. 1" in result.answer


def test_q6_britmed_contract_does_not_exist(session):
    result = _ask(session, "¿Qué dice el contrato de BritMed Supplies?")
    assert "No hay contrato" in result.answer and result.citations == []


def test_patient_level_request_is_refused_without_consulting_anyone(session):
    result = _ask(session, "¿Cuántas pruebas hizo ClearPath por paciente el mes pasado?")
    assert result.refused and result.consultations == []


def test_data_protection_goes_first_for_patient_data_suppliers(session):
    result = _ask(session, "¿Cuánto nos cuesta al año la subida de Thames Pathology?")
    assert result.consultations[0]["intent"] == "data_protection_status"
    assert result.answer.splitlines()[0].startswith("Thames Pathology procesa datos de pacientes")


def test_detected_increase_triggers_automatic_assessment(session):
    result = _ask(session, "¿Ha subido algún proveedor sus precios este mes?")
    intents = [(c["intent"], c["supplier_id"]) for c in result.consultations]
    for sid in ("HC-S01", "HC-S03"):
        assert ("increase_impact", sid) in intents and ("increase_permission", sid) in intents
    assert "Permitida" in result.answer and "No permitida" in result.answer


def test_model_plan_is_used_and_validated(session, fake_llm_factory):
    llm = fake_llm_factory(tool_calls=[
        {"name": "consult_contracts_specialist",
         "arguments": {"intent": "increase_permission", "supplier_id": "HC-S03", "increase_pct": None, "topic": None}},
        {"name": "consult_pricing_specialist", "arguments": {"intent": "teleport", "supplier_id": "HC-S03"}},
        {"name": "send_email_to_supplier", "arguments": {}},
    ])
    result = ask(session, "¿Está permitida la última subida de ClearPath?", requester="human:test", today=TODAY,
                 use_model=True, client=llm)
    assert result.plan_source == "model"
    # Sin porcentaje del usuario, el supervisor pide la subida al especialista de precios y la pasa.
    intents = [c["intent"] for c in result.consultations]
    assert intents.index("detect_increases") < intents.index("increase_permission")
    plan_action = session.exec(select(ScAgentAction).where(ScAgentAction.action == "plan")).first()
    assert set(plan_action.details["rejected_tools"]) == {"consult_pricing_specialist", "send_email_to_supplier"}


def test_data_protection_is_consulted_once_per_supplier(session, fake_llm_factory):
    # Regresión de la prueba con el modelo real: "BAA primero" + evaluación automática
    # de la subida consultaban dos veces el BAA de ClearPath.
    llm = fake_llm_factory(tool_calls=[
        {"name": "consult_pricing_specialist", "arguments": {"intent": "detect_increases", "supplier_id": "HC-S03"}}])
    result = ask(session, "¿Está permitida la última subida de ClearPath y cuánto nos cuesta?", requester="human:test",
                 today=TODAY, use_model=True, client=llm)
    intents = [c["intent"] for c in result.consultations]
    assert intents.count("data_protection_status") == 1
    assert {"increase_impact", "increase_permission"} <= set(intents)


def test_model_failure_falls_back_to_rules(session, fake_llm_factory):
    llm = fake_llm_factory(error=TimeoutError())
    result = ask(session, "¿Qué dice el contrato de BritMed Supplies?", requester="human:test", today=TODAY,
                 use_model=True, client=llm)
    assert result.plan_source == "rules" and "No hay contrato" in result.answer


def test_consultations_are_capped_per_request(session):
    log = ActionLog()
    sup = Supervisor(session, log, today=TODAY)
    plan = supervisor_module.Plan(consultations=[
        PricingRequest(intent="price_history", supplier_id="HC-S01") for _ in range(MAX_CONSULTATIONS_PER_REQUEST + 5)])
    sup.run_plan(plan)
    assert sup.consultations == MAX_CONSULTATIONS_PER_REQUEST and sup.limit_reached
    composed = sup.compose(log.request_id, plan)
    assert composed.limit_reached and "límite" in composed.answer


def test_question_is_never_stored_only_its_hash(session):
    question = "¿Qué dice el contrato de BritMed Supplies?"
    result = _ask(session, question)
    actions = session.exec(select(ScAgentAction).where(ScAgentAction.request_id == result.request_id)).all()
    assert question not in str([a.details for a in actions])
    assert any((a.details or {}).get("question_sha256") for a in actions)
