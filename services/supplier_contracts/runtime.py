"""Identidades de los agentes, contratos de entrada/salida y registro de acciones.

Contratos (README, "Define por escrito el contrato de entrada y salida"):

- Entrada del especialista de precios: `PricingRequest` (intent + proveedor).
- Entrada del especialista de contratos: `ContractsRequest` (intent +
  proveedor + subida a evaluar o tema de cláusula).
- Salida de ambos: `SpecialistResult`. Las cifras viajan como `Fact` con sus
  `refs` (fila del CSV, cláusula + página o dato de referencia del BRIEFING);
  lo que falta viaja en `missing`, nunca se rellena.

`AgentRuntime.call` es el único camino por el que un agente usa una
herramienta: comprueba que está en su lista (`catalog.AGENT_TOOLS`), registra
la acción con la identidad del agente y, si no le corresponde, la rechaza
(`ToolNotAllowed`) dejando constancia `denied`.
"""

from __future__ import annotations

import hashlib
import json
import logging
import uuid
from typing import Any, Callable, Dict, List, Literal, Optional

from pydantic import BaseModel, Field

from services.supplier_contracts.catalog import AGENT_TOOLS
from services.supplier_contracts.models import ScAgentAction

logger = logging.getLogger("healthcore.supplier_contracts.actions")

# --- Referencias y hechos ------------------------------------------------------


class Ref(BaseModel):
    """De dónde sale un dato. `kind`: price_record | supplier_record | contract_clause | reference | calculation."""

    kind: str
    source: str  # archivo o documento ("price_history.csv", "CT-HC-02.pdf", "BRIEFING")
    locator: str  # "fila 27", "§4.2, p. 2", "Datos de referencia"

    def label(self) -> str:
        return f"{self.source} {self.locator}"


def price_ref(source_file: str, source_row: int) -> Ref:
    return Ref(kind="price_record", source=source_file, locator=f"fila {source_row}")


def clause_ref(source_file: str, clause: Optional[str], page: Optional[int]) -> Ref:
    return Ref(kind="contract_clause", source=source_file, locator=f"§{clause}, p. {page}")


class Fact(BaseModel):
    key: str
    label: str
    value: Any
    unit: Optional[str] = None
    refs: List[Ref] = Field(default_factory=list)


PricingIntent = Literal["price_history", "detect_increases", "increase_impact", "compare_alternatives"]
ContractsIntent = Literal[
    "contract_terms", "increase_permission", "notice_deadline", "clause_lookup", "data_protection_status"
]
ClauseTopic = Literal["expiry_date", "non_renewal_notice", "price_review", "exit_terms", "data_protection", "annual_value"]


class PricingRequest(BaseModel):
    intent: PricingIntent
    supplier_id: Optional[str] = None  # None solo en detect_increases (todos)


class ContractsRequest(BaseModel):
    intent: ContractsIntent
    supplier_id: Optional[str] = None  # None solo en data_protection_status (todos)
    increase_pct: Optional[str] = None  # increase_permission: subida a evaluar (decimal en texto)
    topic: Optional[ClauseTopic] = None  # clause_lookup


class SpecialistResult(BaseModel):
    agent_id: str
    intent: str
    supplier_id: Optional[str] = None
    status: Literal["ok", "no_contract", "no_data", "not_found"] = "ok"
    facts: List[Fact] = Field(default_factory=list)
    findings: List[str] = Field(default_factory=list)  # frases ya redactadas, con sus cifras de `facts`
    missing: List[str] = Field(default_factory=list)  # datos/cláusulas que no existen (declarados)
    alerts: List[Dict[str, Any]] = Field(default_factory=list)

    def fact(self, key: str) -> Optional[Fact]:
        return next((f for f in self.facts if f.key == key), None)

    def refs(self) -> List[Ref]:
        seen, out = set(), []
        for fact in self.facts:
            for ref in fact.refs:
                if ref.label() not in seen:
                    seen.add(ref.label())
                    out.append(ref)
        return out


# --- Registro de acciones -------------------------------------------------------


def digest(text: str) -> str:
    """Huella de un texto que no debe guardarse (pregunta, prompt)."""
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def human_actor(user_id: Optional[str]) -> str:
    """Seudónimo estable de una persona: nunca su id ni su email en claro."""
    return "human:" + digest(user_id or "anonymous")[:12]


class ActionLog:
    """Acciones de una petición. Se escriben en `sc_agent_actions` al final
    (`flush`) y, en el momento, como una línea JSON en el logger."""

    def __init__(self, request_id: Optional[str] = None) -> None:
        self.request_id = request_id or uuid.uuid4().hex
        self.actions: List[ScAgentAction] = []

    def record(self, actor: str, action: str, outcome: str, tool: Optional[str] = None, **details: Any) -> None:
        safe = {k: v for k, v in details.items() if v is not None}
        self.actions.append(ScAgentAction(
            request_id=self.request_id, actor=actor, action=action, tool=tool, outcome=outcome, details=safe or None))
        logger.info(json.dumps({"request_id": self.request_id, "actor": actor, "action": action, "tool": tool,
                                "outcome": outcome, **safe}, default=str, sort_keys=True))

    def flush(self, session) -> None:
        from services.supplier_contracts import repository

        if self.actions:
            repository.add_actions(session, self.actions)
            self.actions = []


class ToolNotAllowed(PermissionError):
    pass


class AgentRuntime:
    def __init__(self, agent_id: str, log: ActionLog) -> None:
        if agent_id not in AGENT_TOOLS:
            raise ValueError(f"Agente desconocido: {agent_id}")
        self.agent_id = agent_id
        self.log = log

    @property
    def tools(self):
        return AGENT_TOOLS[self.agent_id]

    def call(self, tool: str, fn: Callable[..., Any], *args: Any, audit: Optional[Dict[str, Any]] = None, **kwargs: Any) -> Any:
        audit = audit or {}
        if tool not in self.tools:
            self.log.record(self.agent_id, "tool_call", "denied", tool=tool, **audit)
            raise ToolNotAllowed(f"{self.agent_id} no tiene la herramienta {tool}")
        try:
            result = fn(*args, **kwargs)
        except Exception as exc:
            self.log.record(self.agent_id, "tool_call", "error", tool=tool, error_type=type(exc).__name__, **audit)
            raise
        self.log.record(self.agent_id, "tool_call", "ok", tool=tool, **audit)
        return result
