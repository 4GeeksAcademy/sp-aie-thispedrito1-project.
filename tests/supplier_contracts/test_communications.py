"""Comunicaciones: aprobadores del BRIEFING y nada sale sin aprobación."""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal

import pytest

from services.supplier_contracts import communications, repository
from services.supplier_contracts.models import DRAFT_APPROVED, DRAFT_PENDING, DRAFT_REJECTED, DRAFT_SENT

TODAY = date(2026, 11, 7)
MARCUS, CLAIRE, SANDRA = "clinical_ops_director", "compliance_officer", "ceo"


def _draft(session, supplier_id, purpose="renegotiation_request", **kwargs):
    kwargs.setdefault("use_model", False)
    return communications.draft_communication(session, supplier_id, purpose, requester_id="u-marcus", today=TODAY, **kwargs)


@pytest.mark.parametrize("processes,value,roles", [
    (False, None, [MARCUS]),
    (True, None, [MARCUS, CLAIRE]),
    (False, Decimal("500000"), [MARCUS]),  # "superior a": 500.000 exactos no
    (False, Decimal("500000.01"), [MARCUS, SANDRA]),
    (True, Decimal("540000"), [MARCUS, CLAIRE, SANDRA]),
])
def test_required_approvals_rule(processes, value, roles):
    assert communications.required_approvals(processes, value)[0] == roles


@pytest.mark.parametrize("supplier_id,roles", [
    ("HC-S01", [MARCUS, SANDRA]),  # MedSupply: 537.600 USD
    ("HC-S03", [MARCUS, CLAIRE, SANDRA]),  # ClearPath: datos de pacientes + 540.000 USD
    ("HC-S07", [MARCUS, CLAIRE]),  # LinguaCare: datos de pacientes, 210.600 USD
    ("HC-S02", [MARCUS]),  # CareSource: sin contrato ni datos de pacientes
])
def test_drafts_get_the_briefing_approvers(session, supplier_id, roles):
    assert _draft(session, supplier_id).required_roles == roles


def test_thames_gbp_value_is_converted_and_stays_below_threshold(session):
    repository.add_clearance(session, "HC-S06", "human:claire")
    draft = _draft(session, "HC-S06")
    assert draft.required_roles == [MARCUS, CLAIRE]  # 121.800 GBP = 154.686 USD
    assert any("CT-HC-03.pdf §7.1" in r for r in draft.refs)


def test_communication_to_supplier_without_dpa_is_blocked_until_compliance_clears(session):
    with pytest.raises(communications.CommunicationBlocked) as exc:
        _draft(session, "HC-S06")
    assert "Claire Whitfield" in exc.value.message
    keys = {a.alert_key for a in repository.list_alerts(session)}
    assert "data_protection_missing:HC-S06:open" in keys
    repository.add_clearance(session, "HC-S06", "human:claire")
    assert _draft(session, "HC-S06").status == DRAFT_PENDING


def test_nothing_is_sent_without_every_approval(session):
    draft = _draft(session, "HC-S03", purpose="price_increase_objection")
    with pytest.raises(communications.DecisionError) as exc:
        communications.send(session, draft.draft_id, user_id="u-marcus", org_role=MARCUS)
    assert exc.value.status == 409
    communications.decide(session, draft.draft_id, user_id="u-marcus", org_role=MARCUS, decision="approved")
    communications.decide(session, draft.draft_id, user_id="u-claire", org_role=CLAIRE, decision="approved")
    with pytest.raises(communications.DecisionError):  # falta la Dra. Okonkwo
        communications.send(session, draft.draft_id, user_id="u-marcus", org_role=MARCUS)
    approved = communications.decide(session, draft.draft_id, user_id="u-sandra", org_role=SANDRA, decision="approved")
    assert approved.status == DRAFT_APPROVED
    sent = communications.send(session, draft.draft_id, user_id="u-marcus", org_role=MARCUS)
    assert sent.status == DRAFT_SENT and sent.sent_at is not None


def test_wrong_approver_is_rejected_and_a_rejection_closes_the_draft(session):
    draft = _draft(session, "HC-S01")
    with pytest.raises(communications.DecisionError) as exc:
        communications.decide(session, draft.draft_id, user_id="u-claire", org_role=CLAIRE, decision="approved")
    assert exc.value.status == 403  # Claire no aprueba un proveedor sin datos de pacientes
    rejected = communications.decide(session, draft.draft_id, user_id="u-sandra", org_role=SANDRA,
                                     decision="rejected", comment="Esperar al cierre del trimestre")
    assert rejected.status == DRAFT_REJECTED
    with pytest.raises(communications.DecisionError):
        communications.decide(session, draft.draft_id, user_id="u-marcus", org_role=MARCUS, decision="approved")


def test_only_clinical_operations_marks_as_sent(session):
    draft = _draft(session, "HC-S02")
    communications.decide(session, draft.draft_id, user_id="u-marcus", org_role=MARCUS, decision="approved")
    with pytest.raises(communications.DecisionError) as exc:
        communications.send(session, draft.draft_id, user_id="u-sandra", org_role=SANDRA)
    assert exc.value.status == 403


def test_objection_without_review_clause_is_not_drafted(session):
    with pytest.raises(communications.CommunicationBlocked):
        _draft(session, "HC-S05", purpose="price_increase_objection")  # BritMed: sin contrato
    with pytest.raises(communications.CommunicationBlocked):
        _draft(session, "HC-S05", purpose="non_renewal_notice")


def test_model_draft_is_used_when_it_only_uses_given_facts(session, fake_llm_factory):
    llm = fake_llm_factory(content=json.dumps({"subject": "Notice of non-renewal - CT-HC-04",
                                               "body": "Dear LinguaCare Interpreting team, we will not renew CT-HC-04, "
                                                       "which expires on 2026-12-05."}))
    draft = _draft(session, "HC-S07", purpose="non_renewal_notice", use_model=True, client=llm)
    assert draft.draft_source == "model" and "CT-HC-04" in draft.subject
    assert draft.drafted_by == "agent:supplier-supervisor"


def test_model_draft_with_invented_figure_falls_back_to_template(session, fake_llm_factory):
    llm = fake_llm_factory(content=json.dumps({"subject": "Price review",
                                               "body": "We propose a 12% discount and payment in 45 days."}))
    draft = _draft(session, "HC-S03", purpose="price_increase_objection", use_model=True, client=llm)
    assert draft.draft_source == "template" and "12%" not in draft.body


def test_validate_draft_rules():
    facts = {"latest_price": "19.2342", "change_pct": "7.00", "expiry_date": "2027-01-06"}
    assert communications.validate_draft("Price", "An increase of 7% to 19.2342 USD; expires January 6, 2027.", facts) is None
    assert communications.validate_draft("Price", "Increase of 8%", facts) == "invented_figures"
    assert communications.validate_draft("Price", "Write to a@b.example", facts) == "sensitive_data"
