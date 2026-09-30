"""Respuesta + auto-evaluación de memoria en UNA sola llamada al modelo.

El nodo `generate` del grafo ya llamaba al modelo para redactar la respuesta;
ahora le pide una salida estructurada (JSON) con la respuesta y, en el mismo
objeto, un campo `memory_proposal` (null casi siempre). No hay segunda
llamada, ni agente evaluador aparte: es el mismo agente con un campo más.

El prompt es el system prompt seguro del agente (`guardrails/prompt.py`,
Ticket #SEC-114: jerarquía de instrucciones, dominio, reglas de veracidad y de
negocio de `data/pipelines/rag.py` sin copiarlas) más MEMORY_RULES: el
criterio explícito, sacado del CONTEXT de HealthCore, de qué merece
proponerse y qué no. El contexto llega aislado en bloques `<fuente_externa>`.

Falla cerrado para la memoria: si la salida no es un JSON válido, el texto se
usa como respuesta y NO hay propuesta. Una propuesta mal formada (tipo fuera
de las tres familias, texto vacío) también se descarta.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List, Optional, Sequence

from pydantic import BaseModel, Field, ValidationError, field_validator

from services.agent.guardrails.monitor import MONITOR, SANITIZE, STRUCTURAL, GuardEvent
from services.agent.guardrails.prompt import build_agent_messages
from services.agent.memory.models import MEMORY_KINDS

logger = logging.getLogger(__name__)

MEMORY_RULES = """Memoria del asistente (autoevaluación obligatoria en cada respuesta):
El CONTEXTO puede incluir "Nota aprobada por el staff" (documento agent-memory): son hechos que \
otros miembros del staff pidieron recordar. Úsalas, pero si contradicen una política o un dato en \
vivo del CONTEXTO, prevalece la política o el dato en vivo y menciona la discrepancia.
Si el mensaje del coordinador no es una pregunta sino un dato o una corrección que te comunica, \
acúsalo de recibido en una o dos frases sin afirmar que sea correcto (no hace falta que esté en el \
CONTEXTO) y termina con "Fuente: información aportada por el coordinador".

Después de redactar la respuesta, decide si hay algo NUEVO o CORREGIDO que valga la pena recordar \
para futuras conversaciones. Propón memoria SOLO si el coordinador aporta uno de estos hechos:
- clinic_operations: un cambio o corrección operativa de una sede (horario, protocolo local de \
recepción o de derivaciones, excepción administrativa de EE. UU. o de R. U.).
- incident_pattern: un patrón de incidentes recurrente o su causa conocida, sin datos de pacientes.
- staff_preference: cómo quiere una persona del staff que se le presente la información operativa.
NO propongas memoria (memory_proposal = null) cuando:
- es una consulta puntual o un dato que vive en un dashboard, en el gestor de incidencias, en el \
inventario o en las políticas (eso ya se consulta en vivo);
- es un saludo, un agradecimiento o un cierre de conversación;
- el hecho ya está en una "Nota aprobada por el staff" del CONTEXTO con el mismo contenido;
- el mensaje menciona a un paciente concreto, información clínica, o cualquier identificador \
(nombre, historia clínica, fecha de nacimiento, seguro, diagnóstico). Nunca copies esos datos.
La mayoría de mensajes NO generan propuesta. En "answer" nunca digas que anotas, apuntas, \
registras, guardas o recuerdas algo (no tienes esa capacidad directa): tú solo propones y el \
sistema preguntará al coordinador. Si el mensaje trae datos de un paciente, no los repitas y \
di que no puedes registrar información de pacientes.
user_requested_memory = true solo si el coordinador te pide explícitamente que recuerdes, apuntes \
o guardes algo (aunque no sea memorizable).

Responde SOLO con un objeto JSON, sin texto alrededor:
{"answer": "<respuesta en texto plano para el coordinador>", \
"memory_proposal": null o {"kind": "clinic_operations|incident_pattern|staff_preference", \
"clinic": "<sede o null>", "content": "<el hecho en una frase autocontenida, sin datos de \
pacientes>", "reason": "<por qué merece recordarse>"}, "user_requested_memory": true|false}"""


class MemoryProposalDraft(BaseModel):
    kind: str
    clinic: Optional[str] = None
    content: str = Field(min_length=8, max_length=500)
    reason: Optional[str] = Field(default=None, max_length=300)

    @field_validator("kind")
    @classmethod
    def _known_kind(cls, value: str) -> str:
        if value not in MEMORY_KINDS:
            raise ValueError(f"kind must be one of {MEMORY_KINDS}")
        return value

    @field_validator("clinic")
    @classmethod
    def _blank_clinic(cls, value: Optional[str]) -> Optional[str]:
        value = (value or "").strip()
        if not value or value.lower() in {"null", "none"}:
            return None
        return value[:64]


class AgentReply(BaseModel):
    answer: str
    memory_proposal: Optional[MemoryProposalDraft] = None
    user_requested_memory: bool = False


def _strip_fences(raw: str) -> str:
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    return match.group(0) if match else raw


def parse_reply(raw: str) -> AgentReply:
    """Salida del modelo → AgentReply. Nunca lanza por una salida rara:
    como mucho se queda sin propuesta."""
    raw = (raw or "").strip()
    if not raw:
        raise RuntimeError("El modelo de generación devolvió una respuesta vacía")
    try:
        data: Dict[str, Any] = json.loads(_strip_fences(raw))
    except ValueError:
        logger.warning("Agent reply was not JSON; answering without memory proposal")
        MONITOR.record(GuardEvent("reply_structure", SANITIZE, STRUCTURAL, "reply_not_json"))
        return AgentReply(answer=raw)
    answer = str(data.get("answer") or "").strip()
    if not answer:
        raise RuntimeError("El modelo de generación devolvió una respuesta vacía")
    proposal = None
    if data.get("memory_proposal"):
        try:
            proposal = MemoryProposalDraft.model_validate(data["memory_proposal"])
        except ValidationError:
            logger.warning("Agent reply carried an invalid memory proposal; discarded")
            MONITOR.record(GuardEvent("reply_structure", SANITIZE, STRUCTURAL, "invalid_memory_proposal"))
    return AgentReply(
        answer=answer,
        memory_proposal=proposal,
        user_requested_memory=bool(data.get("user_requested_memory")),
    )


def build_reply_messages(question: str, context: Sequence[Dict[str, Any]]) -> List[Dict[str, str]]:
    return build_agent_messages(question, context, MEMORY_RULES)


def generate_reply(
    question: str,
    context: Sequence[Dict[str, Any]],
    *,
    client: Optional[Any] = None,
    model: Optional[str] = None,
) -> AgentReply:
    from data.pipelines import rag
    from data.process.rag import get_llm_client

    llm = client or get_llm_client()
    completion = llm.chat.completions.create(
        model=model or rag.get_generation_model(),
        messages=build_reply_messages(question, context),
        temperature=0.2,
        response_format={"type": "json_object"},
    )
    return parse_reply(completion.choices[0].message.content or "")
