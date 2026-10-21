"""Fixtures del flujo de RFPs de punta a punta (Hito 9, Partes 1 → 3). Sin red.

Los usan tests/pipelines/test_rfp_approval.py, tests/pipelines/test_rfp_e2e.py
y scripts/run_rfp_e2e.py (modo --offline), así que el ejemplo reproducible
del PR y los tests recorren exactamente los mismos datos y decisiones.

- `ScriptedLlm`: modelo falso que reconoce a cada agente por su prompt de
  sistema (clasificador, orquestador, workers, synthesizer, generadores,
  revisor de pertinencia) y graba lo que recibió cada uno.
- Borradores de la RFP de Meridian (US) en español que pasan los tres
  evaluadores de la Parte 2: solo usan la cifra 800 (la de la RFP).
- `seed_ticket`: un ticket como lo deja la Parte 2 (`evaluacion_completa` +
  handoffs v1), para probar la Parte 3 sin correr las anteriores.
- `run_parallel_scenario`: las aprobaciones humanas simuladas, por el MISMO
  camino que la API (`submit_*` + `advance`).
"""

from __future__ import annotations

import copy
import json
import re
import threading
import uuid
from typing import Any, Callable, Dict, List, Optional

from sqlmodel import Session, SQLModel

from data.pipelines.rfp_intake.departments import DEPARTMENTS
from data.pipelines.rfp_intake.models import (
    NEXT_STAGE_APPROVAL,
    STATUS_EVALUATED,
    RfpDepartmentSection,
    RfpFinalDocument,
    RfpMetadata,
    RfpTicket,
    RfpTraceEvent,
)

RFP_TABLES = [
    RfpTicket.__table__,
    RfpMetadata.__table__,
    RfpDepartmentSection.__table__,
    RfpFinalDocument.__table__,
    RfpTraceEvent.__table__,
]


def create_rfp_tables(engine: Any) -> None:
    # Solo estas tablas: el metadata entero incluye reporting.* (sin esquema en SQLite).
    SQLModel.metadata.create_all(engine, tables=RFP_TABLES)


# --------------------------------------------------------------------------
# Parte 1: la RFP de Meridian y las respuestas de sus agentes
# --------------------------------------------------------------------------

MERIDIAN = """REQUEST FOR PROPOSAL (RFP)

On-Site Occupational Health & Corporate Wellness Program

Issuing Organization: Meridian Manufacturing, Inc.

Proposal Due Date: August 23, 2026

Meridian Manufacturing operates an 800-employee manufacturing facility in Austin, Texas.

- On-site occupational health clinic staffed 3 days/week

- Annual wellness screenings for all 800 employees

- Chronic condition management support (diabetes, hypertension)

- Contract term: 12 months, with option to renew

- Vendor must be able to execute a Business Associate Agreement (BAA) in compliance with HIPAA

Proposals must be submitted electronically to procurement@meridianmfg.com no later than the due date."""

ACCEPT = {"is_rfp": True, "confidence": 0.93, "document_kind": "formal_rfp", "reason": "Empleador pide propuesta"}
MERIDIAN_PLAN = {
    "client_name": "Meridian Manufacturing, Inc.",
    "client_country": "US",
    "program_type": ["occupational_health", "corporate_wellness"],
    "covered_population": {"count": 800, "unit": "employees"},
    "deadline": "2026-08-23",
    "deadline_text": "August 23, 2026",
    "budget_range": None,
    "departments_needed": ["revenue", "clinical", "compliance"],
    "assignments": {"revenue": [0, 4, 8], "clinical": [5, 6, 7], "compliance": [10, 11]},
}
SYNTHESIS = {
    "summary": "Meridian pide salud ocupacional y bienestar en planta para 800 empleados.",
    "asks": [{"department_id": "clinical", "request": "Confirmar personal para la clínica en planta."}],
    "conflicts": [],
}
WORKERS = {
    "revenue": {"key_aspects": ["Cotizar en la moneda del cliente."], "open_questions": ["Confirmar el presupuesto de referencia."]},
    "clinical": {"key_aspects": ["Clínica en planta para los 800 empleados."], "open_questions": []},
    "compliance": {"key_aspects": ["El cliente exige una cláusula BAA."], "open_questions": []},
}

# Handoff de la Parte 1 (contrato v1, docs/rfp-intake.md) para sembrar tickets.
META_US = {
    "client_name": "Meridian Manufacturing, Inc.",
    "client_country": "US",
    "currency": "USD",
    "program_type": ["occupational_health", "corporate_wellness"],
    "covered_population": 800,
    "covered_population_unit": "employees",
    "deadline": "2026-08-23",
    "budget_range": None,
    "departments_needed": ["revenue", "clinical", "compliance"],
    "unmapped_departments": [],
    "open_questions": [],
}


def intake_handoff(ticket_id: str, rfp_id: str = "RFP-20261021-E2E001", metadata: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    return {
        "handoff_version": 1,
        "ticket_id": ticket_id,
        "rfp_id": rfp_id,
        "next_stage": "generando_borrador",
        "markdown_path": f"data/raw/rfp/{ticket_id}.md",
        "metadata": copy.deepcopy(metadata or META_US),
        "workstreams": [
            {"department_id": d, "owner": DEPARTMENTS[d].owner, "excerpt_indexes": [], **copy.deepcopy(WORKERS[d])}
            for d in ("revenue", "clinical", "compliance")
        ],
        "summary": SYNTHESIS["summary"],
        "who_to_ask": [],
        "conflicts": [],
    }


# --------------------------------------------------------------------------
# Parte 2: borradores que pasan legibilidad, pertinencia y cumplimiento
# --------------------------------------------------------------------------

REVENUE_OK = (
    "Términos económicos\n"
    "HealthCore cotiza este programa en dólares estadounidenses, es decir, en USD, como corresponde a un cliente de Estados Unidos. "
    "La propuesta cubre a los 800 empleados de la planta de Meridian Manufacturing. "
    "El precio por empleado y por servicio queda [por confirmar] por Revenue Cycle antes del envío. "
    "Proponemos una cuota mensual fija que incluye la clínica en planta y las revisiones de bienestar. "
    "El contrato tendría una duración de un año, con opción de renovación al terminar el periodo. "
    "La facturación sería mensual y el pago se haría por transferencia en el plazo que acuerden las partes. "
    "Los importes finales dependerán del calendario de atención que confirme el equipo clínico. "
    "Pendiente de confirmar: el presupuesto de referencia del cliente, que la solicitud no indica todavía."
)
CLINICAL_OK = (
    "Viabilidad clínica y capacidad\n"
    "HealthCore abrirá una clínica de salud laboral dentro de la planta durante tres días por semana. "
    "El equipo atenderá consultas laborales, revisiones preventivas y seguimiento de bienestar de los trabajadores. "
    "Las revisiones anuales de bienestar cubrirán a los 800 empleados que indica la solicitud del cliente. "
    "La sede que prestará el servicio y el número de profesionales quedan [por confirmar] por Clinical Operations. "
    "El arranque se hará por fases, empezando por los turnos con más personal en la planta. "
    "El cliente debe facilitar un espacio privado y accesible para las consultas dentro de sus instalaciones. "
    "Cualquier cambio de turnos se acordará con antelación para no dejar a nadie sin cobertura. "
    "Pendiente de confirmar: la sede de referencia y la plantilla clínica asignada al contrato."
)
COMPLIANCE_OK = (
    "Protección de datos y cumplimiento normativo\n"
    "El programa se rige por la ley HIPAA, porque Meridian Manufacturing es un cliente de Estados Unidos. "
    "Antes de empezar, las dos partes firmarán un Business Associate Agreement, también llamado BAA. "
    "Ese acuerdo fija cómo HealthCore protege la información de salud de los trabajadores en todo momento. "
    "El cliente solo recibirá datos agregados de participación, nunca información clínica de una persona concreta. "
    "Los registros médicos se guardarán en los sistemas de HealthCore, con acceso limitado al personal autorizado. "
    "Cualquier incidente de seguridad se comunicará al cliente en los plazos que marca la normativa aplicable. "
    "La propuesta necesita la aprobación de Compliance and Data Governance antes de enviarse al cliente. "
    "Pendiente de confirmar: ninguna cuestión adicional de protección de datos por ahora."
)
OK_DRAFTS = {"revenue": REVENUE_OK, "clinical": CLINICAL_OK, "compliance": COMPLIANCE_OK}

# Trigger `baa-dpa-mismatch`: promete HIPAA pero sin la cláusula BAA.
COMPLIANCE_NO_BAA = COMPLIANCE_OK.replace(
    "Antes de empezar, las dos partes firmarán un Business Associate Agreement, también llamado BAA. ",
    "Antes de empezar, las dos partes revisarán juntas las medidas de seguridad de la información. ",
)
# Trigger `phi-detected`: un "ejemplo" con un paciente (ficticio) dentro.
CLINICAL_WITH_PHI = CLINICAL_OK + "\nPatient: Daniel Fakename. Diagnosis: type 2 diabetes."
# Versiones tras la decisión de Tom (población reducida a 500).
REVENUE_500 = REVENUE_OK.replace("800 empleados", "500 empleados")
CLINICAL_500 = CLINICAL_OK.replace("800 empleados", "500 empleados")
# Versión de Clinical tras una solicitud de cambios (el arranque se concreta).
CLINICAL_REVISED = CLINICAL_OK.replace(
    "El arranque se hará por fases, empezando por los turnos con más personal en la planta. ",
    "El arranque se hará por fases y empezará por el turno de mañana, que concentra más personal. ",
)


def response_handoff(ticket_id: str, rfp_id: str, drafts: Dict[str, str], provisional: Optional[Dict[str, bool]] = None) -> Dict[str, Any]:
    """Handoff v1 de la Parte 2 (docs/rfp-response.md) con borradores dados."""
    provisional = provisional or {}
    sections = []
    for department_id in ("revenue", "clinical", "compliance"):
        flagged = provisional.get(department_id, False)
        sections.append({
            "department_id": department_id,
            "owner": DEPARTMENTS[department_id].owner,
            "draft_status": "needs_human_review" if flagged else "aprobada_por_evaluadores",
            "provisional": flagged,
            "iterations": 1,
            "draft_content": drafts[department_id],
            "evaluation_result": evaluation_result(ticket_id, department_id, passed=not flagged),
        })
    return {
        "handoff_version": 1, "ticket_id": ticket_id, "rfp_id": rfp_id, "ticket_status": STATUS_EVALUATED,
        "next_stage": NEXT_STAGE_APPROVAL, "client_name": META_US["client_name"], "client_country": "US",
        "currency": "USD", "max_iterations": 3, "sections": sections,
    }


def evaluation_result(ticket_id: str, department_id: str, *, passed: bool = True) -> Dict[str, Any]:
    return {
        "section_id": f"{ticket_id}:{department_id}", "department_id": department_id, "iteration": 1,
        "readability": {"pass": True, "score": 55.0, "details": {"problems": []}},
        "relevance": {"pass": passed, "missing_aspects": [] if passed else ["Aspecto sin cubrir"]},
        "compliance": {"pass": True, "rule_ids": ["no-phi"], "violations": [], "contains_phi": False},
        "overall_pass": passed, "feedback_for_generator": "" if passed else "1. Aborda el aspecto sin cubrir.",
        "retryable": True, "evaluated_at": "2026-10-21T10:00:00+00:00",
    }


def seed_ticket(session: Session, drafts: Optional[Dict[str, str]] = None, *, metadata: Optional[Dict[str, Any]] = None,
                provisional: Optional[Dict[str, bool]] = None) -> uuid.UUID:
    """Ticket tal como lo deja la Parte 2 al terminar."""
    ticket_id = uuid.uuid4()
    rfp_id = f"RFP-20261021-{ticket_id.hex[:6].upper()}"
    drafts = drafts or OK_DRAFTS
    handoff = intake_handoff(str(ticket_id), rfp_id, metadata)
    session.add(RfpTicket(
        ticket_id=ticket_id, rfp_id=rfp_id, status=STATUS_EVALUATED, raw_pdf_path=f"data/raw/rfp/{ticket_id}.pdf",
        created_by="user-1", handoff_payload=handoff, next_stage=NEXT_STAGE_APPROVAL,
        response_handoff=response_handoff(str(ticket_id), rfp_id, drafts, provisional),
    ))
    session.flush()
    meta = handoff["metadata"]
    session.add(RfpMetadata(
        ticket_id=ticket_id, client_name=meta["client_name"], client_country=meta["client_country"],
        currency=meta["currency"], program_type=meta["program_type"], covered_population=meta["covered_population"],
        covered_population_unit=meta["covered_population_unit"], departments_needed=meta["departments_needed"],
    ))
    for workstream in handoff["workstreams"]:
        department_id = workstream["department_id"]
        session.add(RfpDepartmentSection(
            ticket_id=ticket_id, department_id=department_id, owner=workstream["owner"],
            key_aspects=workstream["key_aspects"], open_questions=workstream["open_questions"],
            draft_content=drafts[department_id], draft_status="aprobada_por_evaluadores", draft_iterations=1,
            evaluation_results=evaluation_result(str(ticket_id), department_id),
        ))
    session.commit()
    return ticket_id


# --------------------------------------------------------------------------
# Modelo falso para las tres partes
# --------------------------------------------------------------------------


class ScriptedLlm:
    """`drafts[dept]` es la lista de borradores que devuelve su generador,
    uno por llamada (el último se repite). `fail[agent] = n` hace fallar las
    n primeras llamadas de ese agente (proveedor caído)."""

    def __init__(self, drafts: Optional[Dict[str, List[str]]] = None, fail: Optional[Dict[str, int]] = None) -> None:
        self.drafts = {d: [text] for d, text in OK_DRAFTS.items()}
        self.drafts.update(drafts or {})
        self.fail = dict(fail or {})
        self.calls: List[Dict[str, str]] = []
        self._count: Dict[str, int] = {}
        self._lock = threading.Lock()

    @staticmethod
    def agent_of(system: str) -> str:
        if "clasificador" in system:
            return "classifier"
        if "orquestador" in system:
            return "orchestrator"
        if "synthesizer" in system:
            return "synthesizer"
        if system.startswith("Eres el revisor de pertinencia"):
            return "relevance"
        for department_id, department in DEPARTMENTS.items():
            if f"analista de {department.name}" in system:
                return f"worker:{department_id}"
            if system.startswith(f"Eres el redactor de {department.name}"):
                return f"generator:{department_id}"
        raise AssertionError(f"prompt desconocido: {system[:60]}")

    def __call__(self, system: str, user: str) -> str:
        agent = self.agent_of(system)
        with self._lock:
            self.calls.append({"agent": agent, "user": user})
            if self.fail.get(agent, 0) > 0:
                self.fail[agent] -= 1
                raise RuntimeError(f"proveedor caído ({agent})")
            if agent.startswith("generator:"):
                department_id = agent.split(":", 1)[1]
                n = self._count.get(agent, 0)
                self._count[agent] = n + 1
                script = self.drafts[department_id]
                return json.dumps({"content": script[min(n, len(script) - 1)]})
        if agent == "classifier":
            return json.dumps(ACCEPT)
        if agent == "orchestrator":
            return json.dumps(MERIDIAN_PLAN)
        if agent == "synthesizer":
            return json.dumps(SYNTHESIS)
        if agent.startswith("worker:"):
            return json.dumps(WORKERS[agent.split(":", 1)[1]])
        aspects = re.findall(r"^\[(\d+)\]", user, re.MULTILINE)
        return json.dumps({"covered": [int(n) for n in aspects], "missing": []})

    def agent_calls(self, agent: str) -> List[str]:
        return [c["user"] for c in self.calls if c["agent"] == agent]


# --------------------------------------------------------------------------
# Aprobaciones humanas simuladas (ruta E2E reproducible)
# --------------------------------------------------------------------------

SCENARIO_USERS = {"revenue": "sim-tom", "clinical": "sim-marcus", "compliance": "sim-claire"}


def run_parallel_scenario(
    session: Session,
    ticket_id: uuid.UUID,
    advance: Callable[[], str],
    *,
    log: Callable[[str], None] = lambda _line: None,
    capacity: int = 500,
) -> List[Dict[str, Any]]:
    """Recorre la Parte 3 con decisiones humanas simuladas:

    1. arranque → los tres departamentos esperan;
    2. Compliance aprueba mientras Revenue y Clinical siguen pausados;
    3. Clinical pide cambios → vuelve al generador de la Parte 2 → nueva pausa;
    4. Clinical aprueba confirmando capacidad para `capacity` personas (< 800);
    5. Revenue aprueba → trigger `capacity-vs-population` → arbitraje de Tom;
    6. Tom reduce la población a `capacity` → Revenue y Clinical se revisan;
    7. Revenue y Clinical aprueban la versión nueva → documento final.

    Devuelve una foto del estado tras cada paso (para tests y para el PR)."""
    from data.pipelines.rfp_intake import repository
    from data.pipelines.rfp_intake.approval_runner import submit_arbitration_decision, submit_section_decision

    steps: List[Dict[str, Any]] = []

    def snapshot(label: str) -> None:
        session.expire_all()
        ticket = repository.get_ticket(session, ticket_id)
        sections = repository.sections_by_department(session, ticket_id)
        photo = {
            "step": label,
            "status": ticket.status,
            "sections": {d: s.approval_status for d, s in sorted(sections.items())},
            "arbitration_pending": bool((ticket.arbitration or {}).get("pending")),
        }
        steps.append(photo)
        log(f"{label:<62} ticket={photo['status']:<22} " + " ".join(f"{d}={s}" for d, s in photo["sections"].items())
            + ("  [arbitraje pendiente]" if photo["arbitration_pending"] else ""))

    def decide(department_id: str, **raw: Any) -> None:
        submit_section_decision(session, ticket_id, department_id, raw, decided_by=SCENARIO_USERS[department_id])
        advance()

    advance()
    snapshot("1. Arranque de la Parte 3")
    decide("compliance", decision="approve")
    snapshot("2. Compliance aprueba (Revenue y Clinical siguen pausados)")
    decide("clinical", decision="request_changes", comments="Concretad por qué turno empieza el arranque por fases.")
    snapshot("3. Clinical solicita cambios → generador de la Parte 2")
    decide("clinical", decision="approve", confirmed_capacity=capacity)
    snapshot(f"4. Clinical aprueba con capacidad para {capacity}")
    decide("revenue", decision="approve")
    snapshot("5. Revenue aprueba → capacity-vs-population")
    submit_arbitration_decision(session, ticket_id, {"option": "reduce_population", "new_population": capacity},
                                decided_by=SCENARIO_USERS["revenue"])
    advance()
    snapshot(f"6. Tom Callahan reduce la población a {capacity}")
    decide("revenue", decision="approve")
    snapshot("7a. Revenue aprueba la versión revisada")
    decide("clinical", decision="approve", confirmed_capacity=capacity)
    snapshot("7b. Clinical aprueba la versión revisada → documento final")
    return steps


def run_approve_all_scenario(
    session: Session,
    ticket_id: uuid.UUID,
    advance: Callable[[], str],
    *,
    log: Callable[[str], None] = lambda _line: None,
) -> List[Dict[str, Any]]:
    """Camino feliz sin revisiones: cada responsable aprueba por separado
    (Compliance primero, mientras los otros dos siguen pausados) y Clinical
    confirma capacidad para toda la población. Útil con el modelo real: no
    añade llamadas al modelo en la Parte 3."""
    from data.pipelines.rfp_intake import repository
    from data.pipelines.rfp_intake.approval_runner import submit_section_decision

    steps: List[Dict[str, Any]] = []

    def snapshot(label: str) -> None:
        session.expire_all()
        ticket = repository.get_ticket(session, ticket_id)
        sections = {d: s.approval_status for d, s in sorted(repository.sections_by_department(session, ticket_id).items())}
        steps.append({"step": label, "status": ticket.status, "sections": sections,
                      "arbitration_pending": bool((ticket.arbitration or {}).get("pending"))})
        log(f"{label:<62} ticket={ticket.status:<22} " + " ".join(f"{d}={s}" for d, s in sections.items()))

    advance()
    snapshot("1. Arranque de la Parte 3")
    for number, department_id in enumerate(("compliance", "revenue", "clinical"), start=2):
        section = repository.sections_by_department(session, ticket_id)[department_id]
        raw: Dict[str, Any] = {"decision": "approve"}
        if (section.approval_request or {}).get("asks_capacity"):
            raw["confirmed_capacity"] = section.approval_request["covered_population"]
        submit_section_decision(session, ticket_id, department_id, raw, decided_by=SCENARIO_USERS[department_id])
        advance()
        snapshot(f"{number}. {DEPARTMENTS[department_id].owner} aprueba {DEPARTMENTS[department_id].name}")
    return steps
