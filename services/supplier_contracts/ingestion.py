"""Ingesta: registro de proveedores, histórico de precios y contratos en PDF.

Cada registro conserva su origen: fila del CSV (la cabecera es la fila 1) o
contrato + cláusula + página. Una fila inválida NO se corrige ni se estima:
se rechaza con su motivo y queda en `sc_ingestion_runs.rejected_rows`.

Contratos (decisión del usuario: extracción determinista, sin modelo):
1. `pdfplumber` da el texto de cada página (la página es la real del PDF).
2. Se reconstruyen las cláusulas: una línea que no empieza por "N.N" o "N."
   continúa la anterior (pdfplumber parte las cláusulas largas).
3. Cada término tiene sus reglas. Coincidencia exacta → confianza 0,95;
   coincidencia parcial o contradictoria → por debajo de 0,8 y estado
   `doubtful`; sin cláusula → `missing`. Nunca se rellena un término que el
   contrato no dice.

La cláusula de facturación (datos bancarios y de contacto) nunca se lee para
extraer términos ni se cita: `EXCLUDED_CLAUSE_HEADINGS`. Toda cita guardada
pasa además por `sensitive.redact`.
"""

from __future__ import annotations

import csv
import hashlib
import re
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from services.supplier_contracts.catalog import (
    AGREEMENT_BAA,
    AGREEMENT_DPA,
    AGREEMENT_MISSING,
    AGREEMENT_NOT_APPLICABLE,
    DOUBTFUL_CONFIDENCE,
    REVIEW_CAP_PERCENT,
    REVIEW_CAP_US_CPI,
    REVIEW_FIXED,
    find_supplier,
)
from services.supplier_contracts.models import TERM_DOUBTFUL, TERM_MISSING, TERM_OK
from services.supplier_contracts.sensitive import redact

PRICE_COLUMNS = ["supplier_id", "product", "unit", "currency", "month", "unit_price", "monthly_volume"]
SUPPLIER_COLUMNS = ["supplier_id", "name", "country", "category", "currency", "processes_patient_data"]

TERMS = ("expiry_date", "non_renewal_notice", "price_review", "exit_terms", "data_protection", "annual_value")
TERM_LABELS = {
    "expiry_date": "Fecha de vencimiento",
    "non_renewal_notice": "Preaviso de no renovación",
    "price_review": "Revisión de precio",
    "exit_terms": "Salida y penalización",
    "data_protection": "Acuerdo de protección de datos",
    "annual_value": "Valor anual estimado",
}

EXCLUDED_CLAUSE_HEADINGS = re.compile(r"(?i)invoic|payment|billing|bank")
QUOTE_MAX = 400

HIGH = 0.95
PARTIAL = 0.55


# --- CSV --------------------------------------------------------------------------


@dataclass
class Rejected:
    file: str
    row: int
    reason: str


def _read_csv(path: Path, columns: List[str]) -> List[Tuple[int, Dict[str, str]]]:
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames != columns:
            raise ValueError(f"{path.name}: columnas {reader.fieldnames} distintas de las del BRIEFING {columns}")
        return [(index, row) for index, row in enumerate(reader, start=2)]


def load_suppliers(path: Path) -> Tuple[List[Dict[str, Any]], List[Rejected]]:
    out, rejected = [], []
    for row_number, row in _read_csv(path, SUPPLIER_COLUMNS):
        flag = (row.get("processes_patient_data") or "").strip().lower()
        if flag not in {"yes", "no"} or row["country"] not in {"US", "UK"} or row["currency"] not in {"USD", "GBP"}:
            rejected.append(Rejected(path.name, row_number, "Valores fuera de lista (país, moneda o datos de pacientes)."))
            continue
        out.append({**row, "processes_patient_data": flag == "yes", "source_file": path.name, "source_row": row_number})
    return out, rejected


def load_prices(path: Path, suppliers: Dict[str, Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], List[Rejected]]:
    out, rejected = [], []
    seen = set()
    for row_number, row in _read_csv(path, PRICE_COLUMNS):
        def reject(reason: str) -> None:
            rejected.append(Rejected(path.name, row_number, reason))

        supplier = suppliers.get(row["supplier_id"])
        if supplier is None:
            reject("Proveedor desconocido.")
            continue
        if row["currency"] != supplier["currency"]:
            reject("La moneda no coincide con la del proveedor.")
            continue
        try:
            month = date(int(row["month"][:4]), int(row["month"][5:7]), 1)
            if len(row["month"]) != 7 or row["month"][4] != "-":
                raise ValueError
        except (ValueError, IndexError):
            reject("Mes con formato distinto de AAAA-MM.")
            continue
        try:
            price = Decimal(row["unit_price"])
            volume = int(row["monthly_volume"])
        except (InvalidOperation, ValueError):
            reject("Precio o volumen no numérico.")
            continue
        if price <= 0 or volume < 0:
            reject("Precio no positivo o volumen negativo.")
            continue
        key = (row["supplier_id"], row["product"], month)
        if key in seen:
            reject("Mes duplicado para el mismo proveedor y producto.")
            continue
        seen.add(key)
        out.append({
            "supplier_id": row["supplier_id"], "product": row["product"], "unit": row["unit"],
            "currency": row["currency"], "month": month, "unit_price": price, "monthly_volume": volume,
            "source_file": path.name, "source_row": row_number,
        })
    return out, rejected


# --- Contratos ----------------------------------------------------------------


@dataclass
class Clause:
    number: str
    heading: str
    page: int
    text: str


@dataclass
class ExtractedTerm:
    term: str
    status: str
    confidence: float
    value: Optional[Dict[str, Any]] = None
    clause: Optional[str] = None
    clause_title: Optional[str] = None
    page: Optional[int] = None
    quote: Optional[str] = None
    note: Optional[str] = None


@dataclass
class ContractExtraction:
    contract_id: str
    supplier_id: Optional[str]
    source_file: str
    sha256: str
    pages: int
    terms: Dict[str, ExtractedTerm] = field(default_factory=dict)


_HEADING = re.compile(r"^(\d+)\.\s+([A-Z].*)$")
_CLAUSE = re.compile(r"^(\d+\.\d+)\s+(.*)$")
_FOOTER = re.compile(r"^CT-[A-Z]+-\d+ - page \d+ of \d+$")
_TITLE = re.compile(r"SUPPLY AGREEMENT\s+(CT-[A-Z]+-\d+)")
_PARTIES = re.compile(r"HealthCore Inc\. and (.+)")


def split_clauses(pages: List[str]) -> List[Clause]:
    clauses: List[Clause] = []
    heading = ""
    current: Optional[Clause] = None
    for page_number, text in enumerate(pages, start=1):
        for raw in (text or "").splitlines():
            line = raw.strip()
            if not line or _FOOTER.match(line):
                continue
            clause_match = _CLAUSE.match(line)
            heading_match = _HEADING.match(line) if not clause_match else None
            if clause_match:
                current = Clause(clause_match.group(1), heading, page_number, clause_match.group(2))
                clauses.append(current)
            elif heading_match:
                heading = line
                current = None
            elif current is not None:
                current.text = f"{current.text} {line}"
    return clauses


def _term(term: str, clause: Clause, value: Dict[str, Any], confidence: float, note: Optional[str] = None) -> ExtractedTerm:
    return ExtractedTerm(
        term=term,
        status=TERM_OK if confidence >= DOUBTFUL_CONFIDENCE else TERM_DOUBTFUL,
        confidence=confidence,
        value=value,
        clause=clause.number,
        clause_title=clause.heading,
        page=clause.page,
        quote=redact(clause.text)[:QUOTE_MAX],
        note=note,
    )


def _missing(term: str, note: str) -> ExtractedTerm:
    return ExtractedTerm(term=term, status=TERM_MISSING, confidence=0.0, note=note)


def _single(term: str, hits: List[Tuple[Clause, Dict[str, Any], float]], missing_note: str) -> ExtractedTerm:
    """Una sola coincidencia → su confianza. Varias con valores distintos →
    dudosa (el contrato se contradice o la regla es ambigua)."""
    if not hits:
        return _missing(term, missing_note)
    # Una mención de pasada ("before the Expiry Date") no contradice a la
    # cláusula que sí fija el término: si hay coincidencias exactas, mandan ellas.
    top = max(h[2] for h in hits)
    hits = [h for h in hits if h[2] == top]
    values ={repr(sorted(v.items())) for _, v, _ in hits}
    best = max(hits, key=lambda h: h[2])
    if len(values) > 1:
        return _term(term, best[0], best[1], min(best[2], 0.5), "Varias cláusulas dan valores distintos: revisar a mano.")
    return _term(term, best[0], best[1], best[2])


def _expiry(clause: Clause):
    match = re.search(r"expires on (\d{4}-\d{2}-\d{2})", clause.text)
    if match:
        return {"date": match.group(1)}, HIGH
    if re.search(r"(?i)\bexpir", clause.text):
        return {"date": None}, PARTIAL
    return None


def _notice(clause: Clause):
    match = re.search(r"non-renewal no later than (\d+) days before the Expiry Date", clause.text)
    if match:
        return {"days": int(match.group(1))}, HIGH
    if re.search(r"(?i)non-renewal", clause.text):
        return {"days": None}, PARTIAL
    return None


def _review(clause: Clause):
    text = clause.text
    annual = bool(re.search(r"(?i)once per (?:contract )?year", text))
    pct = re.search(r"shall not exceed (\d+(?:\.\d+)?)%", text)
    if pct:
        return {"type": REVIEW_CAP_PERCENT, "cap_pct": pct.group(1), "annual": annual}, HIGH
    if re.search(r"Consumer Price Index", text):
        return {"type": REVIEW_CAP_US_CPI, "cap_pct": None, "annual": annual}, HIGH
    if re.search(r"(?i)fixed for the entire term", text):
        return {"type": REVIEW_FIXED, "cap_pct": "0", "annual": False}, HIGH
    if re.search(r"(?i)price (?:review|increase)", text):
        return {"type": None, "cap_pct": None, "annual": annual}, PARTIAL
    return None


def _exit(clause: Clause):
    text = clause.text
    if not re.search(r"(?i)terminat", text):
        return None
    penalty = re.search(r"penalty equal to .*?\((\d+)\) months of average monthly billing", text)
    if penalty:
        return {"notice_days": None, "penalty_months": int(penalty.group(1)), "without_penalty": False}, HIGH
    notice = re.search(r"with (\d+) days' written notice, without penalty", text)
    if notice:
        return {"notice_days": int(notice.group(1)), "penalty_months": None, "without_penalty": True}, HIGH
    if re.search(r"at any time .*without penalty", text):
        return {"notice_days": 0, "penalty_months": None, "without_penalty": True}, HIGH
    return {"notice_days": None, "penalty_months": None, "without_penalty": None}, PARTIAL


def _protection(clause: Clause):
    text = clause.text
    if "Business Associate Agreement" in text:
        return {"agreement": AGREEMENT_BAA, "signed": bool(re.search(r"(?i)has executed|signed", text))}, HIGH
    if re.search(r"Data Processing Agreement|\bDPA\b", text):
        return {"agreement": AGREEMENT_DPA, "signed": bool(re.search(r"(?i)has executed|signed", text))}, HIGH
    if re.search(r"(?i)does not access or process patient", text):
        return {"agreement": AGREEMENT_NOT_APPLICABLE, "signed": None}, HIGH
    return None


def _value(clause: Clause):
    match = re.search(r"estimated annual contract value is (USD|GBP) ([\d,]+(?:\.\d+)?)", clause.text)
    if match:
        return {"currency": match.group(1), "amount": match.group(2).replace(",", "")}, HIGH
    return None


_EXTRACTORS: Dict[str, Tuple[Callable[[Clause], Any], str]] = {
    "expiry_date": (_expiry, "El contrato no indica fecha de vencimiento."),
    "non_renewal_notice": (_notice, "El contrato no indica plazo de preaviso de no renovación."),
    "price_review": (_review, "El contrato no tiene cláusula de revisión de precio."),
    "exit_terms": (_exit, "El contrato no tiene cláusula de salida."),
    "data_protection": (_protection, "El contrato no contiene ninguna cláusula de protección de datos (ni BAA ni DPA)."),
    "annual_value": (_value, "El contrato no indica su valor anual estimado."),
}


def extract_terms(clauses: List[Clause]) -> Dict[str, ExtractedTerm]:
    usable = [c for c in clauses if not EXCLUDED_CLAUSE_HEADINGS.search(c.heading)]
    out: Dict[str, ExtractedTerm] = {}
    for term, (extractor, missing_note) in _EXTRACTORS.items():
        hits = []
        for clause in usable:
            result = extractor(clause)
            if result is not None:
                hits.append((clause, result[0], result[1]))
        out[term] = _single(term, hits, missing_note)
    return out


def extract_contract(path: Path) -> ContractExtraction:
    import pdfplumber

    data = path.read_bytes()
    with pdfplumber.open(path) as pdf:
        pages = [page.extract_text() or "" for page in pdf.pages]
    first = pages[0] if pages else ""
    title = _TITLE.search(first)
    parties = _PARTIES.search(first)
    supplier = find_supplier(parties.group(1)) if parties else None
    return ContractExtraction(
        contract_id=title.group(1) if title else path.stem,
        supplier_id=supplier.supplier_id if supplier else None,
        source_file=path.name,
        sha256=hashlib.sha256(data).hexdigest(),
        pages=len(pages),
        terms=extract_terms(split_clauses(pages)),
    )


@dataclass
class Dataset:
    load_date: date
    suppliers: List[Dict[str, Any]]
    prices: List[Dict[str, Any]]
    contracts: List[ContractExtraction]
    rejected: List[Rejected]
    files: List[Dict[str, Any]]


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_dataset(directory: Path, load_date: date) -> Dataset:
    """Lee `suppliers.csv`, `price_history.csv` y `contracts/*.pdf`."""
    suppliers_path = directory / "suppliers.csv"
    prices_path = directory / "price_history.csv"
    suppliers, rejected = load_suppliers(suppliers_path)
    by_id = {s["supplier_id"]: s for s in suppliers}
    prices, price_rejected = load_prices(prices_path, by_id)
    rejected += price_rejected
    contracts = []
    files = [
        {"file": suppliers_path.name, "sha256": _sha(suppliers_path), "rows": len(suppliers)},
        {"file": prices_path.name, "sha256": _sha(prices_path), "rows": len(prices)},
    ]
    for pdf_path in sorted((directory / "contracts").glob("*.pdf")):
        extraction = extract_contract(pdf_path)
        if extraction.supplier_id is None or extraction.supplier_id not in by_id:
            rejected.append(Rejected(pdf_path.name, 0, "No se reconoce el proveedor del contrato."))
            continue
        contracts.append(extraction)
        files.append({"file": pdf_path.name, "sha256": extraction.sha256, "rows": extraction.pages})
    return Dataset(load_date, suppliers, prices, contracts, rejected, files)
