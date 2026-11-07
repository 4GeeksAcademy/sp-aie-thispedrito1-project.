"""Generador de las tres fuentes del cierre (BRIEFING, "Fuentes de datos").

Escribe, para el MES DE CIERRE (el último mes completo antes de la fecha de
carga):

- `ledger.csv`: varios registros por partida cuya suma coincide EXACTAMENTE
  con la cifra real del BRIEFING. Ingresos positivos y agregados por clínica y
  pagador (nunca por paciente); gastos negativos con `payer = n/a`; en las
  partidas de proveedores la descripción lleva proveedor, cantidad y precio
  unitario. Incluye a propósito UN gasto cuya descripción tiene el nombre
  ficticio de un paciente (reembolso mal registrado).
- `budget.csv`: presupuesto del mes, en positivo como en el BRIEFING.
- `operations.csv`: indicadores agregados; el presupuesto de cada indicador va
  como `<indicador>_budget` (el CSV solo tiene una columna `value`).

`incomplete=True` genera el escenario "datos incompletos" del PR: sin los
indicadores operativos de Medicare/Medicaid, la desviación sigue apareciendo
pero el informe debe decir que no hay datos para explicarla.

Determinista: misma fecha de carga → mismos archivos byte a byte.
"""

from __future__ import annotations

import csv
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Dict, List, Sequence, Tuple

from services.financial_close.catalog import (
    BUDGET_SUFFIX,
    CLINICS,
    LINES,
    MEDICARE,
    NHS,
    NHS_CLINICS,
    NO_PAYER,
    OPERATIONS,
    PAYROLL,
    RENT,
    REVENUE_CATEGORY,
    US,
    LineSpec,
)

LEDGER_COLUMNS = ["entry_id", "date", "country", "payer", "category", "description", "currency", "amount", "source_ref"]
BUDGET_COLUMNS = ["month", "country", "payer", "category", "currency", "amount"]
OPERATIONS_COLUMNS = ["month", "country", "payer", "metric", "value"]

# Registro trampa del BRIEFING ("Restricción específica"). Nombre ficticio.
PATIENT_RECORD_CATEGORY = "patient_refunds"
PATIENT_RECORD_DESCRIPTION = "Refund to patient Emily Hartwell - duplicate co-payment posted to clinic expenses"
PATIENT_RECORD_AMOUNT = Decimal("-185.00")

UNIT_NAMES = {"clinical_supply_units": "units", "laboratory_tests": "tests", "interpreting_hours": "hours"}
# Pesos fijos para repartir una cifra entre clínicas (deterministas, no aleatorios).
_WEIGHTS = (13, 12, 11, 11, 10, 10, 12, 11, 10)
_MONTH_CODES = ("JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC")


def closing_month(load_date: date) -> date:
    """Primer día del último mes completo antes de la fecha de carga."""
    first_of_this_month = load_date.replace(day=1)
    return (first_of_this_month - timedelta(days=1)).replace(day=1)


def _last_day(month: date) -> date:
    return (month.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)


def split_amount(total: Decimal, weights: Sequence[int]) -> List[Decimal]:
    """Reparte `total` (en unidades enteras) según pesos; el último absorbe el resto."""
    total_weight = sum(weights)
    parts = [(total * w / total_weight).quantize(Decimal("1")) for w in weights[:-1]]
    parts.append(total - sum(parts, Decimal("0")))
    return parts


def _month_tag(month: date) -> str:
    return month.strftime("%Y%m")


def _revenue_rows(line: LineSpec, month: date) -> List[Tuple[str, str, str]]:
    """(clínica, descripción, source_ref) + importe: agregados por clínica y pagador."""
    clinics = NHS_CLINICS if line.payer == NHS else CLINICS[line.country]
    amounts = split_amount(line.actual, _WEIGHTS[: len(clinics)])
    rows = []
    for clinic, amount in zip(clinics, amounts):
        rows.append((f"{clinic} - {line.payer} remittances, aggregated for the month",
                     f"REMIT-{clinic}-{_month_tag(month)}", amount))
    return rows


def _supplier_rows(line: LineSpec, month: date) -> List[Tuple[str, str, Decimal]]:
    units = (line.actual / line.unit_price)
    if units != units.to_integral_value():
        raise ValueError(f"{line.key}: cantidad no entera ({units})")
    invoices = 3 if units >= 900 else 2
    unit_parts = split_amount(units, [1] * invoices)
    noun = UNIT_NAMES[line.unit_metric]
    prefix = "".join(word[0] for word in line.supplier.split()).upper()
    rows = []
    for i, part in enumerate(unit_parts, start=1):
        amount = part * line.unit_price
        rows.append((f"{line.supplier} - invoice {i}/{invoices}: {part} {noun} @ {line.unit_price} {line.currency}",
                     f"AP-INV-{prefix}-{_month_tag(month)}-{i:02d}", amount))
    return rows


def _payroll_rows(line: LineSpec, month: date) -> List[Tuple[str, str, Decimal]]:
    halves = split_amount(line.actual, [1, 1])
    return [(f"{line.country} payroll - aggregate pay run {i} of 2 (country total, no individual salaries)",
             f"PAYRUN-{line.country}-{_month_tag(month)}-{i}", amount) for i, amount in enumerate(halves, start=1)]


def _rent_rows(line: LineSpec, month: date) -> List[Tuple[str, str, Decimal]]:
    clinics = CLINICS[line.country]
    amounts = split_amount(line.actual, [1] * len(clinics))
    return [(f"{clinic} - clinic lease, monthly rent", f"LEASE-{clinic}-{_month_tag(month)}", amount)
            for clinic, amount in zip(clinics, amounts)]


def ledger_rows(month: date) -> List[Dict[str, str]]:
    rows: List[Dict[str, str]] = []
    last = _last_day(month)
    counter = 0

    def add(line_country: str, payer: str, category: str, currency: str, description: str, ref: str,
            amount: Decimal) -> None:
        nonlocal counter
        counter += 1
        day = min(1 + (counter * 3) % 28, last.day)
        rows.append({
            # Mes con letras a propósito: "JE-202610-0001" tiene la forma 3-3-4
            # dígitos de un NHS number y "JE-2026-10-0001" diez dígitos con
            # guiones, como un teléfono; el filtro de PHI marcaría el informe.
            "entry_id": f"JE-{month.year}{_MONTH_CODES[month.month - 1]}-{counter:04d}",
            "date": month.replace(day=day).isoformat(),
            "country": line_country, "payer": payer, "category": category, "description": description,
            "currency": currency, "amount": f"{amount:.2f}", "source_ref": ref,
        })

    for line in LINES:
        if line.actual is None:
            continue  # "Sin registros": no se inventa ninguno
        if line.category == REVENUE_CATEGORY:
            parts = _revenue_rows(line, month)
            sign = Decimal("1")
        elif line.supplier:
            parts = _supplier_rows(line, month)
            sign = Decimal("-1")
        elif line.category == PAYROLL:
            parts = _payroll_rows(line, month)
            sign = Decimal("-1")
        elif line.category == RENT:
            parts = _rent_rows(line, month)
            sign = Decimal("-1")
        else:
            raise ValueError(f"Partida sin generador: {line.key}")
        for description, ref, amount in parts:
            add(line.country, line.payer, line.category, line.currency, description, ref, sign * amount)
    # El registro con datos de un paciente (BRIEFING, "Restricción específica").
    add(US, NO_PAYER, PATIENT_RECORD_CATEGORY, "USD", PATIENT_RECORD_DESCRIPTION, f"AP-REFUND-{_month_tag(month)}-07",
        PATIENT_RECORD_AMOUNT)
    return rows


def budget_rows(month: date) -> List[Dict[str, str]]:
    tag = month.strftime("%Y-%m")
    return [{"month": tag, "country": line.country, "payer": line.payer, "category": line.category,
             "currency": line.currency, "amount": f"{line.budget:.2f}"} for line in LINES]


def operations_rows(month: date, *, incomplete: bool = False) -> List[Dict[str, str]]:
    tag = month.strftime("%Y-%m")
    rows = []
    for country, payer, metric, budget, actual in OPERATIONS:
        if incomplete and country == US and payer == MEDICARE:
            continue
        rows.append({"month": tag, "country": country, "payer": payer, "metric": metric, "value": str(actual)})
        rows.append({"month": tag, "country": country, "payer": payer, "metric": metric + BUDGET_SUFFIX,
                     "value": str(budget)})
    return rows


def _write(path: Path, columns: List[str], rows: List[Dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def generate_all(out_dir: Path, load_date: date, *, incomplete: bool = False) -> date:
    """Escribe los tres CSV en `out_dir` y devuelve el mes de cierre."""
    month = closing_month(load_date)
    _write(out_dir / "ledger.csv", LEDGER_COLUMNS, ledger_rows(month))
    _write(out_dir / "budget.csv", BUDGET_COLUMNS, budget_rows(month))
    _write(out_dir / "operations.csv", OPERATIONS_COLUMNS, operations_rows(month, incomplete=incomplete))
    return month
