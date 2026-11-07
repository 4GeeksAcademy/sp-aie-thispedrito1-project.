"""Alertas: tipos, destinatarios, antelación e idempotencia."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from services.supplier_contracts import alerts, repository

TODAY = date(2026, 11, 7)

EXPECTED = {
    "price_increase:HC-S01:2026-11": "clinical_ops_director",  # B
    "price_increase:HC-S03:2026-11": "clinical_ops_director",  # A
    "notice_deadline:HC-S03:2026-11-22": "clinical_ops_director",  # A: preaviso en 15 días
    "expiry_90:HC-S03:2027-01-06": "clinical_ops_director",
    "notice_deadline:HC-S07:2026-11-20": "clinical_ops_director",
    "expiry_30:HC-S07:2026-12-05": "clinical_ops_director",  # D: vence en 28 días
    "data_protection_missing:HC-S06:open": "compliance_officer",  # C: falta el DPA
    "data_protection_missing:HC-S04:open": "compliance_officer",  # Lone Star: sin contrato ni BAA
}


def test_sweep_raises_exactly_the_briefing_alerts(session):
    created = alerts.run_alert_sweep(session, today=TODAY)
    assert {a.alert_key: a.recipient_role for a in created} == EXPECTED


def test_sweep_is_idempotent(session):
    alerts.run_alert_sweep(session, today=TODAY)
    assert alerts.run_alert_sweep(session, today=TODAY) == []
    assert len(repository.list_alerts(session)) == len(EXPECTED)


def test_price_alert_carries_automatic_assessment(session):
    alerts.run_alert_sweep(session, today=TODAY)
    by_key = {a.alert_key: a for a in repository.list_alerts(session)}
    clearpath = by_key["price_increase:HC-S03:2026-11"]
    assert clearpath.details["permission"]["verdict"] == "not_permitted"
    assert clearpath.details["annual_impact"]
    assert "No permitida" in clearpath.summary and "CT-HC-02.pdf §4.2, p. 2" in clearpath.refs
    medsupply = by_key["price_increase:HC-S01:2026-11"]
    assert medsupply.details["permission"]["verdict"] == "permitted"
    assert clearpath.raised_by == "agent:supplier-supervisor"


def test_thames_small_increase_raises_no_price_alert_but_dpa_alert(session):
    alerts.run_alert_sweep(session, today=TODAY)
    keys = {a.alert_key for a in repository.list_alerts(session)}
    assert not any(k.startswith("price_increase:HC-S06") for k in keys)
    assert "data_protection_missing:HC-S06:open" in keys


@pytest.mark.parametrize("offset,expected", [
    # MedSupply vence en LOAD+180 y su preaviso de 60 días cae en LOAD+120.
    (89, set()),  # 91 días al vencimiento, 31 a la fecha límite
    (90, {"expiry_90"}),  # 90 días exactos
    (105, {"expiry_90", "notice_deadline"}),  # 15 días a la fecha límite
    (150, {"expiry_30"}),  # 30 días al vencimiento; la fecha límite ya pasó
])
def test_medsupply_alerts_fire_with_the_defined_lead_time(session, offset, expected):
    today = TODAY + timedelta(days=offset)
    alerts.run_alert_sweep(session, today=today)
    kinds = {a.kind for a in repository.list_alerts(session) if a.supplier_id == "HC-S01" and a.kind != "price_increase"}
    assert kinds == expected
