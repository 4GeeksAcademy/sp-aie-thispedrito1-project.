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

Streaming (Sistemas en Tiempo Real, Parte 2): con un `AnswerStream`, la
misma llamada se hace con `stream=True`. `AnswerDeltaExtractor` saca del JSON
a medio llegar solo el texto de `answer` (el usuario nunca ve `{"answer":`),
y entre trozo y trozo se comprueba la orden de parar: si llega, se cierra la
conexión con el modelo (abort real, no ignorar lo que siga llegando). La
respuesta final pasa por el mismo `parse_reply`, con o sin streaming.
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
from services.agent.streaming import AnswerStream

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


_ANSWER_KEY = re.compile(r'"answer"\s*:\s*"')
_SIMPLE_ESCAPES = {'"': '"', "\\": "\\", "/": "/", "b": "\b", "f": "\f", "n": "\n", "r": "\r", "t": "\t"}


class AnswerDeltaExtractor:
    """JSON del modelo llegando a trozos → texto nuevo del campo `answer`.

    Solo sigue ese campo: lo demás (`memory_proposal`...) se ignora aquí y lo
    lee `parse_reply` al final. Un escape partido entre dos trozos (`\\` en
    uno y `n` en el siguiente, o un `\\u00e9` a medias) espera al siguiente
    trozo en vez de emitirse roto. Si el modelo no devuelve JSON, no emite
    nada y la respuesta llega entera al terminar."""

    def __init__(self) -> None:
        self._pending = ""  # lo recibido y aún no interpretado
        self._inside = False  # ya dentro del valor de "answer"
        self._done = False
        self._high_surrogate: Optional[int] = None

    def feed(self, chunk: str) -> str:
        if self._done or not chunk:
            return ""
        self._pending += chunk
        if not self._inside:
            match = _ANSWER_KEY.search(self._pending)
            if match is None:
                return ""
            self._inside = True
            self._pending = self._pending[match.end():]
        return self._consume()

    def _consume(self) -> str:
        out: List[str] = []
        text = self._pending
        i = 0
        while i < len(text):
            char = text[i]
            if char == '"':
                self._done = True
                self._pending = ""
                return "".join(out)
            if char != "\\":
                out.append(char)
                i += 1
                continue
            if i + 1 >= len(text):
                break  # escape partido: esperar al siguiente trozo
            code = text[i + 1]
            if code != "u":
                out.append(_SIMPLE_ESCAPES.get(code, code))
                i += 2
                continue
            if i + 6 > len(text):
                break
            try:
                value = int(text[i + 2 : i + 6], 16)
            except ValueError:
                value = 0xFFFD
            i += 6
            if 0xD800 <= value <= 0xDBFF:  # primera mitad de un emoji: esperar la segunda
                self._high_surrogate = value
                continue
            if 0xDC00 <= value <= 0xDFFF and self._high_surrogate is not None:
                value = 0x10000 + ((self._high_surrogate - 0xD800) << 10) + (value - 0xDC00)
            self._high_surrogate = None
            out.append(chr(value))
        self._pending = text[i:]
        return "".join(out)


def _stream_reply(llm: Any, request: Dict[str, Any], answer_stream: AnswerStream) -> AgentReply:
    answer_stream.check()  # parada pedida antes de empezar: ni se llama al modelo
    # Sin `response_format=json_object` en streaming. Medido con deepseek a
    # través del proxy de 4Geeks (2026-10-28): en modo JSON los 93 trozos
    # llegaron en 0,1 s, todos al final (la salida se retiene hasta validarla);
    # sin él, 21 llegadas repartidas. El prompt ya exige el JSON (MEMORY_RULES)
    # y `parse_reply` ya cubre una salida que no lo sea: respuesta entera al
    # final y sin propuesta de memoria.
    request = {key: value for key, value in request.items() if key != "response_format"}
    stream = llm.chat.completions.create(**request, stream=True)
    extractor = AnswerDeltaExtractor()
    raw: List[str] = []
    try:
        for chunk in stream:
            answer_stream.check()
            choices = getattr(chunk, "choices", None) or []
            delta = getattr(choices[0].delta, "content", None) if choices else None
            if not delta:
                continue
            raw.append(delta)
            text = extractor.feed(delta)
            if text:
                answer_stream.emit(text)
    finally:
        # Con o sin abort: cerrar la respuesta HTTP corta la generación en el
        # proveedor en vez de dejarla terminar en segundo plano.
        close = getattr(stream, "close", None)
        if callable(close):
            close()
    return parse_reply("".join(raw))


def generate_reply(
    question: str,
    context: Sequence[Dict[str, Any]],
    *,
    client: Optional[Any] = None,
    model: Optional[str] = None,
    answer_stream: Optional[AnswerStream] = None,
) -> AgentReply:
    from data.pipelines import rag
    from data.process.rag import get_llm_client

    llm = client or get_llm_client()
    request = {
        "model": model or rag.get_generation_model(),
        "messages": build_reply_messages(question, context),
        "temperature": 0.2,
        "response_format": {"type": "json_object"},
    }
    if answer_stream is not None:
        return _stream_reply(llm, request, answer_stream)
    completion = llm.chat.completions.create(**request)
    return parse_reply(completion.choices[0].message.content or "")
