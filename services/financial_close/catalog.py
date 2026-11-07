"""El BRIEFING del cierre financiero mensual como datos (fuente única).

Todo lo que el BRIEFING fija vive aquí y solo aquí: partidas con su
presupuesto y su cifra real del mes de cierre, indicadores operativos, umbral
de desviación, tipo de cambio, calendario, revisor, destinatarios y la lista
cerrada de herramientas del agente. `seed_data` genera los CSV a partir de
estas cifras y los tests comprueban el informe contra ellas.

Las cifras reales (`actual`) solo las usa el generador de datos: el cierre
nunca las lee de aquí, las vuelve a sumar desde `ledger.csv`.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Dict, List, Optional, Tuple

# --- Monedas, países, umbral ------------------------------------------------------

US, UK = "US", "UK"
COUNTRIES = (US, UK)
COUNTRY_CURRENCY: Dict[str, str] = {US: "USD", UK: "GBP"}
COUNTRY_LABELS = {"en": {US: "United States", UK: "United Kingdom"}, "es": {US: "Estados Unidos", UK: "Reino Unido"}}

REPORTING_CURRENCY = "USD"
# BRIEFING, "Dato de referencia": tipo de cambio de consolidación.
FX_TO_USD: Dict[str, Decimal] = {"USD": Decimal("1"), "GBP": Decimal("1.27")}
FX_REFERENCE = "BRIEFING: 1 GBP = 1.27 USD"

# BRIEFING, "Umbral de desviación": igual o superior al 5 %, a favor o en contra.
DEVIATION_THRESHOLD_PCT = Decimal("5")

# --- Pagadores y categorías -------------------------------------------------------

NO_PAYER = "n/a"  # los gastos usan payer = n/a (BRIEFING, "Fuentes de datos")
COMMERCIAL = "commercial_insurance"
MEDICARE = "medicare_medicaid"
PRIVATE = "private"
NHS = "nhs_contract"
PAYERS = (COMMERCIAL, MEDICARE, PRIVATE, NHS)

PAYER_LABELS = {
    "en": {COMMERCIAL: "Commercial insurance", MEDICARE: "Medicare / Medicaid", PRIVATE: "Private",
           NHS: "NHS contract"},
    "es": {COMMERCIAL: "Seguros comerciales", MEDICARE: "Medicare / Medicaid", PRIVATE: "Privado",
           NHS: "Contrato NHS"},
}

REVENUE_CATEGORY = "patient_services"
SUPPLIES, LAB, INTERPRETING = "clinical_supplies", "laboratory", "medical_interpreting"
PAYROLL, RENT, SOFTWARE = "payroll", "clinic_rent", "clinical_software_licences"
EXPENSE_CATEGORIES = (SUPPLIES, LAB, INTERPRETING, PAYROLL, RENT, SOFTWARE)

CATEGORY_LABELS = {
    "en": {SUPPLIES: "Clinical supplies", LAB: "Laboratory", INTERPRETING: "Medical interpreting",
           PAYROLL: "Payroll", RENT: "Clinic rent", SOFTWARE: "Clinical software licences",
           REVENUE_CATEGORY: "Patient services"},
    "es": {SUPPLIES: "Material clínico", LAB: "Laboratorio", INTERPRETING: "Interpretación médica",
           PAYROLL: "Nómina", RENT: "Alquiler de clínicas", SOFTWARE: "Licencias de software clínico",
           REVENUE_CATEGORY: "Servicios a pacientes"},
}

# --- Partidas del mes de cierre (BRIEFING, "Presupuesto y cifras reales") ---------


@dataclass(frozen=True)
class LineSpec:
    country: str
    payer: str
    category: str
    budget: Decimal
    actual: Optional[Decimal]  # None = "Sin registros" en el BRIEFING
    supplier: Optional[str] = None
    unit_metric: Optional[str] = None  # indicador de unidades en operations.csv
    unit_price: Optional[Decimal] = None  # precio unitario real (descripciones del ledger)
    budget_unit_price: Optional[Decimal] = None

    @property
    def kind(self) -> str:
        return "revenue" if self.category == REVENUE_CATEGORY else "expense"

    @property
    def currency(self) -> str:
        return COUNTRY_CURRENCY[self.country]

    @property
    def key(self) -> str:
        return line_key(self.country, self.payer, self.category)


def line_key(country: str, payer: str, category: str) -> str:
    return f"{country}.{payer}.{category}"


def D(value: str) -> Decimal:
    return Decimal(value)


LINES: Tuple[LineSpec, ...] = (
    # Ingresos — EE. UU. (USD)
    LineSpec(US, COMMERCIAL, REVENUE_CATEGORY, D("1150000"), D("1120000")),
    LineSpec(US, MEDICARE, REVENUE_CATEGORY, D("520000"), D("468000")),
    # Ingresos — R. U. (GBP)
    LineSpec(UK, PRIVATE, REVENUE_CATEGORY, D("260000"), D("271000")),
    LineSpec(UK, NHS, REVENUE_CATEGORY, D("75000"), D("75000")),
    # Gastos — EE. UU. (USD). Unidades y precios elegidos para que
    # cantidad × precio cuadre EXACTO con el presupuesto y el real del BRIEFING.
    LineSpec(US, NO_PAYER, SUPPLIES, D("44800"), D("46816"), "MedSupply Partners", "clinical_supply_units",
             D("14.00"), D("14.00")),  # 3.200 → 3.344 unidades
    LineSpec(US, NO_PAYER, LAB, D("45000"), D("48150"), "ClearPath Labs", "laboratory_tests",
             D("32.10"), D("30.00")),  # 1.500 pruebas en ambos casos
    LineSpec(US, NO_PAYER, INTERPRETING, D("17550"), D("17550"), "LinguaCare Interpreting", "interpreting_hours",
             D("45.00"), D("45.00")),  # 390 horas
    LineSpec(US, NO_PAYER, PAYROLL, D("1050000"), D("1060000")),
    LineSpec(US, NO_PAYER, RENT, D("180000"), D("180000")),
    # Gastos — R. U. (GBP)
    LineSpec(UK, NO_PAYER, SUPPLIES, D("9450"), D("9450"), "BritMed Supplies", "clinical_supply_units",
             D("9.00"), D("9.00")),  # 1.050 unidades
    LineSpec(UK, NO_PAYER, LAB, D("10150"), D("10353"), "Thames Pathology", "laboratory_tests",
             D("29.00"), D("29.00")),  # 350 → 357 pruebas
    LineSpec(UK, NO_PAYER, PAYROLL, D("210000"), D("209000")),
    LineSpec(UK, NO_PAYER, RENT, D("52000"), D("52000")),
    LineSpec(UK, NO_PAYER, SOFTWARE, D("8000"), None),  # "Sin registros"
)
LINES_BY_KEY: Dict[str, LineSpec] = {line.key: line for line in LINES}

# --- Indicadores operativos (operations.csv) --------------------------------------
# El BRIEFING da "Presupuesto" y "Real" por indicador, pero operations.csv solo
# tiene una columna `value`: el presupuesto va como el mismo indicador con el
# sufijo `_budget` (mismo mes, país y pagador).
BUDGET_SUFFIX = "_budget"
CLAIMS_SUBMITTED = "claims_submitted"
DENIAL_RATE = "denial_rate_pct"

OPERATIONS: Tuple[Tuple[str, str, str, Decimal, Decimal], ...] = (
    # (país, pagador, indicador, presupuesto, real)
    (US, MEDICARE, CLAIMS_SUBMITTED, D("4000"), D("4000")),
    (US, MEDICARE, DENIAL_RATE, D("14.0"), D("22.6")),
    (US, COMMERCIAL, CLAIMS_SUBMITTED, D("6500"), D("6400")),
    (US, COMMERCIAL, DENIAL_RATE, D("12.0"), D("12.0")),
    # Unidades de proveedores: base de las hipótesis de precio frente a volumen.
    (US, NO_PAYER, "clinical_supply_units", D("3200"), D("3344")),
    (US, NO_PAYER, "laboratory_tests", D("1500"), D("1500")),
    (US, NO_PAYER, "interpreting_hours", D("390"), D("390")),
    (UK, NO_PAYER, "clinical_supply_units", D("1050"), D("1050")),
    (UK, NO_PAYER, "laboratory_tests", D("350"), D("357")),
)

METRIC_LABELS = {
    "en": {CLAIMS_SUBMITTED: "Claims submitted", DENIAL_RATE: "Denial rate (%)", "paid_claims": "Paid claims",
           "clinical_supply_units": "Supply units", "laboratory_tests": "Laboratory tests",
           "interpreting_hours": "Interpreting hours", "unit_price": "Unit price",
           "claims_change_pct": "Change in claims submitted", "denial_rate_change_pp": "Change in denial rate",
           "paid_claims_change_pct": "Change in paid claims", "units": "Units", "units_change_pct": "Change in units",
           "unit_price_change_pct": "Change in unit price", "budget": "Budget", "actual": "Actual",
           "variance_pct": "Variance"},
    "es": {CLAIMS_SUBMITTED: "Reclamaciones presentadas", DENIAL_RATE: "Tasa de denegación (%)",
           "paid_claims": "Reclamaciones pagadas", "clinical_supply_units": "Unidades de material",
           "laboratory_tests": "Pruebas de laboratorio", "interpreting_hours": "Horas de interpretación",
           "unit_price": "Precio unitario", "claims_change_pct": "Variación de reclamaciones presentadas",
           "denial_rate_change_pp": "Variación de la tasa de denegación",
           "paid_claims_change_pct": "Variación de reclamaciones pagadas", "units": "Unidades",
           "units_change_pct": "Variación de unidades", "unit_price_change_pct": "Variación del precio unitario",
           "budget": "Presupuesto", "actual": "Real", "variance_pct": "Desviación"},
}

# --- Clínicas (ingresos agregados por clínica y pagador, nunca por paciente) -------
CLINICS: Dict[str, Tuple[str, ...]] = {
    US: ("AUS-01", "HOU-01", "DAL-01", "MIA-01", "ORL-01", "TPA-01", "ATL-01", "ATL-02", "SAV-01"),
    UK: ("LON-01", "LON-02", "MAN-01"),
}
# Contrato NHS solo en las clínicas que lo tienen (supuesto del generador).
NHS_CLINICS = ("LON-02", "MAN-01")

# --- Personas, revisión y distribución ----------------------------------------------
ROLE_CEO = "ceo"
ROLE_CTO = "cto"
ROLE_CLINICAL_OPS = "clinical_ops_director"
ROLE_PATIENT_COORDINATION = "patient_coordination_lead"
ROLE_REVENUE_CYCLE = "revenue_cycle_director"
ROLE_COMPLIANCE = "compliance_officer"
ROLE_PEOPLE = "people_director"

PEOPLE: Dict[str, str] = {
    ROLE_CEO: "Dr. Sandra Okonkwo",
    ROLE_CTO: "James Osei",
    ROLE_CLINICAL_OPS: "Dr. Marcus Reid",
    ROLE_PATIENT_COORDINATION: "Priya Nair",
    ROLE_REVENUE_CYCLE: "Tom Callahan",
    ROLE_COMPLIANCE: "Claire Whitfield",
    ROLE_PEOPLE: "Diane Foster",
}
ORG_ROLES = tuple(PEOPLE)

# BRIEFING, "Revisión y distribución".
REVIEWER_ROLE = ROLE_REVENUE_CYCLE
DISTRIBUTION_ROLES: Tuple[str, ...] = (
    ROLE_CEO, ROLE_CTO, ROLE_CLINICAL_OPS, ROLE_PATIENT_COORDINATION, ROLE_REVENUE_CYCLE, ROLE_COMPLIANCE, ROLE_PEOPLE,
)
# BRIEFING, "Restricción específica": Cumplimiento revisa los registros con datos de pacientes.
PATIENT_DATA_REVIEWER_ROLE = ROLE_COMPLIANCE

# Ejecución: tercer día hábil del mes, 06:00 hora de Austin.
SCHEDULE_TIMEZONE = "America/Chicago"
SCHEDULE_BUSINESS_DAY = 3
SCHEDULE_HOUR = 6

# --- Identidad del agente y herramientas --------------------------------------------
# README: "El agente opera con una identidad propia". El prefijo `process:`
# distingue en el registro una acción automatizada de una persona (`human:`).
AGENT_ID = "agent:financial-close"
AGENT_ACTOR = f"process:{AGENT_ID}"

AGENT_TOOLS: Tuple[str, ...] = (
    "read_period_data",  # lectura (solo lectura) de ledger/budget/operations ya ingeridos
    "compute_line_totals",
    "compare_to_budget",
    "convert_to_usd",
    "consolidate_usd",
    "find_relevant_deviations",
    "payer_claims_drivers",
    "unit_cost_drivers",
    "data_quality_report",
)
# Las herramientas de "explicación" entre las que elige el modelo.
DRIVER_TOOLS = ("payer_claims_drivers", "unit_cost_drivers")

REPORT_LANGUAGES = ("en", "es")
BUDGET_LABEL = {"en": "budget", "es": "presupuesto"}


def metric_label(name: str, lang: str) -> str:
    """Etiqueta legible de un hecho; `<x>_budget` → "<X> (budget)"."""
    if name.endswith(BUDGET_SUFFIX) and name != BUDGET_SUFFIX.strip("_"):
        base = name[: -len(BUDGET_SUFFIX)]
        return f"{METRIC_LABELS[lang].get(base, base)} ({BUDGET_LABEL[lang]})"
    return METRIC_LABELS[lang].get(name, name)


def revenue_lines() -> List[LineSpec]:
    return [line for line in LINES if line.kind == "revenue"]


def expense_lines() -> List[LineSpec]:
    return [line for line in LINES if line.kind == "expense"]


def line_label(key: str, lang: str) -> str:
    country, payer, category = key.split(".")
    if category == REVENUE_CATEGORY:
        return f"{PAYER_LABELS[lang].get(payer, payer)} ({country})"
    spec = LINES_BY_KEY.get(key)
    label = CATEGORY_LABELS[lang].get(category, category)
    supplier = f" — {spec.supplier}" if spec and spec.supplier else ""
    return f"{label}{supplier} ({country})"
