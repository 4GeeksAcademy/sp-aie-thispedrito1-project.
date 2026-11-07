"""El BRIEFING de "Gestión de Proveedores y Contratos" como datos.

Fuente única de proveedores, contratos, umbrales, plazos de alerta,
aprobadores, datos de referencia (IPC, tipo de cambio) e identidades de los
agentes. Ningún otro módulo repite estas cifras: las importa de aquí, y los
tests comprueban que coinciden con la tabla del BRIEFING.

Las fechas de los contratos son RELATIVAS a la fecha de carga (BRIEFING,
"Contratos"): aquí se guardan como días; `seed_data.py` las convierte en
fechas concretas al generar los PDF.

`SENSITIVE_BILLING` son datos bancarios y de contacto FICTICIOS que el
BRIEFING obliga a meter en la cláusula de facturación de cada contrato "para
poder comprobar que el sistema no los propaga". Solo los usan el generador de
PDF y los tests que demuestran que no aparecen en ninguna salida.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Dict, FrozenSet, Optional, Tuple

# --- Datos de referencia (BRIEFING, "Datos de referencia") -----------------

US_CPI_ANNUAL_PCT = Decimal("2.9")
GBP_TO_USD = Decimal("1.27")
REFERENCE_REF = {"source": "BRIEFING", "locator": "Datos de referencia"}

# --- Umbrales y alertas (BRIEFING, "Umbrales y alertas") -------------------

PRICE_INCREASE_THRESHOLD_PCT = Decimal("4")  # "Más de un 4 % frente al mes anterior"
EXPIRY_ALERT_DAYS: Tuple[int, ...] = (90, 30)
NOTICE_DEADLINE_ALERT_DAYS = 15

# Aprobaciones (BRIEFING, "Aprobaciones")
HIGH_VALUE_CONTRACT_USD = Decimal("500000")

HISTORY_MONTHS = 12

# Extracción: por debajo de esta confianza el término se marca como dudoso.
DOUBTFUL_CONFIDENCE = 0.8

# --- Personas ------------------------------------------------------------------

ROLE_CLINICAL_OPS = "clinical_ops_director"
ROLE_COMPLIANCE = "compliance_officer"
ROLE_CEO = "ceo"
ORG_ROLES = (ROLE_CLINICAL_OPS, ROLE_COMPLIANCE, ROLE_CEO)

PEOPLE: Dict[str, str] = {
    ROLE_CLINICAL_OPS: "Dr. Marcus Reid",
    ROLE_COMPLIANCE: "Claire Whitfield",
    ROLE_CEO: "Dra. Sandra Okonkwo",
}

# --- Proveedores (BRIEFING, "Proveedores") -------------------------------------

CATEGORY_CLINICAL_SUPPLIES = "clinical_supplies"
CATEGORY_LABORATORY = "laboratory"
CATEGORY_INTERPRETING = "medical_interpreting"

CATEGORY_LABELS = {
    CATEGORY_CLINICAL_SUPPLIES: "Material clínico",
    CATEGORY_LABORATORY: "Laboratorio",
    CATEGORY_INTERPRETING: "Interpretación médica",
}


@dataclass(frozen=True)
class SupplierSpec:
    supplier_id: str
    name: str
    country: str  # "US" | "UK"
    category: str
    currency: str  # "USD" | "GBP"
    processes_patient_data: bool
    product: str
    unit: str
    base_price: Decimal
    monthly_volume: int
    # Variación del último mes frente al anterior (BRIEFING, escenarios A-D).
    # None = deriva normal (como máximo ±1 %), como el resto de meses.
    last_month_change: Optional[Decimal] = None
    # LinguaCare tiene precio fijo por contrato: su histórico no varía.
    fixed_price: bool = False


SUPPLIERS: Tuple[SupplierSpec, ...] = (
    SupplierSpec("HC-S01", "MedSupply Partners", "US", CATEGORY_CLINICAL_SUPPLIES, "USD", False,
                 "Disposable consultation kit", "kit", Decimal("3.20"), 14000, last_month_change=Decimal("0.045")),
    SupplierSpec("HC-S02", "CareSource Medical", "US", CATEGORY_CLINICAL_SUPPLIES, "USD", False,
                 "Disposable consultation kit", "kit", Decimal("3.05"), 0),
    SupplierSpec("HC-S03", "ClearPath Labs", "US", CATEGORY_LABORATORY, "USD", True,
                 "Basic metabolic panel", "test", Decimal("18.00"), 2500, last_month_change=Decimal("0.07")),
    SupplierSpec("HC-S04", "Lone Star Diagnostics", "US", CATEGORY_LABORATORY, "USD", True,
                 "Basic metabolic panel", "test", Decimal("17.20"), 0),
    SupplierSpec("HC-S05", "BritMed Supplies", "UK", CATEGORY_CLINICAL_SUPPLIES, "GBP", False,
                 "Disposable consultation kit", "kit", Decimal("2.70"), 3500),
    SupplierSpec("HC-S06", "Thames Pathology", "UK", CATEGORY_LABORATORY, "GBP", True,
                 "Basic metabolic panel", "test", Decimal("14.50"), 700, last_month_change=Decimal("0.02")),
    SupplierSpec("HC-S07", "LinguaCare Interpreting", "US", CATEGORY_INTERPRETING, "USD", True,
                 "Medical interpreting", "minute", Decimal("1.95"), 9000,
                 last_month_change=Decimal("0"), fixed_price=True),
)

SUPPLIERS_BY_ID: Dict[str, SupplierSpec] = {s.supplier_id: s for s in SUPPLIERS}

# --- Contratos (BRIEFING, "Contratos") -------------------------------------------

REVIEW_CAP_PERCENT = "cap_percent"
REVIEW_CAP_US_CPI = "cap_us_cpi"
REVIEW_FIXED = "fixed"

AGREEMENT_BAA = "BAA"
AGREEMENT_DPA = "DPA"
AGREEMENT_NOT_APPLICABLE = "not_applicable"
AGREEMENT_MISSING = "missing"


@dataclass(frozen=True)
class ContractSpec:
    contract_id: str
    supplier_id: str
    expiry_in_days: int
    non_renewal_notice_days: int
    review_type: str
    review_cap_pct: Optional[Decimal]
    exit_notice_days: Optional[int]
    penalty_months: Optional[int]
    agreement: str  # BAA | not_applicable | missing (Thames: "el contrato no incluye DPA")
    annual_value: Decimal
    currency: str


CONTRACTS: Tuple[ContractSpec, ...] = (
    ContractSpec("CT-HC-01", "HC-S01", 180, 60, REVIEW_CAP_PERCENT, Decimal("5"), 90, None,
                 AGREEMENT_NOT_APPLICABLE, Decimal("537600"), "USD"),
    ContractSpec("CT-HC-02", "HC-S03", 60, 45, REVIEW_CAP_US_CPI, None, None, 2,
                 AGREEMENT_BAA, Decimal("540000"), "USD"),
    ContractSpec("CT-HC-03", "HC-S06", 210, 90, REVIEW_CAP_PERCENT, Decimal("3"), 90, None,
                 AGREEMENT_MISSING, Decimal("121800"), "GBP"),
    ContractSpec("CT-HC-04", "HC-S07", 28, 15, REVIEW_FIXED, None, None, None,
                 AGREEMENT_BAA, Decimal("210600"), "USD"),
)

CONTRACTS_BY_SUPPLIER: Dict[str, ContractSpec] = {c.supplier_id: c for c in CONTRACTS}


def required_agreement(supplier: SupplierSpec) -> Optional[str]:
    """BAA en EE. UU., DPA en Reino Unido; None si no procesa datos de pacientes."""
    if not supplier.processes_patient_data:
        return None
    return AGREEMENT_BAA if supplier.country == "US" else AGREEMENT_DPA


# --- Datos sensibles FICTICIOS de la cláusula de facturación --------------------
# Nunca deben salir de los PDF (BRIEFING, "Datos sensibles").

SENSITIVE_BILLING: Dict[str, Dict[str, str]] = {
    "CT-HC-01": {"bank": "First Lone Star Bank", "account": "4410-2297-1186", "routing": "111904215",
                 "contact": "Rebecca Tolliver", "email": "r.tolliver@medsupply-partners.example",
                 "phone": "+1 512 555 0142"},
    "CT-HC-02": {"bank": "Austin Capital Trust", "account": "7781-0034-5529", "routing": "114924742",
                 "contact": "Gregory Halvorsen", "email": "g.halvorsen@clearpath-labs.example",
                 "phone": "+1 737 555 0198"},
    "CT-HC-03": {"bank": "Thames Mercantile Bank", "account": "31926819", "sort_code": "40-11-62",
                 "iban": "GB29TMBK40116231926819", "contact": "Imogen Ashcroft",
                 "email": "i.ashcroft@thames-pathology.example", "phone": "+44 20 7946 0331"},
    "CT-HC-04": {"bank": "Gulf Coast Federal", "account": "5502-8813-0047", "routing": "113122655",
                 "contact": "Mariana Delgado", "email": "m.delgado@linguacare.example",
                 "phone": "+1 832 555 0177"},
}

# --- Agentes: identidad propia y herramientas de su especialidad ---------------

AGENT_SUPERVISOR = "agent:supplier-supervisor"
AGENT_PRICING = "agent:pricing-specialist"
AGENT_CONTRACTS = "agent:contracts-specialist"

AGENT_TOOLS: Dict[str, FrozenSet[str]] = {
    AGENT_SUPERVISOR: frozenset({
        "consult_pricing_specialist",
        "consult_contracts_specialist",
        "build_renegotiation_brief",
        "draft_supplier_communication",
        "raise_alert",
        # Cruza un término del contrato (meses de penalización) con el
        # histórico de precios: solo el supervisor ve ambos resultados.
        "compute_exit_cost",
    }),
    AGENT_PRICING: frozenset({
        "get_price_history",
        "detect_price_increases",
        "compute_increase_impact",
        "compare_same_category_prices",
        "convert_currency",
    }),
    AGENT_CONTRACTS: frozenset({
        "get_contract_terms",
        "evaluate_price_review_clause",
        "compute_contract_deadlines",
        "check_data_protection_agreement",
        "lookup_clause",
    }),
}

# Límite de consultas a especialistas por petición (evita bucles). Un brief
# completo necesita 7; una pregunta suelta, 1-3.
MAX_CONSULTATIONS_PER_REQUEST = 8


def find_supplier(text: str) -> Optional[SupplierSpec]:
    """Proveedor mencionado en un texto por id, nombre completo o primera palabra
    distintiva ("ClearPath", "Thames"). Determinista: el primero que aparece."""
    lowered = text.lower()
    hits = []
    for spec in SUPPLIERS:
        keys = {spec.supplier_id.lower(), spec.name.lower(), spec.name.split()[0].lower()}
        positions = [lowered.find(k) for k in keys if k in lowered]
        if positions:
            hits.append((min(positions), spec))
    return min(hits, key=lambda h: h[0])[1] if hits else None
