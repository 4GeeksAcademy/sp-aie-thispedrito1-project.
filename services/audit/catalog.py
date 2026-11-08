"""Catálogo cerrado del registro de auditoría (BRIEFING de Registro de Auditoría).

Todo lo que puede aparecer en una entrada sale de aquí. Un valor fuera de
lista es un error de programación que revienta al escribir (`ValueError`),
nunca un dato que se cuela: así el registro no puede acabar con texto libre
y, por tanto, con PHI (BRIEFING §4).
"""

from __future__ import annotations

import re
import uuid

# --- Actores -----------------------------------------------------------------
# El "quién" no es solo una persona (README, exigencia 2).

ACTOR_HUMAN = "human"
ACTOR_PROCESS = "process"
# Intento sin cuenta identificable (login fallido de un email que no existe):
# no hay persona a la que atribuirlo, pero sí origen.
ACTOR_ANONYMOUS = "anonymous"
ACTOR_TYPES = frozenset({ACTOR_HUMAN, ACTOR_PROCESS, ACTOR_ANONYMOUS})

# Procesos y agentes que actúan sobre datos sin que una persona pulse el botón
# de esa acción concreta. El id de actor de un proceso es su nombre.
PROCESS_CLINICAL_DOCUMENTATION = "clinical_documentation_assistant"
PROCESS_ONBOARDING_AGENT = "onboarding_agent"
PROCESS_FINANCIAL_CLOSE_AGENT = "financial_close_agent"
PROCESS_AUDIT_SEED = "audit_seed_script"
PROCESS_ACTORS = frozenset(
    {
        PROCESS_CLINICAL_DOCUMENTATION,
        PROCESS_ONBOARDING_AGENT,
        PROCESS_FINANCIAL_CLOSE_AGENT,
        PROCESS_AUDIT_SEED,
    }
)

# --- Resultado -----------------------------------------------------------------

OUTCOME_SUCCESS = "success"
OUTCOME_DENIED = "denied"  # la plataforma rechazó la acción (403, credenciales)
OUTCOME_BLOCKED = "blocked"  # una regla de negocio la paró (p. ej. sin Compliance)
OUTCOMES = frozenset({OUTCOME_SUCCESS, OUTCOME_DENIED, OUTCOME_BLOCKED})

# --- Recursos ------------------------------------------------------------------

RESOURCE_SESSION = "session"
RESOURCE_USER = "user"
RESOURCE_ROLE = "role"
RESOURCE_DEPARTMENT = "department"
RESOURCE_PATIENT_RECORD = "patient_record"
RESOURCE_INSURANCE_CLAIM = "insurance_claim"
RESOURCE_PATIENT_EXPORT = "patient_export"
RESOURCE_ACCESS_REQUEST = "access_request"
RESOURCE_FINANCIAL_REPORT = "financial_report"
RESOURCE_TYPES = frozenset(
    {
        RESOURCE_SESSION,
        RESOURCE_USER,
        RESOURCE_ROLE,
        RESOURCE_DEPARTMENT,
        RESOURCE_PATIENT_RECORD,
        RESOURCE_INSURANCE_CLAIM,
        RESOURCE_PATIENT_EXPORT,
        RESOURCE_ACCESS_REQUEST,
        RESOURCE_FINANCIAL_REPORT,
    }
)

# --- Acciones (BRIEFING §2) ------------------------------------------------------
# acción → recurso sobre el que se aplica. El recurso no se elige al escribir:
# sale de aquí, así que una acción nunca queda etiquetada con un recurso ajeno.

PATIENT_RECORD_VIEWED = "patient_record_viewed"

ACTIONS: dict[str, str] = {
    # Autenticación
    "login_succeeded": RESOURCE_SESSION,
    "login_failed": RESOURCE_SESSION,
    # Usuarios y sus roles/departamentos
    "user_created": RESOURCE_USER,
    "user_updated": RESOURCE_USER,
    "user_assignment_changed": RESOURCE_USER,
    "user_deleted": RESOURCE_USER,
    "role_created": RESOURCE_ROLE,
    "role_updated": RESOURCE_ROLE,
    "department_created": RESOURCE_DEPARTMENT,
    "department_updated": RESOURCE_DEPARTMENT,
    # Pacientes: TODO acceso, también la lectura
    PATIENT_RECORD_VIEWED: RESOURCE_PATIENT_RECORD,
    "patient_clinical_updated": RESOURCE_PATIENT_RECORD,
    "patient_billing_updated": RESOURCE_PATIENT_RECORD,
    "patient_documentation_drafted": RESOURCE_PATIENT_RECORD,
    "insurance_claim_approved": RESOURCE_INSURANCE_CLAIM,
    "insurance_claim_denied": RESOURCE_INSURANCE_CLAIM,
    "patient_data_exported": RESOURCE_PATIENT_EXPORT,
    # Procesos y agentes automatizados
    "access_request_issued": RESOURCE_ACCESS_REQUEST,
    "access_request_blocked": RESOURCE_ACCESS_REQUEST,
    "financial_report_prepared": RESOURCE_FINANCIAL_REPORT,
}

# Acciones que tocan datos de pacientes: exigen `patient_ref` (o, en la
# exportación, la lista de refs en `details`).
PATIENT_ACTIONS = frozenset(
    {
        PATIENT_RECORD_VIEWED,
        "patient_clinical_updated",
        "patient_billing_updated",
        "patient_documentation_drafted",
        "insurance_claim_approved",
        "insurance_claim_denied",
    }
)

# --- Contexto del acceso ---------------------------------------------------------
# BRIEFING §2: "desde qué contexto (ej. 'resolviendo referral', 'revisión
# rutinaria')". Lista cerrada: un motivo en texto libre podría traer PHI.

ACCESS_PURPOSES: dict[str, str] = {
    "referral_resolution": "Resolviendo referral",
    "routine_review": "Revisión rutinaria",
    "care_coordination": "Coordinación de cuidados",
    "billing_review": "Revisión de facturación",
    "claim_review": "Revisión de reclamación",
    "compliance_investigation": "Investigación de Compliance",
    "patient_request": "Solicitud del propio paciente",
}

# --- Detalles permitidos -----------------------------------------------------------
# Solo códigos, ids opacos, recuentos y nombres de campo. NUNCA valores
# clínicos: `changed_fields` dice QUÉ campo cambió, no a qué valor.

ALLOWED_DETAIL_KEYS = frozenset(
    {
        "changed_fields",
        "sections",
        "record_count",
        "patient_refs",
        "format",
        "reason_code",
        "claim_ref",
        "login_method",
        "identifier_hash",
        "target_user",
        "from_role",
        "to_role",
        "from_department",
        "to_department",
        "role_code",
        "department_code",
        "system_key",
        "hire_ref",
        "period",
        "version",
        "trigger",
        "figures_sha256",
        "seed_batch",
    }
)
# Identificadores opacos y huellas: NO pasan por el escáner de PHI (una serie
# de dígitos dentro de un UUID o un SHA-256 puede parecer un teléfono, y
# "PT-AB1C2DE5" contiene algo con forma de código postal británico). En su
# lugar se exige su formato exacto, que es más estricto que escanear.
_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
_CLAIM_REF = re.compile(r"^CL-[0-9A-F]{8}$")

MAX_DETAIL_STRING = 128
MAX_DETAIL_LIST = 500

# Identificador opaco de paciente: "PT-" + 8 hex. No se deriva de ningún dato
# del paciente (BRIEFING §4) y no tiene series largas de dígitos que el
# validador de PHI confunda con un teléfono.
PATIENT_REF_PATTERN = re.compile(r"^PT-[0-9A-F]{8}$")

OPAQUE_DETAIL_FORMATS = {
    "identifier_hash": _HEX64,
    "figures_sha256": _HEX64,
    "target_user": _UUID,
    "hire_ref": _UUID,
    "claim_ref": _CLAIM_REF,
    "patient_refs": PATIENT_REF_PATTERN,
}

# Clave del advisory lock de Postgres que serializa la cadena (un solo
# "último eslabón" a la vez). Número arbitrario, fijo.
AUDIT_CHAIN_LOCK_KEY = 7_311_204_001


# --- Departamentos ---------------------------------------------------------------
# Mismo uuid5 que `access_models.department_id_for` (services/api). Se repite
# aquí para que los procesos del worker (onboarding, cierre financiero) no
# importen la API; un test fija que ambos coinciden.
_DEPARTMENT_NAMESPACE = uuid.UUID("6c1f3a52-8f4e-4c55-9d0f-3c3f0b6f2a10")


def department_id(code: str) -> str:
    return str(uuid.uuid5(_DEPARTMENT_NAMESPACE, f"department:{code}"))
