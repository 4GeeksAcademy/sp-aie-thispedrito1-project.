"""Datos sensibles del BRIEFING: nunca en base de datos, logs, prompts ni salidas.

El contrato ficticio de cada proveedor lleva datos bancarios y de contacto en
su cláusula de facturación (catalog.SENSITIVE_BILLING). Tras recorrer todo el
sistema (ingesta, barrido de alertas, preguntas, brief y borradores con un
modelo que INTENTA colar un IBAN), ninguno de esos valores aparece en:
- ninguna fila de ninguna tabla sc_*,
- los logs,
- los prompts enviados al modelo,
- las respuestas.
"""

from __future__ import annotations

import json
import logging
from datetime import date

import pytest
from sqlalchemy import text

from services.supplier_contracts import alerts, communications, repository, sensitive, supervisor
from services.supplier_contracts.catalog import SENSITIVE_BILLING
from services.supplier_contracts.models import SC_TABLES

TODAY = date(2026, 11, 7)
SENSITIVE_VALUES = sorted({v for billing in SENSITIVE_BILLING.values() for v in billing.values()})


def _dump_tables(session) -> str:
    rows = []
    for table in SC_TABLES:
        rows += [str(tuple(r)) for r in session.exec(text(f"SELECT * FROM {table.name}")).all()]
    return "\n".join(rows)


def test_sensitive_values_never_leave_the_pdfs(session, fake_llm_factory, caplog):
    caplog.set_level(logging.DEBUG)
    planner = fake_llm_factory(tool_calls=[{"name": "consult_contracts_specialist", "arguments": {
        "intent": "clause_lookup", "supplier_id": "HC-S03", "increase_pct": None, "topic": "exit_terms"}}])
    # Un modelo de redacción que intenta incluir datos bancarios y de contacto.
    leaky = fake_llm_factory(content=json.dumps({
        "subject": "Price review",
        "body": "Please contact Gregory Halvorsen at g.halvorsen@clearpath-labs.example; "
                "pay to Account number: 7781-0034-5529."}))
    outputs = []
    outputs.append(alerts.run_alert_sweep(session, today=TODAY))
    for question in ("¿Qué dice el contrato de ClearPath Labs?", "¿Cuándo tenemos que avisar a ClearPath Labs?",
                     "¿Cuál es la cuenta bancaria de ClearPath Labs para pagarles?"):
        outputs.append(supervisor.ask(session, question, requester="human:t", today=TODAY, use_model=False).model_dump())
    outputs.append(supervisor.ask(session, "¿Cómo podemos salir del contrato de ClearPath?", requester="human:t",
                                  today=TODAY, use_model=True, client=planner).model_dump())
    draft = communications.draft_communication(session, "HC-S03", "renegotiation_request", requester_id="u",
                                               today=TODAY, use_model=True, client=leaky)
    outputs.append(communications.draft_view(session, draft))
    from services.supplier_contracts.brief import BriefResult  # noqa: F401  (el brief se cubre en test_brief)

    haystacks = {
        "base de datos": _dump_tables(session),
        "logs": caplog.text,
        "prompts": planner.prompts_text() + leaky.prompts_text(),
        "respuestas": json.dumps(outputs, default=str, ensure_ascii=False),
    }
    leaks = {where: [v for v in SENSITIVE_VALUES if v in content] for where, content in haystacks.items()}
    assert leaks == {where: [] for where in haystacks}
    assert draft.draft_source == "template"  # el borrador con datos sensibles se descartó


@pytest.mark.parametrize("raw", [
    "Bank: Thames Mercantile Bank; Account number: 31926819; Sort code: 40-11-62; IBAN: GB29TMBK40116231926819.",
    "Contact person: Imogen Ashcroft; email i.ashcroft@thames-pathology.example; phone +44 20 7946 0331.",
    "Routing number: 114924742",
])
def test_redact_masks_bank_and_contact_data(raw):
    masked = sensitive.redact(raw)
    for value in SENSITIVE_VALUES:
        assert value not in masked
    assert sensitive.redact(masked) == masked  # idempotente


def test_redact_keeps_dates_amounts_and_percentages():
    clean = "Expires on 2027-01-06; USD 540,000; increase of +4.5% (CT-HC-02 §4.2, p. 2)."
    assert sensitive.redact(clean) == clean


@pytest.mark.parametrize("question,refused", [
    ("¿Cuántas pruebas hizo ClearPath por paciente?", True),
    ("Dame la lista de pacientes atendidos por LinguaCare", True),
    ("¿Qué pacientes usaron interpretación en octubre?", True),
    ("Show the per-patient usage for ClearPath", True),
    ("¿Cuánto costó la interpretación del paciente Johnson?", True),
    ("¿Qué proveedores procesan datos de pacientes sin acuerdo de protección de datos firmado?", False),
    ("¿ClearPath Labs puede subirnos un 7%?", False),
])
def test_patient_level_requests(question, refused):
    assert sensitive.is_patient_level_request(question) is refused


def test_price_csv_has_only_aggregates(data_dir):
    header = (data_dir / "price_history.csv").read_text().splitlines()[0]
    assert "patient" not in header.lower()
