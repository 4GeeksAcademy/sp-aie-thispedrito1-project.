"""Grafo de aprobación humana y cierre (Hito 9, Parte 3).

    START → open_approval ─Send×N─→ department_branch ─→ arbitrate ─┬─(sin conflicto)──→ synthesize_final_document → END
                                          ▲                         ├─(rechazo/límite)─→ close_without_document → END
                                          └──(Send: secciones que el árbitro obliga a revisar)

    department_branch = subgrafo `rfp_department_approval` (uno por departamento):

        screen_section ─(señal regulatoria)──────────────────→ END   (rama "blocked": decide el árbitro)
             │└─(cambios forzados por el árbitro)→ revise_section ─┐
             └→ request_approval ── interrupt() ── pausa SOLO esta rama
                     ├─ aprobar / rechazar / cancelar ───────→ END
                     └─ solicitar cambios → revise_section ──→ screen_section

- La pausa es un `interrupt()` dentro del subgrafo de UN departamento. Las
  ramas son tareas paralelas del mismo superstep: reanudar la de Clinical
  (`Command(resume={id_de_su_interrupt: ...})`) la lleva hasta su final
  mientras Revenue y Compliance siguen pausadas. `arbitrate` espera a que
  todas terminen (superstep), que es justo lo que pide el cierre.
- Se reanuda exactamente donde se paró: el checkpointer (Postgres) guarda el
  estado de cada rama en su propio `checkpoint_ns` dentro del hilo
  `rfp-{ticket_id}`. Ojo: LangGraph vuelve a ejecutar el nodo pausado desde
  su principio al reanudar (también los de las ramas que siguen esperando),
  así que lo que hay antes del `interrupt()` es puro: sin base de datos y
  sin llamadas al modelo.
- Límites verificables en código: `max_revision_rounds` rondas de cambios
  humanos por sección (la petición deja de ofrecer "solicitar cambios" y la
  validación lo rechaza) y `max_arbitration_rounds` vueltas del árbitro
  (después, la propuesta se descarta: decisión del usuario).
- "Solicitar cambios" vuelve al generador de la Parte 2 (su subgrafo
  generador-evaluador, con el comentario humano como feedback) dentro de la
  MISMA ejecución: no hace falta relanzar el ticket (pregunta de diseño 1).
- Trazabilidad: cada nodo devuelve una entrada `trace` (agente, entrada,
  salida, timestamp) y la manda a `on_trace` para persistirla.
- Como el resto del flujo, el grafo no toca la base de datos: el runner lee
  el estado (`get_state`) y escribe.
"""

from __future__ import annotations

import copy
import operator
from datetime import datetime, timezone
from typing import Annotated, Any, Dict, List, Optional, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import Send, interrupt

from data.pipelines.rfp_intake import arbitration, decisions
from data.pipelines.rfp_intake.agents import LlmCall, call_model
from data.pipelines.rfp_intake.departments import DEPARTMENT_IDS, DEPARTMENTS, country_rules
from data.pipelines.rfp_intake.final_document import build_final_document
from data.pipelines.rfp_intake.graph import validate_structure
from data.pipelines.rfp_intake.models import (
    DECISION_APPROVE,
    DECISION_CANCEL,
    DECISION_REJECT,
    DECISION_REQUEST_CHANGES,
)
from data.pipelines.rfp_intake.response_graph import build_section_graph
from data.pipelines.rfp_intake.trace import TraceEntry, TraceSink, entry, no_trace, text_digest

GRAPH_NAME = "rfp_approval"
BRANCH_GRAPH_NAME = "rfp_department_approval"

DEFAULT_MAX_REVISION_ROUNDS = 2      # rondas de "solicitar cambios" por sección
DEFAULT_MAX_ARBITRATION_ROUNDS = 2   # vueltas del árbitro antes de descartar
# Cada revisión reutiliza el ciclo generador-evaluador de la Parte 2, más
# corto: el borrador ya pasó por él y lo que se corrige es un comentario.
REVISION_MAX_ITERATIONS = 2

INTERRUPT_SECTION = "section_approval"
INTERRUPT_ARBITRATION = "arbitration"

BRANCH_APPROVED = arbitration.BRANCH_APPROVED
BRANCH_REJECTED = "rejected"
BRANCH_BLOCKED = "blocked"
BRANCH_CANCELLED = "cancelled"

RESULT_FINISHED = "finished"
RESULT_REJECTED = "rejected"
RESULT_ARBITRATION_LIMIT = "arbitration_limit"


def merge_outcomes(left: Optional[Dict[str, Any]], right: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Reducer de `outcomes`: una entrada por departamento y la más reciente
    gana (una sección que el árbitro reabre sustituye a su resultado
    anterior; las demás se conservan)."""
    return {**(left or {}), **(right or {})}


class BranchState(TypedDict, total=False):
    thread_id: str
    department_id: str
    context: Dict[str, Any]
    generator_input: Dict[str, Any]
    figures_allowed: List[str]
    draft: str
    evaluation: Dict[str, Any]
    provisional: bool
    forced_feedback: Optional[str]
    arbitration_round: int
    revision_round: int
    max_revision_rounds: int
    cycle: int  # vueltas de revisión en esta ejecución de la rama (claves del trace)
    signals: List[Dict[str, Any]]
    decision: Optional[Dict[str, Any]]
    trace: Annotated[List[TraceEntry], operator.add]


class ApprovalState(TypedDict, total=False):
    ticket_id: str
    thread_id: str
    context: Dict[str, Any]
    tasks: Dict[str, Dict[str, Any]]  # entrada base de cada rama (actualizada por el árbitro)
    max_revision_rounds: int
    max_arbitration_rounds: int
    arbitration_round: int
    outcomes: Annotated[Dict[str, Dict[str, Any]], merge_outcomes]
    arbitration: Dict[str, Any]
    result: str
    final_document: Dict[str, Any]
    trace: Annotated[List[TraceEntry], operator.add]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _emit(on_trace: TraceSink, item: TraceEntry) -> List[TraceEntry]:
    try:
        on_trace(item)
    except Exception:  # noqa: BLE001 — persistir el trace nunca tumba la aprobación
        pass
    return [item]


def build_approval_request(state: BranchState) -> Dict[str, Any]:
    """Lo mínimo para decidir sin releer el documento (pregunta de diseño 2):
    el borrador, si es provisional y por qué, lo que la RFP pide a este
    departamento, los hechos fijados por país y cuántas rondas quedan."""
    department_id = state["department_id"]
    department = DEPARTMENTS[department_id]
    context = state["context"]
    evaluation = state.get("evaluation") or {}
    compliance = evaluation.get("compliance") or {}
    rules = country_rules(context.get("client_country")) or {}
    revision_round = state.get("revision_round", 0)
    max_rounds = state["max_revision_rounds"]
    return {
        "kind": INTERRUPT_SECTION,
        "department_id": department_id,
        "department": department.name,
        "approver": department.owner,
        "draft": state["draft"],
        "draft_digest": text_digest(state["draft"])["sha256"],
        "provisional": bool(state.get("provisional")),
        "evaluation": {
            "overall_pass": evaluation.get("overall_pass"),
            "iteration": evaluation.get("iteration"),
            "readability_pass": (evaluation.get("readability") or {}).get("pass"),
            "readability_score": (evaluation.get("readability") or {}).get("score"),
            "missing_aspects": (evaluation.get("relevance") or {}).get("missing_aspects", []),
            "compliance_rule_ids": compliance.get("rule_ids", []),
            "compliance_violations": [v.get("message") for v in compliance.get("violations", [])],
            "feedback": None if evaluation.get("overall_pass") else evaluation.get("feedback_for_generator"),
        },
        "key_aspects": state["generator_input"].get("key_aspects", []),
        "fixed_facts": {
            "client_country": context.get("client_country"),
            "currency": rules.get("currency"),
            "data_agreement": rules.get("data_agreement"),
            "regulation": rules.get("regulation"),
        },
        "covered_population": context.get("covered_population"),
        "covered_population_unit": context.get("covered_population_unit"),
        # Clinical confirma capacidad al aprobar: es la señal estructurada del
        # trigger `capacity-vs-population` (si la RFP no da población, no hay
        # con qué comparar y no se pide).
        "asks_capacity": department_id == "clinical" and context.get("covered_population") is not None,
        "revision_round": revision_round,
        "max_revision_rounds": max_rounds,
        "can_request_changes": revision_round < max_rounds,
        "arbitration_round": state.get("arbitration_round", 0),
    }


def build_branch_graph(*, llm: LlmCall = call_model, on_trace: TraceSink = no_trace) -> StateGraph:
    # Ciclo generador-evaluador de la Parte 2, sin checkpointer propio: corre
    # entero dentro de `revise_section` y no tiene nada que pausar.
    section_graph = build_section_graph(llm=llm)
    validate_structure(section_graph)
    reviser = section_graph.compile(name="rfp_section_revision", checkpointer=False)

    def key(state: BranchState, node: str) -> tuple:
        return (state["thread_id"], node, state["department_id"], state.get("arbitration_round", 0), state.get("cycle", 0))

    def screen_section(state: BranchState) -> Dict[str, Any]:
        """Guardarraíl determinista ANTES de molestar a un humano: los casos
        claros (PHI, BAA/DPA) los resuelve el árbitro por regla; no se le
        enseña al aprobador un borrador con datos de paciente."""
        if state.get("forced_feedback"):
            signals: List[Dict[str, Any]] = []
            route = "revise_section"
        else:
            signals = arbitration.regulatory_signals(
                state["draft"], department_id=state["department_id"], client_country=state["context"].get("client_country")
            )
            route = "blocked" if signals else "request_approval"
        item = entry(
            key=key(state, "screen_section"), part=3, node="screen_section", agent="compliance_rules",
            department_id=state["department_id"],
            input={"draft": text_digest(state["draft"]), "forced_by_arbiter": bool(state.get("forced_feedback"))},
            output={"route": route, "triggers": [s["trigger_id"] for s in signals]},
        )
        return {"signals": signals, "trace": _emit(on_trace, item)}

    def request_approval(state: BranchState) -> Dict[str, Any]:
        request = build_approval_request(state)
        raw = interrupt(request)  # ← la rama de ESTE departamento se pausa aquí
        if raw.get("decision") == DECISION_CANCEL:
            decision = {"decision": DECISION_CANCEL, "comments": None, "confirmed_capacity": None, "reason": raw.get("reason")}
        else:
            # Segunda capa: el runner ya validó; aquí se valida contra la
            # petición que ESTE nodo acaba de hacer (no entra nada sin pasarla).
            decision = decisions.validate_section_decision(raw, request)
        decision["decided_by"] = raw.get("decided_by")
        decision["decided_at"] = raw.get("decided_at") or _now()
        item = entry(
            key=key(state, "request_approval"), part=3, node="request_approval",
            agent=f"human:{request['approver']}", department_id=state["department_id"],
            input={
                "draft": text_digest(state["draft"]), "provisional": request["provisional"],
                "revision_round": request["revision_round"], "can_request_changes": request["can_request_changes"],
            },
            output={
                "decision": decision["decision"], "decided_by": decision["decided_by"],
                "has_comments": bool(decision.get("comments")), "comment_chars": len(decision.get("comments") or ""),
                "confirmed_capacity": decision.get("confirmed_capacity"),
            },
            timestamp=datetime.fromisoformat(decision["decided_at"]),
        )
        return {"decision": decision, "trace": _emit(on_trace, item)}

    def revise_section(state: BranchState) -> Dict[str, Any]:
        """Vuelve al generador de la Parte 2 con el motivo (humano o del
        árbitro) y repasa los tres evaluadores."""
        department = DEPARTMENTS[state["department_id"]]
        forced = state.get("forced_feedback")
        if forced:
            feedback, source = forced, "arbitration"
        else:
            comments = (state.get("decision") or {}).get("comments") or ""
            feedback = f"Revisión de {department.owner} ({department.name}): {comments}"
            source = "human"
        result = reviser.invoke({
            "section_id": f"{state['thread_id']}:{state['department_id']}",
            "generator_input": state["generator_input"],
            "figures_allowed": state["figures_allowed"],
            "max_iterations": REVISION_MAX_ITERATIONS,
            "iteration": 0,
            "draft": state["draft"],
            "evaluation": {"feedback_for_generator": feedback},
            "history": [],
        })
        evaluation = result["evaluation"]
        item = entry(
            key=key(state, "revise_section"), part=3, node="revise_section",
            agent=f"generator:{state['department_id']}+evaluators", department_id=state["department_id"],
            input={"draft": text_digest(state["draft"]), "feedback_source": source, "feedback_chars": len(feedback)},
            output={
                "draft": text_digest(result["draft"]), "iterations": result["iteration"],
                "overall_pass": evaluation["overall_pass"],
            },
        )
        return {
            "draft": result["draft"],
            "evaluation": evaluation,
            "provisional": not evaluation["overall_pass"],
            "forced_feedback": None,
            "decision": None,
            "revision_round": state.get("revision_round", 0) + (1 if source == "human" else 0),
            "cycle": state.get("cycle", 0) + 1,
            "trace": _emit(on_trace, item),
        }

    builder = StateGraph(BranchState)
    builder.add_node("screen_section", screen_section)
    builder.add_node("request_approval", request_approval)
    builder.add_node("revise_section", revise_section)
    builder.add_edge(START, "screen_section")
    builder.add_conditional_edges(
        "screen_section", route_after_screen,
        {"revise_section": "revise_section", "request_approval": "request_approval", END: END},
    )
    builder.add_conditional_edges("request_approval", route_after_decision, {"revise_section": "revise_section", END: END})
    builder.add_edge("revise_section", "screen_section")
    return builder


def route_after_screen(state: BranchState) -> str:
    if state.get("forced_feedback"):
        return "revise_section"
    return END if state.get("signals") else "request_approval"


def route_after_decision(state: BranchState) -> str:
    decision = state["decision"]["decision"]
    if decision == DECISION_REQUEST_CHANGES:
        # La validación ya impide pasar del límite; esto es la red de seguridad.
        if state.get("revision_round", 0) >= state["max_revision_rounds"]:
            raise decisions.DecisionError([{"field": "decision", "message": "Límite de rondas de cambios alcanzado."}])
        return "revise_section"
    return END  # aprobar, rechazar o cancelar cierran la rama


def branch_outcome(task: Dict[str, Any], final: Dict[str, Any]) -> Dict[str, Any]:
    decision = final.get("decision") or {}
    status = {
        DECISION_APPROVE: BRANCH_APPROVED,
        DECISION_REJECT: BRANCH_REJECTED,
        DECISION_CANCEL: BRANCH_CANCELLED,
    }.get(decision.get("decision"), BRANCH_BLOCKED)
    return {
        "department_id": task["department_id"],
        "status": status,
        "draft": final["draft"],
        "evaluation": final.get("evaluation"),
        "provisional": bool(final.get("provisional")),
        "signals": final.get("signals") or [],
        "revision_round": final.get("revision_round", 0),
        "arbitration_round": task.get("arbitration_round", 0),
        "confirmed_capacity": decision.get("confirmed_capacity") if status == BRANCH_APPROVED else None,
        "decided_by": decision.get("decided_by"),
        "decided_at": decision.get("decided_at"),
        "has_comments": bool(decision.get("comments")),
    }


def build_approval_graph(*, llm: LlmCall = call_model, on_trace: TraceSink = no_trace) -> StateGraph:
    branch_builder = build_branch_graph(llm=llm, on_trace=on_trace)
    validate_structure(branch_builder)
    # Sin `checkpointer=` propio: hereda el del grafo padre, que es lo que
    # permite pausar dentro de la rama y reanudar en otro proceso.
    branch_graph = branch_builder.compile(name=BRANCH_GRAPH_NAME)

    def open_approval(state: ApprovalState) -> Dict[str, Any]:
        item = entry(
            key=(state["thread_id"], "open_approval"), part=3, node="open_approval", agent="approval_orchestrator",
            input={"departments": list(state["tasks"]), "client_country": state["context"].get("client_country")},
            output={
                "branches": len(state["tasks"]), "max_revision_rounds": state["max_revision_rounds"],
                "max_arbitration_rounds": state["max_arbitration_rounds"],
            },
        )
        return {"arbitration_round": 0, "trace": _emit(on_trace, item)}

    def department_branch(task: Dict[str, Any]) -> Dict[str, Any]:
        final = branch_graph.invoke({**task, "trace": []})
        outcome = branch_outcome(task, final)
        return {"outcomes": {task["department_id"]: outcome}, "trace": final.get("trace", [])}

    def arbitrate(state: ApprovalState) -> Dict[str, Any]:
        round_number = state.get("arbitration_round", 0)
        max_rounds = state["max_arbitration_rounds"]
        outcomes = state["outcomes"]
        context = state["context"]
        trace_key = (state["thread_id"], "arbitrate", round_number)
        branch_input = {"round": round_number, "branches": {d: o["status"] for d, o in outcomes.items()}}

        def done(record: Dict[str, Any], agent: str, **updates: Any) -> Dict[str, Any]:
            item = entry(key=trace_key, part=3, node="arbitrate", agent=agent, input=branch_input,
                         output={k: v for k, v in record.items() if k != "forced"})
            return {"arbitration": record, "trace": _emit(on_trace, item), **updates}

        rejected = [d for d, o in outcomes.items() if o["status"] == BRANCH_REJECTED]
        if rejected:
            return done({"round": round_number, "resolution": "rejected", "rejected_by": rejected, "conflicts": []},
                        "arbiter:rules", result=RESULT_REJECTED)

        conflicts = arbitration.detect_conflicts(outcomes, context)
        if not conflicts:
            return done({"round": round_number, "resolution": "none", "conflicts": []}, "arbiter:rules")
        conflict_dicts = [c.to_dict() for c in conflicts]
        if round_number >= max_rounds:
            return done({"round": round_number, "resolution": "limit", "conflicts": conflict_dicts},
                        f"arbiter:{conflicts[0].arbiter}", result=RESULT_ARBITRATION_LIMIT)

        if conflicts[0].arbiter_kind == arbitration.ARBITER_HUMAN:
            request = arbitration.capacity_request(conflicts[0], round_number=round_number + 1, max_rounds=max_rounds)
            raw = interrupt(request)  # ← solo se llega aquí con todas las ramas terminadas
            decision = decisions.validate_arbitration_decision(raw, request)
            resolution = arbitration.capacity_resolution(decision, request)
            summary = {
                "option": decision["option"], "new_population": decision["new_population"],
                "has_note": bool(decision["note"]), "decided_by": raw.get("decided_by"),
                "decided_at": raw.get("decided_at") or _now(),
            }
            agent, kind = f"arbiter:human:{conflicts[0].arbiter}", "human"
        else:
            resolution = {"forced": arbitration.rule_resolution(conflicts), "context": {}, "extra_figures": []}
            summary, agent, kind = None, f"arbiter:rule:{conflicts[0].arbiter}", "rule"

        new_context = {**context, **resolution["context"]}
        tasks = copy.deepcopy(state["tasks"])
        for task in tasks.values():
            task["context"] = new_context
            task["generator_input"]["metadata"]["covered_population"] = new_context.get("covered_population")
            task["figures_allowed"] = sorted(set(task["figures_allowed"]) | set(resolution["extra_figures"]))
        record = {
            "round": round_number + 1,
            "resolution": kind,
            "conflicts": conflict_dicts,
            "decision": summary,
            "forced_departments": sorted(resolution["forced"]),
            "forced": resolution["forced"],
        }
        return done(record, agent, arbitration_round=round_number + 1, context=new_context, tasks=tasks)

    def synthesize_final_document(state: ApprovalState) -> Dict[str, Any]:
        document = build_final_document(state["context"], state["outcomes"], generated_at=datetime.now(timezone.utc))
        item = entry(
            key=(state["thread_id"], "synthesize_final_document"), part=3, node="synthesize_final_document",
            agent="ultimate_document_synthesizer",
            input={"approved": sorted(state["outcomes"]), "currency": document["currency"]},
            output={"sections": len(document["sections"]), "document": text_digest(document["content_markdown"])},
        )
        return {"final_document": document, "result": RESULT_FINISHED, "trace": _emit(on_trace, item)}

    def close_without_document(state: ApprovalState) -> Dict[str, Any]:
        item = entry(
            key=(state["thread_id"], "close_without_document"), part=3, node="close_without_document",
            agent="approval_orchestrator", input={"result": state["result"]},
            output={"final_document": None, "arbitration": {k: v for k, v in state["arbitration"].items() if k != "forced"}},
        )
        return {"trace": _emit(on_trace, item)}

    builder = StateGraph(ApprovalState)
    builder.add_node("open_approval", open_approval)
    builder.add_node("department_branch", department_branch)
    builder.add_node("arbitrate", arbitrate)
    builder.add_node("synthesize_final_document", synthesize_final_document)
    builder.add_node("close_without_document", close_without_document)
    builder.add_edge(START, "open_approval")
    builder.add_conditional_edges("open_approval", assign_branches, ["department_branch"])
    builder.add_edge("department_branch", "arbitrate")
    builder.add_conditional_edges(
        "arbitrate", route_after_arbitrate,
        ["department_branch", "synthesize_final_document", "close_without_document"],
    )
    builder.add_edge("synthesize_final_document", END)
    builder.add_edge("close_without_document", END)
    return builder


def assign_branches(state: ApprovalState) -> List[Send]:
    """Una rama por departamento con borrador, en el orden del catálogo."""
    return [Send("department_branch", state["tasks"][d]) for d in DEPARTMENT_IDS if d in state["tasks"]]


def route_after_arbitrate(state: ApprovalState) -> Any:
    if state.get("result"):
        return "close_without_document"
    forced = state["arbitration"].get("forced") or {}
    if not forced:
        return "synthesize_final_document"
    sends = []
    for department_id in DEPARTMENT_IDS:
        if department_id not in forced:
            continue
        outcome = state["outcomes"][department_id]
        task = copy.deepcopy(state["tasks"][department_id])
        task.update(
            draft=outcome["draft"],
            evaluation=outcome["evaluation"],
            provisional=outcome["provisional"],
            revision_round=outcome["revision_round"],
            forced_feedback=forced[department_id],
            arbitration_round=state["arbitration_round"],
            cycle=0,
        )
        sends.append(Send("department_branch", task))
    return sends


def compile_approval_graph(*, checkpointer: Any, llm: LlmCall = call_model, on_trace: TraceSink = no_trace) -> Any:
    builder = build_approval_graph(llm=llm, on_trace=on_trace)
    validate_structure(builder)
    return builder.compile(name=GRAPH_NAME, checkpointer=checkpointer)
