"""Genera los datos de prueba del BRIEFING a partir de una fecha de carga.

- `suppliers.csv`: registro de proveedores (BRIEFING, "Proveedores").
- `price_history.csv`: 12 meses con las columnas literales del BRIEFING. Los
  11 primeros derivan como máximo ±1 % mensual (semilla fija: siempre salen
  iguales); el último aplica los escenarios A-D. Precios con 4 decimales, para
  que +7 % sea +7,00 % y no +6,98 % por redondear a céntimos.
- Un contrato PDF por fila de la tabla "Contratos", en inglés, con cláusulas
  numeradas en 3 páginas y la cláusula de facturación con datos bancarios y de
  contacto FICTICIOS (para probar que no se propagan).

Thames Pathology no tiene cláusula de protección de datos: el BRIEFING dice
"el contrato no incluye DPA". Tiene una de confidencialidad genérica a
propósito, para comprobar que la extracción no la confunde con un DPA.
"""

from __future__ import annotations

import csv
import random
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Dict, List, Tuple

from services.supplier_contracts.catalog import (
    AGREEMENT_BAA,
    AGREEMENT_NOT_APPLICABLE,
    CONTRACTS,
    HISTORY_MONTHS,
    REVIEW_CAP_PERCENT,
    REVIEW_CAP_US_CPI,
    REVIEW_FIXED,
    SENSITIVE_BILLING,
    SUPPLIERS,
    SUPPLIERS_BY_ID,
    ContractSpec,
)

PRICE_COLUMNS = ["supplier_id", "product", "unit", "currency", "month", "unit_price", "monthly_volume"]
SUPPLIER_COLUMNS = ["supplier_id", "name", "country", "category", "currency", "processes_patient_data"]
PRICE_STEP = Decimal("0.0001")
MAX_DRIFT = 0.009  # < 1 %: margen para que el redondeo nunca lo supere


def month_starts(load_date: date, months: int = HISTORY_MONTHS) -> List[date]:
    """Primeros de mes de los `months` meses que terminan en el mes de carga."""
    year, month = load_date.year, load_date.month
    out = []
    for _ in range(months):
        out.append(date(year, month, 1))
        month -= 1
        if month == 0:
            year, month = year - 1, 12
    return list(reversed(out))


def build_price_rows(load_date: date, seed: int = 20261107) -> List[Dict[str, str]]:
    rows: List[Dict[str, str]] = []
    months = month_starts(load_date)
    for spec in SUPPLIERS:
        rng = random.Random(f"{seed}-{spec.supplier_id}")
        price = spec.base_price
        for index, month in enumerate(months):
            if index > 0:
                if index == len(months) - 1 and spec.last_month_change is not None:
                    factor = Decimal(1) + spec.last_month_change
                elif spec.fixed_price:
                    factor = Decimal(1)
                else:
                    factor = Decimal(1) + Decimal(str(round(rng.uniform(-MAX_DRIFT, MAX_DRIFT), 5)))
                price = (price * factor).quantize(PRICE_STEP, rounding=ROUND_HALF_UP)
            rows.append({
                "supplier_id": spec.supplier_id,
                "product": spec.product,
                "unit": spec.unit,
                "currency": spec.currency,
                "month": month.strftime("%Y-%m"),
                "unit_price": f"{price:.4f}",
                "monthly_volume": str(spec.monthly_volume),
            })
    return rows


def write_csv(path: Path, columns: List[str], rows: List[Dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)


def supplier_rows() -> List[Dict[str, str]]:
    return [
        {
            "supplier_id": s.supplier_id,
            "name": s.name,
            "country": s.country,
            "category": s.category,
            "currency": s.currency,
            "processes_patient_data": "yes" if s.processes_patient_data else "no",
        }
        for s in SUPPLIERS
    ]


# --- Contratos en PDF ------------------------------------------------------------


def _money_text(currency: str, amount: Decimal) -> str:
    return f"{currency} {amount:,.0f}"


def contract_clauses(spec: ContractSpec, load_date: date) -> List[List[Tuple[str, List[Tuple[str, str]]]]]:
    """Cláusulas por página: [[(título de sección, [(número, texto), ...]), ...], ...]."""
    supplier = SUPPLIERS_BY_ID[spec.supplier_id]
    expiry = load_date + timedelta(days=spec.expiry_in_days)
    effective = expiry - timedelta(days=365)
    billing = SENSITIVE_BILLING[spec.contract_id]

    if spec.review_type == REVIEW_CAP_PERCENT:
        review = (f"Prices may be reviewed once per contract year. Any price increase shall not exceed "
                  f"{spec.review_cap_pct}% ({_words(spec.review_cap_pct)} percent) of the price in force.")
    elif spec.review_type == REVIEW_CAP_US_CPI:
        review = ("Prices may be reviewed once per contract year. Any price increase shall not exceed the annual "
                  "change in the United States Consumer Price Index (CPI-U) published for the preceding year.")
    else:
        review = "Prices are fixed for the entire term of this Agreement and shall not be increased."

    if spec.penalty_months:
        exit_text = (f"HealthCore may terminate this Agreement before the Expiry Date by paying an early termination "
                     f"penalty equal to {_words(spec.penalty_months)} ({spec.penalty_months}) months of average "
                     f"monthly billing.")
    elif spec.exit_notice_days:
        exit_text = (f"HealthCore may terminate this Agreement for convenience with {spec.exit_notice_days} days' "
                     f"written notice, without penalty.")
    else:
        exit_text = "HealthCore may terminate this Agreement at any time by written notice, without penalty."

    if spec.agreement == AGREEMENT_BAA:
        protection = ("6. Data Protection", [
            ("6.1", "The Supplier has executed a Business Associate Agreement (BAA) under HIPAA with HealthCore, "
                    "attached to this Agreement as Annex 1."),
            ("6.2", "The Supplier shall apply the minimum necessary standard to any patient information."),
        ])
    elif spec.agreement == AGREEMENT_NOT_APPLICABLE:
        protection = ("6. Data Protection", [
            ("6.1", "The Supplier does not access or process patient health information under this Agreement."),
        ])
    else:  # el contrato no incluye DPA
        protection = ("6. Confidentiality", [
            ("6.1", "Each party shall keep the commercial terms of this Agreement confidential."),
        ])

    if supplier.country == "US":
        bank_lines = (f"Bank: {billing['bank']}; Account number: {billing['account']}; "
                      f"Routing number: {billing['routing']}.")
    else:
        bank_lines = (f"Bank: {billing['bank']}; Account number: {billing['account']}; "
                      f"Sort code: {billing['sort_code']}; IBAN: {billing['iban']}.")

    page1 = [
        ("1. Parties and Scope", [
            ("1.1", f"This Agreement is made between HealthCore Inc. and {supplier.name} (the \"Supplier\") for the "
                    f"supply of {supplier.product.lower()} services to HealthCore clinics."),
            ("1.2", f"Unit prices are expressed in {supplier.currency} per {supplier.unit} and are listed in Schedule A."),
        ]),
        ("2. Term and Expiry", [
            ("2.1", f"This Agreement takes effect on {effective.isoformat()} and expires on {expiry.isoformat()} "
                    f"(the \"Expiry Date\")."),
        ]),
        ("3. Renewal and Notice", [
            ("3.1", f"This Agreement renews automatically for successive twelve-month periods unless either party "
                    f"gives written notice of non-renewal no later than {spec.non_renewal_notice_days} days before "
                    f"the Expiry Date."),
        ]),
    ]
    page2 = [
        ("4. Pricing and Price Review", [
            ("4.1", "Invoiced prices shall match Schedule A in force at the date of service."),
            ("4.2", review),
        ]),
        ("5. Termination and Exit", [("5.1", exit_text)]),
        protection,
        ("7. Contract Value", [
            ("7.1", f"The estimated annual contract value is {_money_text(spec.currency, spec.annual_value)}."),
        ]),
    ]
    page3 = [
        ("8. Invoicing and Payment", [
            ("8.1", "The Supplier shall invoice monthly in arrears. Payment terms are 30 days from invoice."),
            ("8.2", f"Payments shall be made to: {bank_lines}"),
            ("8.3", f"Contact person: {billing['contact']}; email {billing['email']}; phone {billing['phone']}."),
        ]),
        ("9. Governing Law", [
            ("9.1", "This Agreement is governed by the laws of the State of Texas."
             if supplier.country == "US" else "This Agreement is governed by the laws of England and Wales."),
        ]),
    ]
    return [page1, page2, page3]


_NUMBER_WORDS = {1: "one", 2: "two", 3: "three", 4: "four", 5: "five", 6: "six"}


def _words(value) -> str:
    return _NUMBER_WORDS.get(int(value), str(value))


def write_contract_pdf(path: Path, spec: ContractSpec, load_date: date) -> None:
    from fpdf import FPDF

    supplier = SUPPLIERS_BY_ID[spec.supplier_id]

    class _ContractPDF(FPDF):
        def footer(self) -> None:  # fpdf2 lo llama al cerrar cada página
            self.set_y(-12)
            self.set_font("Helvetica", "I", 8)
            self.cell(0, 6, f"{spec.contract_id} - page {self.page_no()} of 3", align="C")

    pdf = _ContractPDF(format="A4")
    pdf.set_auto_page_break(auto=True, margin=18)
    pdf.set_title(f"{spec.contract_id} {supplier.name}")
    for page_index, sections in enumerate(contract_clauses(spec, load_date), start=1):
        pdf.add_page()
        if page_index == 1:
            pdf.set_font("Helvetica", "B", 14)
            pdf.multi_cell(0, 8, f"SUPPLY AGREEMENT {spec.contract_id}", new_x="LMARGIN", new_y="NEXT")
            pdf.set_font("Helvetica", "", 10)
            pdf.multi_cell(0, 6, f"HealthCore Inc. and {supplier.name}", new_x="LMARGIN", new_y="NEXT")
            pdf.ln(4)
        for heading, clauses in sections:
            pdf.set_font("Helvetica", "B", 11)
            pdf.multi_cell(0, 7, heading, new_x="LMARGIN", new_y="NEXT")
            pdf.set_font("Helvetica", "", 10)
            for number, text in clauses:
                pdf.multi_cell(0, 5.5, f"{number} {text}", new_x="LMARGIN", new_y="NEXT")
                pdf.ln(1.5)
            pdf.ln(2)
    path.parent.mkdir(parents=True, exist_ok=True)
    pdf.output(str(path))


def generate_all(out_dir: Path, load_date: date) -> Dict[str, Path]:
    """Escribe el registro, el histórico y los contratos. Devuelve sus rutas."""
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = {"suppliers": out_dir / "suppliers.csv", "prices": out_dir / "price_history.csv"}
    write_csv(paths["suppliers"], SUPPLIER_COLUMNS, supplier_rows())
    write_csv(paths["prices"], PRICE_COLUMNS, build_price_rows(load_date))
    for spec in CONTRACTS:
        target = out_dir / "contracts" / f"{spec.contract_id}.pdf"
        write_contract_pdf(target, spec, load_date)
        paths[spec.contract_id] = target
    return paths
