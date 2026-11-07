"""Catálogo del onboarding: la tabla del BRIEFING convertida en datos.

Es la única fuente de las etapas, plazos, aprobadores, departamentos, puestos
con material propio y sistemas. El grafo (graph.py) genera sus nodos a partir
de `STAGES`, la API valida contra estas listas y las pantallas muestran los
mismos títulos. Si el BRIEFING cambia, se cambia aquí y en nada más.

Correspondencia literal con el BRIEFING (tabla "Etapas del onboarding"):

| # | Etapa                 | Salida                                   | Plazo  | Aprueba             |
| 1 | Bienvenida y trámites | Personas marca documentación + verif.    | Día 2  | Equipo de Personas  |
| 2 | La empresa            | Comprobación superada                    | Día 3  | Automática          |
| 3 | Cómo operamos         | Comprobación superada                    | Día 4  | Automática          |
| 4 | Cumplimiento          | Comprobación (100 %) + Cumplimiento      | Día 6  | Claire Whitfield    |
| 5 | Tu función            | Comprobación + responsable directo       | Día 8  | Responsable directo |
| 6 | Cómo trabajamos       | Comprobación superada                    | Día 9  | Automática          |
| 7 | Accesos y cierre      | Diane aprueba el cierre                  | Día 10 | Diane Foster        |
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional, Tuple

# --- Tipos de etapa ---------------------------------------------------------

KIND_ADMIN = "admin"  # trámites: la marca Personas, sin comprobación
KIND_CONTENT = "content"  # material oficial + comprobación de comprensión
KIND_CLOSURE = "closure"  # accesos + aprobación final de Diane

# --- Reglas de aprobación (quién puede cerrar una etapa) --------------------

APPROVER_PEOPLE_TEAM = "people_team"  # etapa 1: equipo de Personas
APPROVER_COMPLIANCE = "compliance"  # etapa 4: Claire Whitfield
APPROVER_LINE_MANAGER = "line_manager"  # etapa 5: SU responsable directo
APPROVER_VP_PEOPLE = "vp_people"  # etapa 7: Diane Foster

# Umbrales de las comprobaciones (BRIEFING, "Comprobaciones de comprensión").
DEFAULT_PASS_THRESHOLD = 0.8
COMPLIANCE_PASS_THRESHOLD = 1.0
# Tras este número de intentos fallidos se avisa (a Claire en la etapa 4, al
# responsable directo en el resto). El flujo NO se salta la etapa: se sigue
# repitiendo la comprobación.
FAILED_ATTEMPTS_BEFORE_NOTICE = 2


@dataclass(frozen=True)
class Stage:
    number: int
    key: str
    kind: str
    title_en: str
    title_es: str
    deadline_day: int
    approver: Optional[str] = None
    pass_threshold: Optional[float] = None
    # Etapa 4: "no escala por plazo"; si se retrasa, se avisa a Diane.
    escalates_when_overdue: bool = True

    @property
    def needs_check(self) -> bool:
        return self.kind == KIND_CONTENT

    def title(self, language: str) -> str:
        return self.title_es if language == "es" else self.title_en


STAGES: Tuple[Stage, ...] = (
    Stage(1, "welcome_paperwork", KIND_ADMIN, "Welcome and paperwork", "Bienvenida y trámites", 2, APPROVER_PEOPLE_TEAM),
    Stage(2, "company", KIND_CONTENT, "The company", "La empresa", 3, None, DEFAULT_PASS_THRESHOLD),
    Stage(3, "operations", KIND_CONTENT, "How we operate", "Cómo operamos", 4, None, DEFAULT_PASS_THRESHOLD),
    Stage(
        4,
        "compliance",
        KIND_CONTENT,
        "Compliance",
        "Cumplimiento",
        6,
        APPROVER_COMPLIANCE,
        COMPLIANCE_PASS_THRESHOLD,
        escalates_when_overdue=False,
    ),
    Stage(5, "role", KIND_CONTENT, "Your role", "Tu función", 8, APPROVER_LINE_MANAGER, DEFAULT_PASS_THRESHOLD),
    Stage(6, "ways_of_working", KIND_CONTENT, "How we work", "Cómo trabajamos", 9, None, DEFAULT_PASS_THRESHOLD),
    Stage(7, "access_closure", KIND_CLOSURE, "Access and closure", "Accesos y cierre", 10, APPROVER_VP_PEOPLE),
)

STAGES_BY_NUMBER: Dict[int, Stage] = {stage.number: stage for stage in STAGES}
FIRST_STAGE = STAGES[0].number
LAST_STAGE = STAGES[-1].number
COMPLIANCE_STAGE = 4  # la que desbloquea accesos a sistemas con datos de pacientes


def get_stage(number: int) -> Stage:
    try:
        return STAGES_BY_NUMBER[number]
    except KeyError as exc:  # pragma: no cover - error de programación
        raise ValueError(f"Etapa desconocida: {number}") from exc


# --- A quién se incorpora (BRIEFING, "A quién se incorpora") -----------------

DEPARTMENTS: Dict[str, str] = {
    "digital": "HealthCore Digital",
    "revenue_cycle": "Ciclo de Ingresos y Facturación",
    "compliance": "Cumplimiento y Gobernanza de Datos",
    "patient_experience": "Experiencia del Paciente (coordinación central)",
    "people": "Personas",
    "leadership": "Dirección",
}

SITES: Dict[str, str] = {"austin": "Austin", "london": "Londres"}
SITE_TIMEZONES: Dict[str, str] = {"austin": "America/Chicago", "london": "Europe/London"}

FRAMEWORK_HIPAA = "hipaa"
FRAMEWORK_UK_GDPR = "uk_gdpr"
FRAMEWORKS: Dict[str, str] = {FRAMEWORK_HIPAA: "HIPAA", FRAMEWORK_UK_GDPR: "UK GDPR"}

# Idiomas: inglés es la base (sedes de Austin y Londres). El español existe
# solo para incorporaciones bajo HIPAA (el BRIEFING lo justifica "para
# incorporaciones de Florida"): la variante UK GDPR del material de
# cumplimiento no existe en español y el agente nunca la traduce al vuelo.
LANGUAGES: Dict[str, str] = {"en": "English", "es": "Español"}
SPANISH_ALLOWED_FRAMEWORKS = frozenset({FRAMEWORK_HIPAA})


@dataclass(frozen=True)
class RoleVariant:
    key: str
    department: str
    title_en: str
    title_es: str


# Puestos con material propio para la etapa 5 (el BRIEFING pide al menos dos).
# Un puesto sin variante no se puede dar de alta: el agente no inventa el
# material de una función que nadie ha escrito.
ROLE_VARIANTS: Dict[str, RoleVariant] = {
    "digital_engineer": RoleVariant("digital_engineer", "digital", "HealthCore Digital engineer", "Ingeniero/a de HealthCore Digital"),
    "revenue_cycle_analyst": RoleVariant(
        "revenue_cycle_analyst", "revenue_cycle", "Revenue Cycle analyst", "Analista de Ciclo de Ingresos"
    ),
}

# --- Directorio de aprobadores ------------------------------------------------

# Rol organizativo de una persona del equipo en el onboarding (tabla
# onboarding_staff). NO es el rol de la plataforma (`user`/`admin` de la
# TinyDB): ser admin del backoffice no da permiso para aprobar cumplimiento.
ORG_ROLE_PEOPLE_TEAM = "people_team"
ORG_ROLE_VP_PEOPLE = "vp_people"
ORG_ROLE_COMPLIANCE = "compliance_approver"
ORG_ROLE_LINE_MANAGER = "line_manager"
ORG_ROLES = frozenset({ORG_ROLE_PEOPLE_TEAM, ORG_ROLE_VP_PEOPLE, ORG_ROLE_COMPLIANCE, ORG_ROLE_LINE_MANAGER})

# Destinatarios de avisos que no son una persona concreta de la incorporación.
RECIPIENT_HIRE = "hire"
RECIPIENT_MANAGER = "manager"
RECIPIENT_COMPLIANCE = "compliance"  # Claire Whitfield
RECIPIENT_VP_PEOPLE = "vp_people"  # Diane Foster
RECIPIENT_PEOPLE_TEAM = "people_team"

# --- Sistemas (etapa 7, y el bloqueo de la "Restricción específica") ----------


@dataclass(frozen=True)
class System:
    key: str
    name: str
    contains_patient_data: bool
    departments: Tuple[str, ...]


_ALL = tuple(DEPARTMENTS)

SYSTEMS: Dict[str, System] = {
    system.key: system
    for system in (
        System("microsoft_365", "Microsoft 365", False, _ALL),
        System("slack", "Slack", False, _ALL),
        System("jira", "Jira", False, _ALL),
        System("notion", "Notion", False, _ALL),
        System("github", "GitHub (HealthCore Digital)", False, ("digital",)),
        System("ehr_epic", "Epic EHR (US clinics)", True, ("revenue_cycle", "patient_experience", "compliance")),
        System("ehr_emis", "EMIS Health (UK clinics)", True, ("revenue_cycle", "patient_experience", "compliance")),
        System("availity", "Availity (claims and eligibility)", True, ("revenue_cycle",)),
        System("patient_comms", "Patient messaging platform (Twilio)", True, ("patient_experience", "digital")),
        System("data_platform", "HealthCore data platform (PHI zone)", True, ("digital", "compliance")),
    )
}


def department_systems(department: str) -> Tuple[System, ...]:
    """Sistemas que la etapa 7 solicita por defecto para un departamento."""
    return tuple(system for system in SYSTEMS.values() if department in system.departments)


# --- Identidad del agente ----------------------------------------------------

AGENT_ACTOR_ID = "onboarding-orchestrator"
ACTOR_AGENT = "agent"
ACTOR_HUMAN = "human"
