"""Agente de cierre + informe: el modelo decide y redacta; el código calcula y valida."""

from __future__ import annotations

import json
import re
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from fc_helpers import LAB_US, MEDICARE, PATIENT_NAME, PERIOD, good_draft, good_plan
from services.financial_close import formatting as fmt
from services.financial_close import ingestion, report
from services.financial_close.agent import CloseAgent
from services.financial_close.runtime import ActionLog, AgentRuntime, ToolNotAllowed, read_only_session

SECTIONS = {
    "en": ["1. Executive summary", "2. Consolidated result (USD)", "3. Revenue by country and payer",
           "4. Expenses by country", "5. Relevant deviations", "6. Missing or doubtful data", "7. Traceability annex"],
    "es": ["1. Resumen ejecutivo", "2. Resultado consolidado (USD)", "3. Ingresos por país y pagador",
           "4. Gastos por país", "5. Desviaciones relevantes", "6. Datos faltantes o dudosos", "7. Anexo de trazabilidad"],
}


def _analyze(dataset, client=None, use_model=False, comments=None):
    log = ActionLog()
    return CloseAgent(log, client=client, use_model=use_model).analyze_dataset(dataset, comments=comments), log


def _render(analysis, lang):
    return report.render(analysis, report.build_figures(analysis), lang=lang, version=1, status="pending_review")


# --- Estructura y contenido ------------------------------------------------------------


@pytest.mark.parametrize("lang", ["en", "es"])
def test_report_has_the_seven_sections_in_order(dataset, lang):
    analysis, _ = _analyze(dataset)
    body = _render(analysis, lang)
    positions = [body.index(f"## {title}") for title in SECTIONS[lang]]
    assert positions == sorted(positions)


def test_executive_summary_has_at_most_five_lines(dataset):
    analysis, _ = _analyze(dataset)
    assert all(len(lines) <= 5 for lines in analysis.narrative.summary.values())


def test_relevant_deviations_have_hypotheses_with_supporting_figures(dataset):
    analysis, _ = _analyze(dataset)
    assert [d.comparison.key for d in analysis.deviations] == [MEDICARE, LAB_US]
    body = _render(analysis, "en")
    medicare = body.split("### Medicare / Medicaid (US)")[1].split("###")[0]
    assert "Hypothesis:" in medicare and "14.0%" in medicare and "22.6%" in medicare
    assert "driver:US.medicare_medicaid.patient_services:denial_rate_pct" in medicare
    lab = body.split("### Laboratory — ClearPath Labs (US)")[1].split("##")[0]
    assert "30.00" in lab and "32.10" in lab and "price change" in lab


def test_below_threshold_lines_are_not_deviations(dataset):
    body = _render(_analyze(dataset)[0], "en")
    section5 = body.split("## 5.")[1].split("## 6.")[0]
    assert "MedSupply" not in section5 and "Private (UK)" not in section5


def test_missing_licences_declared_not_zero(dataset):
    body = _render(_analyze(dataset)[0], "en")
    assert "| Clinical software licences (UK) | 8,000.00 | No records | — | — |" in body
    section6 = body.split("## 6.")[1].split("## 7.")[0]
    assert "Clinical software licences (UK)" in section6 and "not treated as 0" in section6
    assert "-100" not in body and "−100.0" not in body


def test_patient_record_appears_only_by_entry_id(dataset):
    analysis, log = _analyze(dataset)
    for lang in ("en", "es"):
        body = _render(analysis, lang)
        assert PATIENT_NAME not in body and "Hartwell" not in body
        line = next(l for l in body.splitlines() if "JE-2026OCT-0053" in l)
        assert "185" not in line  # ni el importe: solo el entry_id
    assert PATIENT_NAME not in json.dumps(report.build_figures(analysis))


def test_traceability_every_figure_has_sources(dataset):
    figures = report.build_figures(_analyze(dataset)[0])
    for fid, fig in figures.items():
        if fig.get("status") == "missing":
            assert fig["value"] is None and fig["sources"] == []
        else:
            assert fig["sources"], fid
    medicare = figures[f"line:{MEDICARE}:actual"]
    assert [s["entry_id"] for s in medicare["sources"]] == [f"JE-2026OCT-{i:04d}" for i in range(10, 19)]
    assert figures[f"line:{MEDICARE}:budget"]["sources"] == [{"kind": "budget", "source_file": "budget.csv",
                                                              "source_row": 3}]


def _numbers(body: str, lang: str):
    thousands, dec = (",", ".") if lang == "en" else (".", ",")
    out = []
    for token in re.findall(r"\d[\d.,]*\d|\d", body.split("## 2.")[1]):
        try:
            out.append(Decimal(token.replace(thousands, "").replace(dec, ".")))
        except ArithmeticError:
            pass
    return sorted(out)


def test_figures_are_identical_in_english_and_spanish(dataset):
    analysis, _ = _analyze(dataset)
    # Secciones 2 a 7 son las tablas y anexos generados por código: mismas cifras.
    assert _numbers(_render(analysis, "en"), "en") == _numbers(_render(analysis, "es"), "es")


def test_running_twice_gives_exactly_the_same_figures(dataset, data_dir):
    first = report.build_figures(_analyze(dataset)[0])
    second = report.build_figures(_analyze(ingestion.read_dataset(data_dir, PERIOD))[0])
    assert first == second and report.figures_sha256(first) == report.figures_sha256(second)


def test_incomplete_period_declares_what_is_missing(incomplete_dir):
    analysis, _ = _analyze(ingestion.read_dataset(incomplete_dir, PERIOD))
    body = _render(analysis, "en")
    medicare = body.split("### Medicare / Medicaid (US)")[1].split("###")[0]
    assert "\n\nUnexplained: " in medicare and "Hypothesis:" not in medicare
    assert "No data-supported hypothesis" in medicare and "Denial rate" in medicare
    assert "operations.csv — claims_submitted" in body.split("## 6.")[1]


# --- El modelo ---------------------------------------------------------------------------


def test_model_plan_and_draft_are_used_when_valid(dataset, fake_llm):
    llm = fake_llm([good_plan(), good_draft()])
    analysis, log = _analyze(dataset, client=llm, use_model=True)
    assert {d.plan_source for d in analysis.deviations} == {"model"}
    assert analysis.narrative.source == "model"
    assert analysis.narrative.hypotheses[LAB_US]["es"].startswith("Las mismas 1.500 pruebas")
    body = _render(analysis, "en")
    assert "Hypothesis: Same 1,500 tests" in body  # el prefijo lo pone el código


def test_model_with_invented_figure_falls_back_to_template(dataset, fake_llm):
    draft = json.loads(good_draft()["content"])
    draft["hypotheses"][LAB_US]["en"] = "The price per test rose to 33.50 USD."  # 33,50 no está en los hechos
    llm = fake_llm([good_plan(), {"content": json.dumps(draft)}])
    analysis, log = _analyze(dataset, client=llm, use_model=True)
    assert analysis.narrative.sources[LAB_US] == "template"
    assert analysis.narrative.sources[MEDICARE] == "model"
    assert "33.50" not in _render(analysis, "en")
    drafted = [a for a in log.actions if a.action == "draft_narrative"][0]
    assert drafted.details["rejected"] == {LAB_US: "invented_figures"}


def test_model_plan_with_wrong_tool_is_replaced_by_rules(dataset, fake_llm):
    plan = {"tool_calls": [{"name": "unit_cost_drivers", "arguments": {"line_key": MEDICARE}}]}
    analysis, _ = _analyze(dataset, client=fake_llm([plan, good_draft()]), use_model=True)
    medicare = next(d for d in analysis.deviations if d.comparison.key == MEDICARE)
    assert medicare.plan_source == "rules" and medicare.driver.tool == "payer_claims_drivers"


def test_model_down_still_produces_the_same_figures(dataset, fake_llm):
    llm = fake_llm([{"error": TimeoutError("proxy")}])
    with_failure, _ = _analyze(dataset, client=llm, use_model=True)
    without_model, _ = _analyze(dataset)
    assert report.build_figures(with_failure) == report.build_figures(without_model)
    assert with_failure.narrative.source == "template"


def test_model_never_sees_descriptions_or_patient_data(dataset, fake_llm):
    llm = fake_llm([good_plan(), good_draft()])
    _analyze(dataset, client=llm, use_model=True)
    prompts = llm.prompts_text()
    assert PATIENT_NAME not in prompts and "Hartwell" not in prompts
    assert "remittances" not in prompts and "invoice" not in prompts  # ninguna descripción del ledger


def test_model_only_sees_the_comparable_budget_and_line_kind(dataset, fake_llm):
    # Regresión de la prueba real: con el presupuesto completo delante, el modelo
    # emparejó "0,8 %" con 1.705.142 (el % es contra el comparable, 1.694.982).
    llm = fake_llm([good_plan(), good_draft()])
    _analyze(dataset, client=llm, use_model=True)
    facts = llm.calls[1]["messages"][1]["content"]
    assert "1694982.00" in facts and "1705142.00" not in facts
    assert '"kind": "expense"' in facts and "never call it revenue" in llm.calls[1]["messages"][0]["content"]


def test_model_summary_with_patient_data_is_rejected(dataset, fake_llm):
    draft = json.loads(good_draft()["content"])
    draft["executive_summary"]["en"].append("Refund to patient Emily Hartwell pending.")
    analysis, _ = _analyze(dataset, client=fake_llm([good_plan(), {"content": json.dumps(draft)}]), use_model=True)
    assert analysis.narrative.sources["summary"] == "template"
    assert PATIENT_NAME not in "\n".join(analysis.narrative.summary["en"])


def test_number_validation_accepts_both_formats():
    allowed = fmt.allowed_numbers([Decimal("1120000.00"), Decimal("22.6")])
    assert not fmt.invented_numbers("1,120,000.00 USD and 22.6%", allowed)
    assert not fmt.invented_numbers("1.120.000,00 USD y 22,6 %", allowed)
    assert fmt.invented_numbers("1,130,000 USD", allowed) == {"1,130,000"}


# --- Identidad, herramientas y solo lectura -------------------------------------------------


def test_actions_are_recorded_as_automated_process(dataset):
    _, log = _analyze(dataset)
    tools = [a.tool for a in log.actions if a.action == "tool_call"]
    assert tools[:5] == ["compute_line_totals", "compare_to_budget", "consolidate_usd", "find_relevant_deviations",
                         "data_quality_report"]
    assert {a.actor for a in log.actions} == {"process:agent:financial-close"}
    assert {a.actor_type for a in log.actions} == {"automated_process"}


def test_tool_outside_the_list_is_denied():
    log = ActionLog()
    with pytest.raises(ToolNotAllowed):
        AgentRuntime(log).call("write_ledger", lambda: None)
    assert log.actions[-1].outcome == "denied"


def test_agent_reads_period_in_read_only_mode(engine, session):
    with read_only_session(engine) as ro:
        with pytest.raises(OperationalError):
            ro.exec(text("DELETE FROM fc_ledger_entries"))
    # Al salir se restaura la escritura para el resto del proceso.
    session.exec(text("SELECT 1"))
    analysis = CloseAgent(ActionLog(), use_model=False).analyze(engine, PERIOD)
    assert len(analysis.deviations) == 2


def test_logs_never_carry_patient_data(dataset, fake_llm, caplog):
    caplog.set_level("INFO")
    _analyze(dataset, client=fake_llm([good_plan(), good_draft()]), use_model=True)
    assert caplog.records and PATIENT_NAME not in caplog.text
