"""Brief de renegociación: combina a los dos especialistas, con trazabilidad."""

from __future__ import annotations

import re
from dataclasses import asdict
from datetime import date
from decimal import Decimal

import pytest

from services.supplier_contracts import repository
from services.supplier_contracts.brief import BriefInputs, build_brief, recommend_actions
from services.supplier_contracts.supervisor import ask

TODAY = date(2026, 11, 7)

CLEARPATH = BriefInputs(
    supplier_name="ClearPath Labs", currency="USD", change_pct=Decimal("7.00"), exceeds_threshold=True,
    permission="not_permitted", cap_pct=Decimal("2.9"), annual_impact=Decimal("37749.00"),
    alternative_name="Lone Star Diagnostics", alternative_saving=Decimal("60621.00"), alternative_has_contract=False,
    alternative_agreement_ok=False, notice_deadline="2026-11-22", days_to_notice_deadline=15,
    exit_summary="Salida y penalización: rescisión anticipada con penalización de 2 meses de facturación media",
    exit_cost=Decimal("90813.33"), has_contract=True,
)
MEDSUPPLY = BriefInputs(
    supplier_name="MedSupply Partners", currency="USD", change_pct=Decimal("4.50"), exceeds_threshold=True,
    permission="permitted", cap_pct=Decimal("5"), annual_impact=Decimal("24477.60"),
    alternative_name="CareSource Medical", alternative_saving=Decimal("0"), alternative_has_contract=False,
    alternative_agreement_ok=None, notice_deadline="2027-03-07", days_to_notice_deadline=120, has_contract=True,
)
NO_CONTRACT = BriefInputs(supplier_name="BritMed Supplies", currency="GBP", change_pct=Decimal("0.31"),
                          permission=None, has_contract=False)


def _numbers(text: str) -> set:
    """Números normalizados: "37.749,00" → "37749", "2,9" → "2.9", "2026-11-22" → {"2026","11","22"}."""
    out = set()
    for token in re.findall(r"\d[\d.,]*\d|\d", text):
        token = token.replace(".", "").replace(",", ".") if "," in token else token
        if token.count(".") > 1:
            token = token.replace(".", "")
        if "." in token:
            token = token.rstrip("0").rstrip(".")
        out.add(token.lstrip("0") or "0")
    return out


@pytest.mark.parametrize("inputs", [CLEARPATH, MEDSUPPLY, NO_CONTRACT], ids=["clearpath", "medsupply", "no-contract"])
def test_recommendation_is_deterministic_and_never_invents_figures(inputs):
    first, second = recommend_actions(inputs), recommend_actions(inputs)
    assert first == second and first and all(isinstance(item, str) and item for item in first)
    allowed = _numbers(" ".join(str(v) for v in asdict(inputs).values() if v is not None))
    invented = _numbers(" ".join(first)) - allowed
    assert invented == set(), f"Cifras que no salen de los datos: {invented}"


def test_clearpath_brief_combines_both_specialists(session):
    brief = build_brief(session, "HC-S03", requester="human:test", today=TODAY)
    assert brief.status == "ready"
    agents = {c["agent_id"] for c in brief.consultations}
    assert agents == {"agent:pricing-specialist", "agent:contracts-specialist"}
    assert brief.consultations[0]["intent"] == "data_protection_status"  # BAA antes que precios
    assert set(brief.sections) == {"Protección de datos", "Histórico de precios", "Subida e impacto",
                                   "Comparativa (misma categoría y país)", "Margen contractual"}
    margin = " ".join(brief.sections["Margen contractual"])
    assert "2026-11-22" in margin and "Coste estimado de salida anticipada" in margin
    assert any("Lone Star Diagnostics" in f and "BAA" in f for f in brief.sections["Comparativa (misma categoría y país)"])
    assert "No permitida" in " ".join(brief.sections["Subida e impacto"])
    assert brief.recommendation


def test_brief_is_fully_traceable(session):
    brief = build_brief(session, "HC-S03", requester="human:test", today=TODAY)
    assert any(c.startswith("price_history.csv fila") for c in brief.citations)
    for clause in ("§2.1, p. 1", "§3.1, p. 1", "§4.2, p. 2", "§5.1, p. 2", "§6.1, p. 2"):
        assert any(clause in c for c in brief.citations), clause
    # El histórico va en tabla, mes a mes, con la fila de origen de cada precio.
    assert brief.markdown.count("| price_history.csv fila") == 12
    assert "## Trazabilidad" in brief.markdown and "## Recomendación" in brief.markdown


def test_thames_brief_stops_and_alerts_compliance(session):
    brief = build_brief(session, "HC-S06", requester="human:test", today=TODAY)
    assert brief.status == "blocked" and "Claire Whitfield" in brief.message
    assert [c["intent"] for c in brief.consultations] == ["data_protection_status"]  # nada de precios
    alert = {a.alert_key: a for a in repository.list_alerts(session)}["data_protection_missing:HC-S06:open"]
    assert alert.recipient_role == "compliance_officer"


def test_thames_brief_continues_after_compliance_clearance(session):
    repository.add_clearance(session, "HC-S06", "human:claire")
    brief = build_brief(session, "HC-S06", requester="human:test", today=TODAY)
    assert brief.status == "ready" and "Cumplimiento autorizó continuar" in brief.message
    assert any("no existe una alternativa" in m for m in brief.missing)


def test_brief_without_contract_declares_it(session):
    brief = build_brief(session, "HC-S05", requester="human:test", today=TODAY)
    assert brief.status == "ready"
    assert "Contrato con BritMed Supplies" in brief.missing
    assert "Margen contractual" in brief.sections


def test_q7_brief_from_a_free_question(session):
    result = ask(session, "Prepárame un brief para renegociar con ClearPath Labs.", requester="human:test",
                 today=TODAY, use_model=False)
    assert result.brief and result.brief["status"] == "ready"
    assert result.answer.startswith("# Brief de renegociación — ClearPath Labs")
