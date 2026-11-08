"""Revisión humana y distribución (BRIEFING, "Revisión y distribución" y "Restricción específica")."""

from __future__ import annotations

import json

import pytest
from sqlmodel import select

from fc_helpers import LAB_US, PATIENT_NAME, PERIOD, good_draft, good_plan
from services.financial_close import repository, workflow
from services.financial_close.catalog import DISTRIBUTION_ROLES
from services.financial_close.models import FcAgentAction, FcDistribution, FcNotification, FcReport

TOM = "revenue_cycle_director"


def _close(session, engine, **kwargs):
    return workflow.run_close(session, engine, PERIOD, trigger=kwargs.pop("trigger", "manual"),
                              use_model=kwargs.pop("use_model", False), **kwargs)


def test_ingestion_notifies_compliance_once_with_entry_id_only(session, data_dir, engine):
    notes = repository.notifications_for_role(session, "compliance_officer")
    assert [(n.kind, n.entry_id) for n in notes] == [("patient_data_in_source", "JE-2026OCT-0053")]
    assert PATIENT_NAME not in notes[0].summary
    assert workflow.ingest_period(session, data_dir, PERIOD) == 0  # reingerir no duplica
    assert len(repository.notifications_for_role(session, "compliance_officer")) == 1


def test_withheld_description_is_not_stored(session):
    stored = repository.ledger_entries(session, PERIOD, ["JE-2026OCT-0053"])[0]
    assert stored.description is None and stored.status == "doubtful"


def test_close_starts_pending_and_notifies_the_reviewer(session, engine):
    row = _close(session, engine)
    assert (row.status, row.version) == ("pending_review", 1)
    assert "Draft pending review" in row.report_en and "Borrador pendiente" in row.report_es
    notes = repository.notifications_for_role(session, TOM)
    assert notes[0].kind == "report_pending_review" and notes[0].report_id == row.id
    assert not session.exec(select(FcDistribution)).all()


def test_only_the_reviewer_can_decide(session, engine):
    row = _close(session, engine)
    with pytest.raises(workflow.DecisionError) as exc:
        workflow.decide(session, row.id, user_id="u-ceo", org_role="ceo", decision="approve")
    assert exc.value.status == 403
    assert repository.get_report(session, row.id).status == "pending_review"


def test_rejection_requires_comments_without_sensitive_data(session, engine):
    row = _close(session, engine)
    for comments in (None, "   "):
        with pytest.raises(workflow.DecisionError) as exc:
            workflow.decide(session, row.id, user_id="tom", org_role=TOM, decision="reject", comments=comments)
        assert exc.value.field == "comments"
    with pytest.raises(workflow.DecisionError) as exc:
        workflow.decide(session, row.id, user_id="tom", org_role=TOM, decision="reject",
                        comments="Check the refund to patient Emily Hartwell")
    assert PATIENT_NAME not in exc.value.message
    assert repository.get_report(session, row.id).status == "pending_review"


def test_rejection_produces_a_new_version_with_the_comments(session, engine, fake_llm):
    row = _close(session, engine)
    comments = "Please explain the lab deviation with the price per test and keep it short."
    workflow.decide(session, row.id, user_id="tom", org_role=TOM, decision="reject", comments=comments)
    assert repository.get_report(session, row.id).status == "rejected"

    note = {"en": "Lab explanation now leads with the price per test.",
            "es": "La explicación del laboratorio empieza por el precio por prueba."}
    llm = fake_llm([good_plan(), good_draft(revision_note=note)])
    v2 = workflow.revise(session, engine, row.id, client=llm, use_model=True)
    assert (v2.version, v2.supersedes_id, v2.status, v2.trigger) == (2, row.id, "pending_review", "revision")
    assert comments in llm.prompts_text()  # el modelo recibe los comentarios
    assert comments in v2.report_en and note["en"] in v2.report_en and note["es"] in v2.report_es
    assert v2.figures_sha256 == row.figures_sha256  # mismas cifras: solo cambia el texto
    assert workflow.revise(session, engine, row.id).id == v2.id  # reintento idempotente


def test_approval_distributes_only_to_the_briefing_recipients(session, engine):
    row = _close(session, engine)
    workflow.decide(session, row.id, user_id="tom", org_role=TOM, decision="approve")
    row = repository.get_report(session, row.id)
    assert row.status == "distributed" and row.distributed_at is not None
    recipients = [d.recipient_role for d in repository.distributions_for(session, row.id)]
    assert recipients == list(DISTRIBUTION_ROLES) and len(recipients) == 7
    assert "pending review" not in row.report_en and "status: distributed" in row.report_en
    assert "Draft pending review" not in row.report_en


def test_a_decision_cannot_be_repeated(session, engine):
    row = _close(session, engine)
    workflow.decide(session, row.id, user_id="tom", org_role=TOM, decision="approve")
    with pytest.raises(workflow.DecisionError) as exc:
        workflow.decide(session, row.id, user_id="tom", org_role=TOM, decision="reject", comments="late")
    assert exc.value.status == 409


def test_distribution_requires_approval(session, engine):
    row = _close(session, engine)
    with pytest.raises(workflow.DecisionError):
        workflow.distribute(session, row, log=workflow.ActionLog())


def test_sensitive_text_blocks_distribution(session, engine):
    row = _close(session, engine)
    row.report_en += "\nRefund to patient Emily Hartwell"
    session.add(row)
    session.commit()
    with pytest.raises(workflow.DecisionError) as exc:
        workflow.decide(session, row.id, user_id="tom", org_role=TOM, decision="approve")
    assert exc.value.status == 409
    assert not session.exec(select(FcDistribution)).all()
    assert repository.get_report(session, row.id).status == "pending_review"


def test_actions_identify_process_and_people_without_sensitive_data(session, engine, fake_llm):
    row = _close(session, engine, client=fake_llm([good_plan(), good_draft()]), use_model=True)
    workflow.decide(session, row.id, user_id="tom-user-id", org_role=TOM, decision="approve")
    actions = session.exec(select(FcAgentAction)).all()
    by_type = {(a.actor_type, a.actor.split(":")[0]) for a in actions}
    assert by_type == {("automated_process", "process"), ("human", "human")}
    dump = json.dumps([a.model_dump(mode="json") for a in actions])
    assert PATIENT_NAME not in dump and "tom-user-id" not in dump
    stored = json.dumps([r.model_dump(mode="json") for r in session.exec(select(FcReport)).all()])
    stored += json.dumps([n.model_dump(mode="json") for n in session.exec(select(FcNotification)).all()])
    assert PATIENT_NAME not in stored and "Hartwell" not in stored


def test_two_closes_on_same_data_have_identical_figures(session, engine):
    first = _close(session, engine)
    second = _close(session, engine)
    assert second.version == first.version + 1
    assert first.figures == second.figures and first.figures_sha256 == second.figures_sha256
    assert first.report_en.split("## 1.")[1] == second.report_en.split("## 1.")[1]  # sin modelo: idéntico


def test_model_text_does_not_change_figures(session, engine, fake_llm):
    with_model = _close(session, engine, client=fake_llm([good_plan(), good_draft()]), use_model=True)
    without = _close(session, engine)
    assert with_model.figures_sha256 == without.figures_sha256
    assert with_model.trace["narrative_sources"][LAB_US] == "model"


def test_the_close_agent_is_recorded_as_a_process_in_the_audit_log(session, engine):
    from services.audit import catalog as audit_catalog
    from services.audit.models import AuditLogEntry

    row = _close(session, engine)
    entry = session.exec(select(AuditLogEntry)).one()
    assert (entry.actor_type, entry.actor_id) == ("process", audit_catalog.PROCESS_FINANCIAL_CLOSE_AGENT)
    assert entry.action == "financial_report_prepared" and entry.resource_id == str(row.id)
    assert entry.department_id == audit_catalog.department_id("revenue_cycle")
    assert entry.origin.startswith("process:financial_close_agent:pid")
    # Ninguna cifra ni texto del informe: solo periodo, versión y la huella.
    assert PATIENT_NAME not in entry.details and set(json.loads(entry.details)) == {
        "period", "version", "trigger", "figures_sha256"
    }
