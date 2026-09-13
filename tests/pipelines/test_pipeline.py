"""Tests unitarios de las tasks de transformacion del pipeline de negocio.

Pipeline: monthly_clinic_supply_performance ("Reporte Mensual de Desempeno
de Insumos por Clinica"). Cada test llama a la funcion de una task de
Prefect (`task.fn`) con eventos en memoria con la forma de telemetry_events
(propiedades de docs/telemetry/event-schemas.json): sin base de datos, sin
servidor de Prefect y sin APIs externas.

Definiciones de la seccion 4 del CONTEXT que se verifican:
- total_supply_cost        = suma de los costos de inbound_order_created del mes
- supply_consumption_count = conteo de outbound_order_created del mes
- critical_stockout_count  = conteo de stock_threshold_triggered del mes
- expiry_risk_count        = conteo de supply_expiry_flagged del mes
- currency                 = USD (clinicas US) o GBP (clinicas UK), sin convertir
"""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal
from typing import Any

import pandas as pd
import pytest

from data.pipelines.pipeline import (
    CaptureGapError,
    assemble_monthly_clinic_supply_performance,
    compute_critical_stockout_count,
    compute_expiry_risk_count,
    compute_supply_consumption_count,
    compute_total_supply_cost,
    prepare_clinic_supply_events,
    validate_monthly_aggregates,
)
from data.process.supply_performance_transforms import events_to_frame

AUGUST_2026 = date(2026, 8, 1)


# --- Fixtures en memoria con forma de telemetria ----------------------------


def telemetry_event(timestamp: str, event_type: str, event_id: str | None = None, **properties: Any) -> dict:
    """Una fila de telemetry_events tal como la devuelve la extraccion:
    envelope (eventId, sessionId) + properties del catalogo en `tags`."""
    return {
        "id": str(uuid.uuid4()),
        "timestamp": timestamp,
        "event_type": event_type,
        "tags": {"eventId": event_id or str(uuid.uuid4()), "sessionId": "session-austin-1", **properties},
    }


def inbound_order_created(timestamp: str, clinic_id: int, country: str, quantity: int, unit_cost: Any, delivery_id: int, **extra):
    properties = {"clinic_id": clinic_id, "country": country, "product_id": 1, "product_category": "ppe", "quantity": quantity, "vendor_name": "MedLine Industries", "delivery_id": delivery_id, **extra}
    if unit_cost is not None:
        properties["unit_cost"] = unit_cost
    return telemetry_event(timestamp, "inbound_order_created", **properties)


def outbound_order_created(timestamp: str, clinic_id: Any, country: str, quantity: int, consumption_id: int, **extra):
    return telemetry_event(
        timestamp,
        "outbound_order_created",
        clinic_id=clinic_id,
        country=country,
        product_id=1,
        product_category="ppe",
        quantity=quantity,
        department=None,
        consumption_type="clinical_use",
        consumption_id=consumption_id,
        **extra,
    )


def stock_threshold_triggered(timestamp: str, clinic_id: int, country: str):
    return telemetry_event(timestamp, "stock_threshold_triggered", clinic_id=clinic_id, country=country, product_id=1, product_category="ppe", current_stock=40, threshold_value=50)


def supply_expiry_flagged(timestamp: str, clinic_id: int, country: str):
    return telemetry_event(timestamp, "supply_expiry_flagged", clinic_id=clinic_id, country=country, product_id=6, product_category="medications", expiry_date="2026-09-01", days_until_expiry=20, quantity_at_risk=40)


def supply_events_frame(*events: dict) -> pd.DataFrame:
    """DataFrame de eventos ya preparado (validado y deduplicado), que es lo
    que reciben las tasks de KPI dentro del subflow."""
    return prepare_clinic_supply_events.fn(list(events), AUGUST_2026)["events"]


# --- KPI "Costo de insumos por clinica" -------------------------------------


def test_compute_total_supply_cost_matches_context_definition_for_hand_computed_input():
    """CONTEXT: total_supply_cost = suma de los costos de inbound_order_created.
    Calculado a mano para la clinica 7 (US):
        120 unidades x 0.35 USD = 42.00
      +  40 unidades x 1.10 USD = 44.00
      = 86.00 USD
    La clinica 10 (UK) compra aparte: 10 x 2.50 = 25.00 GBP, nunca sumado a USD."""
    supply_events = supply_events_frame(
        inbound_order_created("2026-08-02T09:00:00", clinic_id=7, country="US", quantity=120, unit_cost=0.35, delivery_id=1),
        inbound_order_created("2026-08-15T09:00:00", clinic_id=7, country="US", quantity=40, unit_cost=1.10, delivery_id=2),
        inbound_order_created("2026-08-20T09:00:00", clinic_id=10, country="UK", quantity=10, unit_cost=2.50, delivery_id=3),
        # Un consumo no suma coste: el KPI solo mira compras.
        outbound_order_created("2026-08-21T09:00:00", clinic_id=7, country="US", quantity=5, consumption_id=1),
    )

    result = compute_total_supply_cost.fn(supply_events)

    assert result["total_supply_cost"] == {"7": Decimal("86.00"), "10": Decimal("25.00")}
    assert result["missing_cost"] == {}


def test_compute_total_supply_cost_never_turns_an_unknown_or_malformed_cost_into_zero():
    """Defensivo: la task recibe filas que la validacion no filtro (se la
    llama directamente con events_to_frame). Un coste ausente, negativo o que
    no es un numero se excluye de la suma y se cuenta como coste desconocido;
    una fila sin clinic_id o sin cantidad valida se ignora sin lanzar."""
    supply_events = events_to_frame(
        [
            inbound_order_created("2026-08-02T09:00:00", clinic_id=2, country="US", quantity=10, unit_cost=3.00, delivery_id=1),
            inbound_order_created("2026-08-03T09:00:00", clinic_id=2, country="US", quantity=10, unit_cost=None, delivery_id=2),
            inbound_order_created("2026-08-04T09:00:00", clinic_id=2, country="US", quantity=10, unit_cost="gratis", delivery_id=3),
            inbound_order_created("2026-08-05T09:00:00", clinic_id=2, country="US", quantity=10, unit_cost=-4.0, delivery_id=4),
            inbound_order_created("2026-08-06T09:00:00", clinic_id=None, country="US", quantity=10, unit_cost=9.99, delivery_id=5),
            inbound_order_created("2026-08-07T09:00:00", clinic_id=2, country="US", quantity=None, unit_cost=9.99, delivery_id=6),
        ]
    )

    result = compute_total_supply_cost.fn(supply_events)

    assert result["total_supply_cost"] == {"2": Decimal("30.00")}
    assert result["missing_cost"] == {"2": 3}


# --- KPI "Volumen de consumo de insumos" ------------------------------------


def test_compute_supply_consumption_count_counts_deduplicated_outbound_orders_per_clinic():
    retried_event_id = str(uuid.uuid4())
    supply_events = supply_events_frame(
        outbound_order_created("2026-08-04T09:00:00", clinic_id=1, country="US", quantity=5, consumption_id=11, event_id=retried_event_id),
        # Reintento de red del TelemetryService: mismo eventId -> una sola orden.
        outbound_order_created("2026-08-04T09:00:02", clinic_id=1, country="US", quantity=5, consumption_id=11, event_id=retried_event_id),
        # Doble track() de la misma orden: eventId distinto, mismo consumption_id.
        outbound_order_created("2026-08-04T09:00:03", clinic_id=1, country="US", quantity=5, consumption_id=11),
        outbound_order_created("2026-08-09T09:00:00", clinic_id=1, country="US", quantity=1, consumption_id=12),
        outbound_order_created("2026-08-09T10:00:00", clinic_id=10, country="UK", quantity=3, consumption_id=13),
        stock_threshold_triggered("2026-08-09T10:00:01", clinic_id=10, country="UK"),
    )

    assert compute_supply_consumption_count.fn(supply_events) == {"1": 2, "10": 1}


def test_compute_supply_consumption_count_ignores_malformed_clinic_ids_without_failing():
    """Defensivo: clinic_id como texto, decimal o fuera del rango 1-12 no
    identifica una clinica de la red; la fila se ignora y la task no lanza."""
    supply_events = events_to_frame(
        [
            outbound_order_created("2026-08-04T09:00:00", clinic_id="7", country="US", quantity=1, consumption_id=1),
            outbound_order_created("2026-08-04T09:00:00", clinic_id=7.0, country="US", quantity=1, consumption_id=2),
            outbound_order_created("2026-08-04T09:00:00", clinic_id=13, country="US", quantity=1, consumption_id=3),
            outbound_order_created("2026-08-04T09:00:00", clinic_id=7, country="US", quantity=1, consumption_id=4),
        ]
    )

    assert compute_supply_consumption_count.fn(supply_events) == {"7": 1}


# --- KPI "Frecuencia de quiebre critico" ------------------------------------


def test_compute_critical_stockout_count_is_the_literal_count_of_threshold_events():
    supply_events = supply_events_frame(
        stock_threshold_triggered("2026-08-05T09:00:00", clinic_id=4, country="US"),
        stock_threshold_triggered("2026-08-19T09:00:00", clinic_id=4, country="US"),
        stock_threshold_triggered("2026-08-19T11:00:00", clinic_id=11, country="UK"),
    )

    assert compute_critical_stockout_count.fn(supply_events) == {"4": 2, "11": 1}


# --- KPI "Conteo de riesgo de vencimiento" ----------------------------------


def test_compute_expiry_risk_count_is_the_literal_count_of_expiry_flags():
    supply_events = supply_events_frame(
        supply_expiry_flagged("2026-08-10T02:00:00", clinic_id=12, country="UK"),
        supply_expiry_flagged("2026-08-11T02:00:00", clinic_id=12, country="UK"),
        # Septiembre queda fuera de la ventana de agosto.
        supply_expiry_flagged("2026-09-01T00:00:00", clinic_id=12, country="UK"),
    )

    assert compute_expiry_risk_count.fn(supply_events) == {"12": 2}


def test_kpi_tasks_return_empty_results_for_a_month_without_events():
    supply_events = supply_events_frame()

    assert compute_total_supply_cost.fn(supply_events) == {"total_supply_cost": {}, "missing_cost": {}}
    assert compute_supply_consumption_count.fn(supply_events) == {}
    assert compute_critical_stockout_count.fn(supply_events) == {}
    assert compute_expiry_risk_count.fn(supply_events) == {}


# --- Preparacion (validar + deduplicar) --------------------------------------


def test_prepare_clinic_supply_events_discards_malformed_rows_instead_of_failing():
    """Defensivo: una fila corrupta no tumba el mes. tags que no es un objeto,
    timestamp ilegible, clinic_id nulo o pais fuera de US/UK se cuentan como
    invalidos y el resto del mes se procesa."""
    corrupt_tags = telemetry_event("2026-08-03T09:00:00", "outbound_order_created")
    corrupt_tags["tags"] = "not-a-json-object"
    unreadable_timestamp = outbound_order_created("hoy por la tarde", clinic_id=1, country="US", quantity=1, consumption_id=1)

    prepared = prepare_clinic_supply_events.fn(
        [
            corrupt_tags,
            unreadable_timestamp,
            outbound_order_created("2026-08-03T09:00:00", clinic_id=None, country="US", quantity=1, consumption_id=2),
            outbound_order_created("2026-08-03T09:00:00", clinic_id=1, country="ES", quantity=1, consumption_id=3),
            outbound_order_created("2026-08-03T09:00:00", clinic_id=1, country="US", quantity=1, consumption_id=4),
        ],
        AUGUST_2026,
    )

    assert prepared["counts"]["rows_extracted"] == 5
    assert prepared["counts"]["rows_invalid"] == 4
    assert prepared["counts"]["rows_after_dedup"] == 1
    assert compute_supply_consumption_count.fn(prepared["events"]) == {"1": 1}


# --- Ensamblado de filas y validacion ---------------------------------------


def test_assemble_monthly_clinic_supply_performance_builds_one_row_per_clinic_with_its_currency():
    supply_events = supply_events_frame(
        inbound_order_created("2026-08-02T09:00:00", clinic_id=7, country="US", quantity=120, unit_cost=0.35, delivery_id=1),
        supply_expiry_flagged("2026-08-10T02:00:00", clinic_id=12, country="UK"),
        # Clinica 3 con eventos de dos paises: se rechaza, nunca mezcla monedas.
        outbound_order_created("2026-08-11T09:00:00", clinic_id=3, country="US", quantity=1, consumption_id=1),
        outbound_order_created("2026-08-12T09:00:00", clinic_id=3, country="UK", quantity=1, consumption_id=2),
    )

    assembled = assemble_monthly_clinic_supply_performance.fn(
        AUGUST_2026,
        supply_events,
        compute_total_supply_cost.fn(supply_events),
        compute_supply_consumption_count.fn(supply_events),
        compute_critical_stockout_count.fn(supply_events),
        compute_expiry_risk_count.fn(supply_events),
    )

    assert assembled["rows"] == [
        {
            "clinic_id": "7",
            "country": "US",
            "month_start": AUGUST_2026,
            "total_supply_cost": Decimal("42.00"),
            "supply_consumption_count": 0,
            "critical_stockout_count": 0,
            "expiry_risk_count": 0,
            "currency": "USD",
        },
        {
            "clinic_id": "12",
            "country": "UK",
            "month_start": AUGUST_2026,
            "total_supply_cost": Decimal("0.00"),
            "supply_consumption_count": 0,
            "critical_stockout_count": 0,
            "expiry_risk_count": 1,
            "currency": "GBP",
        },
    ]
    assert assembled["rejected"] == [{"clinic_id": "3", "reason": "mixed_country", "countries": ["UK", "US"]}]


def test_validate_monthly_aggregates_blocks_publication_when_capture_is_broken():
    """Cero eventos de ordenes en el mes con 5 entregas reales en inventario:
    la captura esta rota y publicar ceros enganaria a la junta."""
    with pytest.raises(CaptureGapError):
        validate_monthly_aggregates.fn({}, {"deliveries": {"1": 5}, "consumptions": {}})

    validation = validate_monthly_aggregates.fn(
        {"1": {"inbound_order_created": 5, "outbound_order_created": 2}},
        {"deliveries": {"1": 5}, "consumptions": {"1": 2}},
    )
    assert validation["blocking_error"] is None
    assert validation["warnings"] == []
