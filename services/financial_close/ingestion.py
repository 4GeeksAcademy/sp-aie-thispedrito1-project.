"""Ingesta de ledger.csv, budget.csv y operations.csv a un formato común.

Cada registro normalizado conserva SIEMPRE su origen (`source_file` +
`source_row`, y en el ledger además `entry_id` y `source_ref`): es lo que
permite que cualquier cifra del informe se rastree hasta la fila que la
produce.

Nada se descarta en silencio ni se da por válido sin más: un registro
incompleto o incoherente se conserva marcado como `doubtful`, con la lista de
motivos (`issues`). Los registros dudosos NO entran en los totales; el
informe los declara en "Datos faltantes o dudosos".

Datos sensibles (BRIEFING, "Restricción específica"): la descripción de cada
registro pasa por `sensitive.screen` AQUÍ, antes de llegar a la base de datos
o al agente. Si contiene algo, el registro es dudoso y su descripción se
sustituye por `None` (solo queda su huella SHA-256).
"""

from __future__ import annotations

import csv
import hashlib
import re
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from services.financial_close import sensitive
from services.financial_close.catalog import (
    BUDGET_SUFFIX,
    COUNTRIES,
    COUNTRY_CURRENCY,
    EXPENSE_CATEGORIES,
    INTERPRETING,
    LAB,
    NO_PAYER,
    PAYERS,
    REVENUE_CATEGORY,
    SUPPLIES,
)
from services.financial_close.seed_data import BUDGET_COLUMNS, LEDGER_COLUMNS, OPERATIONS_COLUMNS

OK = "ok"
DOUBTFUL = "doubtful"

LEDGER_FILE, BUDGET_FILE, OPERATIONS_FILE = "ledger.csv", "budget.csv", "operations.csv"
SUPPLIER_CATEGORIES = (SUPPLIES, LAB, INTERPRETING)
# "500 tests @ 32.10 USD"
_UNIT_DETAIL = re.compile(r"(?P<qty>\d+(?:\.\d+)?)\s+(?:units|tests|hours)\s+@\s+(?P<price>\d+(?:\.\d+)?)\s+(?P<cur>USD|GBP)")

# Motivos legibles (los muestra el informe; nunca llevan el contenido del registro).
ISSUE_LABELS = {
    "en": {
        "missing_field": "missing required field", "invalid_amount": "amount is not a number",
        "invalid_date": "date is not valid", "outside_period": "date outside the closing month",
        "unknown_country": "unknown country", "currency_mismatch": "currency does not match the country",
        "sign_inconsistent": "sign inconsistent with revenue/expense", "payer_on_expense": "expense with a payer",
        "unknown_payer": "unknown payer", "unknown_category": "unknown category",
        "duplicate_entry_id": "duplicate entry_id", "missing_unit_detail": "supplier entry without quantity and unit price",
        "unit_price_mismatch": "quantity x unit price does not match the amount",
        "sensitive_data:patient_data": "description may identify a patient (withheld; Compliance notified)",
        "sensitive_data:individual_salary": "description contains an individual salary (withheld)",
        "sensitive_data:bank_details": "description contains supplier bank details (withheld)",
        "invalid_value": "value is not a number", "duplicate_line": "duplicate budget line",
        "non_positive_budget": "budget amount must be positive",
    },
    "es": {
        "missing_field": "falta un campo obligatorio", "invalid_amount": "el importe no es un número",
        "invalid_date": "la fecha no es válida", "outside_period": "fecha fuera del mes de cierre",
        "unknown_country": "país desconocido", "currency_mismatch": "la moneda no corresponde al país",
        "sign_inconsistent": "signo incoherente con ingreso/gasto", "payer_on_expense": "gasto con pagador",
        "unknown_payer": "pagador desconocido", "unknown_category": "categoría desconocida",
        "duplicate_entry_id": "entry_id duplicado",
        "missing_unit_detail": "registro de proveedor sin cantidad ni precio unitario",
        "unit_price_mismatch": "cantidad x precio unitario no coincide con el importe",
        "sensitive_data:patient_data": "la descripción puede identificar a un paciente (retenida; Cumplimiento avisado)",
        "sensitive_data:individual_salary": "la descripción contiene un salario individual (retenida)",
        "sensitive_data:bank_details": "la descripción contiene datos bancarios de un proveedor (retenida)",
        "invalid_value": "el valor no es un número", "duplicate_line": "línea de presupuesto duplicada",
        "non_positive_budget": "el presupuesto debe ser positivo",
    },
}


@dataclass(frozen=True)
class LedgerRecord:
    entry_id: str
    entry_date: Optional[date]
    country: str
    payer: str
    category: str
    currency: str
    amount: Optional[Decimal]
    source_ref: str
    source_row: int
    description: Optional[str]  # None si se retuvo por datos sensibles
    description_sha256: str
    status: str
    issues: Tuple[str, ...]
    sensitive_categories: Tuple[str, ...] = ()
    quantity: Optional[Decimal] = None
    unit_price: Optional[Decimal] = None
    source_file: str = LEDGER_FILE

    @property
    def is_ok(self) -> bool:
        return self.status == OK

    @property
    def description_withheld(self) -> bool:
        return self.description is None


@dataclass(frozen=True)
class BudgetRecord:
    country: str
    payer: str
    category: str
    currency: str
    amount: Optional[Decimal]
    source_row: int
    status: str
    issues: Tuple[str, ...]
    source_file: str = BUDGET_FILE

    @property
    def line_key(self) -> str:
        return f"{self.country}.{self.payer}.{self.category}"


@dataclass(frozen=True)
class OperationRecord:
    country: str
    payer: str
    metric: str
    value: Optional[Decimal]
    source_row: int
    status: str
    issues: Tuple[str, ...]
    source_file: str = OPERATIONS_FILE

    @property
    def is_ok(self) -> bool:
        return self.status == OK


@dataclass
class PeriodDataset:
    month: date
    ledger: List[LedgerRecord] = field(default_factory=list)
    budget: List[BudgetRecord] = field(default_factory=list)
    operations: List[OperationRecord] = field(default_factory=list)
    missing_files: List[str] = field(default_factory=list)
    fingerprint: str = ""


def _decimal(value: Optional[str]) -> Optional[Decimal]:
    try:
        number = Decimal((value or "").strip())
    except InvalidOperation:
        return None
    return number if number.is_finite() else None


def _sha(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def _read(path: Path, columns: List[str]) -> List[Dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        missing = [c for c in columns if c not in (reader.fieldnames or [])]
        if missing:
            raise ValueError(f"{path.name}: faltan columnas {missing}")
        return [dict(row) for row in reader]


def normalize_ledger_row(row: Dict[str, str], source_row: int, month: date) -> LedgerRecord:
    issues: List[str] = []
    get = lambda name: (row.get(name) or "").strip()  # noqa: E731
    for name in ("entry_id", "date", "country", "payer", "category", "currency", "amount", "source_ref"):
        if not get(name):
            issues.append("missing_field")
            break

    amount = _decimal(get("amount"))
    if get("amount") and amount is None:
        issues.append("invalid_amount")
    entry_date: Optional[date] = None
    try:
        entry_date = date.fromisoformat(get("date"))
    except ValueError:
        if get("date"):
            issues.append("invalid_date")
    if entry_date and (entry_date.year, entry_date.month) != (month.year, month.month):
        issues.append("outside_period")

    country, payer, category, currency = get("country"), get("payer"), get("category"), get("currency")
    if country and country not in COUNTRIES:
        issues.append("unknown_country")
    elif country and currency and COUNTRY_CURRENCY[country] != currency:
        issues.append("currency_mismatch")

    is_revenue = category == REVENUE_CATEGORY
    if is_revenue:
        if payer not in PAYERS:
            issues.append("unknown_payer")
        if amount is not None and amount <= 0:
            issues.append("sign_inconsistent")
    else:
        if payer and payer != NO_PAYER:
            issues.append("payer_on_expense")
        if amount is not None and amount >= 0:
            issues.append("sign_inconsistent")

    description = row.get("description") or ""
    screening = sensitive.screen(description)
    quantity = unit_price = None
    if category in SUPPLIER_CATEGORIES and not screening.is_sensitive:
        match = _UNIT_DETAIL.search(description)
        if not match:
            issues.append("missing_unit_detail")
        else:
            quantity, unit_price = Decimal(match["qty"]), Decimal(match["price"])
            if amount is not None and quantity * unit_price != abs(amount):
                issues.append("unit_price_mismatch")
    for category_name in screening.categories:
        issues.append(f"sensitive_data:{category_name}")

    # Sin duplicados en los motivos, en orden de aparición.
    unique = tuple(dict.fromkeys(issues))
    return LedgerRecord(
        entry_id=get("entry_id"), entry_date=entry_date, country=country, payer=payer, category=category,
        currency=currency, amount=amount, source_ref=get("source_ref"), source_row=source_row,
        description=None if screening.is_sensitive else description,
        description_sha256=_sha(description), status=DOUBTFUL if unique else OK, issues=unique,
        sensitive_categories=screening.categories, quantity=quantity, unit_price=unit_price,
    )


def _with_issue(record, issue: str):
    from dataclasses import replace

    issues = tuple(dict.fromkeys((*record.issues, issue)))
    return replace(record, status=DOUBTFUL, issues=issues)


def read_ledger(path: Path, month: date) -> List[LedgerRecord]:
    records: List[LedgerRecord] = []
    seen = set()
    # Fila 1 = cabecera: la primera fila de datos es la 2 (como en una hoja de cálculo).
    for index, row in enumerate(_read(path, LEDGER_COLUMNS), start=2):
        record = normalize_ledger_row(row, index, month)
        if record.entry_id and record.entry_id in seen:
            record = _with_issue(record, "duplicate_entry_id")
        seen.add(record.entry_id)
        records.append(record)
    return records


def read_budget(path: Path, month: date) -> List[BudgetRecord]:
    records: List[BudgetRecord] = []
    seen = set()
    tag = month.strftime("%Y-%m")
    for index, row in enumerate(_read(path, BUDGET_COLUMNS), start=2):
        get = lambda name: (row.get(name) or "").strip()  # noqa: E731
        issues: List[str] = []
        if get("month") != tag:
            issues.append("outside_period")
        amount = _decimal(get("amount"))
        if amount is None:
            issues.append("invalid_amount")
        elif amount <= 0:
            issues.append("non_positive_budget")
        country, currency, category = get("country"), get("currency"), get("category")
        if country not in COUNTRIES:
            issues.append("unknown_country")
        elif COUNTRY_CURRENCY[country] != currency:
            issues.append("currency_mismatch")
        if category != REVENUE_CATEGORY and category not in EXPENSE_CATEGORIES:
            issues.append("unknown_category")
        record = BudgetRecord(country=country, payer=get("payer"), category=category, currency=currency,
                              amount=amount, source_row=index, status=DOUBTFUL if issues else OK,
                              issues=tuple(dict.fromkeys(issues)))
        if record.line_key in seen:
            record = _with_issue(record, "duplicate_line")
        seen.add(record.line_key)
        records.append(record)
    return records


def read_operations(path: Path, month: date) -> List[OperationRecord]:
    records: List[OperationRecord] = []
    tag = month.strftime("%Y-%m")
    for index, row in enumerate(_read(path, OPERATIONS_COLUMNS), start=2):
        get = lambda name: (row.get(name) or "").strip()  # noqa: E731
        issues: List[str] = []
        if get("month") != tag:
            issues.append("outside_period")
        value = _decimal(get("value"))
        if value is None:
            issues.append("invalid_value")
        records.append(OperationRecord(country=get("country"), payer=get("payer"), metric=get("metric"), value=value,
                                       source_row=index, status=DOUBTFUL if issues else OK, issues=tuple(issues)))
    return records


def read_dataset(data_dir: Path, month: date) -> PeriodDataset:
    """Lee las tres fuentes del mes. Un archivo ausente no rompe la ingesta:
    queda en `missing_files` y el informe lo declara."""
    dataset = PeriodDataset(month=month)
    digest = hashlib.sha256(month.isoformat().encode())
    for name, reader, attr in ((LEDGER_FILE, read_ledger, "ledger"), (BUDGET_FILE, read_budget, "budget"),
                               (OPERATIONS_FILE, read_operations, "operations")):
        path = data_dir / name
        if not path.exists():
            dataset.missing_files.append(name)
            continue
        digest.update(name.encode() + b"\0" + path.read_bytes())
        setattr(dataset, attr, reader(path, month))
    dataset.fingerprint = digest.hexdigest()
    return dataset


def is_budget_metric(metric: str) -> bool:
    return metric.endswith(BUDGET_SUFFIX)
