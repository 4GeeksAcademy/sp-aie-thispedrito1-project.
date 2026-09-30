"""Aislamiento del contenido externo (Ticket #SEC-114, anti-inyección indirecta).

"Externo" = todo lo que no escribió HealthCore en el system prompt y llega al
modelo como contexto:
- chunks del RAG (`healthcore_knowledge`): hoy son políticas propias, pero la
  colección puede recargarse con documentos nuevos;
- datos en vivo de las tools (incidencias e inventario vía MCP / API): el
  nombre de un insumo o una sede los teclea una persona;
- notas de memoria aprobadas por el staff (Ticket #MEM-092): texto de usuario
  que sobrevive entre sesiones. Un recuerdo envenenado es justo el riesgo que
  el README de la clase señala.

Dos defensas, en este orden:

1. `sanitize_text`: neutraliza lo que parezca una instrucción (las MISMAS
   reglas de jailbreak que la entrada, `patterns.JAILBREAK_RULES`) y elimina
   marcadores que imiten la estructura del prompt (`</fuente_externa>`,
   `system:`, `[INST]`...). Así un documento no puede "cerrar" su bloque y
   escribir fuera de él.
2. `render_context`: envuelve cada fragmento en `<fuente_externa>` con su
   origen. El system prompt declara que ese bloque son DATOS: se citan, nunca
   se obedecen (`prompt.INSTRUCTION_HIERARCHY`).

Cada neutralización se registra en el monitor (acción `sanitize`, tipo
`security`) con el documento de origen, nunca con el texto.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Sequence

from services.agent.guardrails import patterns
from services.agent.guardrails.monitor import MONITOR, SANITIZE, SECURITY, GuardEvent, GuardrailMonitor

NEUTRALIZED = "[texto neutralizado: parecía una instrucción]"
NO_CONTEXT = "(sin fuentes: ningún fragmento de la base de conocimiento ni dato en vivo es relevante)"
MAX_FRAGMENT_CHARS = 4000

# Marcadores de estructura del prompt que un texto externo nunca debe traer.
_STRUCTURE_MARKERS = re.compile(
    r"</?\s*(fuente_externa|mensaje_usuario|system|sistema|instrucciones)[^>]*>"
    r"|\[/?(INST|SYSTEM)\]|<\|im_(start|end)\|>",
    re.IGNORECASE,
)
_ROLE_PREFIX = re.compile(r"(?im)^\s*(system|sistema|developer|assistant)\s*:\s*")
# Frases separadas por fin de oración o salto de línea.
_SENTENCE = re.compile(r"[^.!?\n]+[.!?]?|\n")


def _is_instruction(sentence: str) -> str:
    return patterns.first_match(patterns.JAILBREAK_RULES, patterns.normalize(sentence))


def sanitize_text(text: str) -> "tuple[str, List[str]]":
    """Texto externo → (texto seguro, motivos de lo neutralizado)."""
    reasons: List[str] = []
    cleaned = (text or "")[:MAX_FRAGMENT_CHARS]
    if _STRUCTURE_MARKERS.search(cleaned) or _ROLE_PREFIX.search(cleaned):
        reasons.append("structure_marker")
        cleaned = _STRUCTURE_MARKERS.sub(" ", cleaned)
        cleaned = _ROLE_PREFIX.sub("", cleaned)
    parts = []
    for match in _SENTENCE.finditer(cleaned):
        sentence = match.group(0)
        reason = _is_instruction(sentence) if sentence.strip() else ""
        if reason:
            reasons.append(reason)
            parts.append(" " + NEUTRALIZED)
        else:
            parts.append(sentence)
    return "".join(parts).strip(), reasons


def isolate_evidence(
    items: Sequence[Dict[str, Any]],
    *,
    monitor: Optional[GuardrailMonitor] = None,
) -> List[Dict[str, Any]]:
    """Copia de los fragmentos con `text` y `section` saneados."""
    monitor = monitor or MONITOR
    isolated = []
    for item in items:
        text, text_reasons = sanitize_text(str(item.get("text") or ""))
        section, section_reasons = sanitize_text(str(item.get("section") or ""))
        for reason in text_reasons + section_reasons:
            monitor.record(GuardEvent("context_isolation", SANITIZE, SECURITY, reason))
        isolated.append({**item, "text": text, "section": section})
    return isolated


def render_context(items: Sequence[Dict[str, Any]]) -> str:
    """Bloque de contexto para el mensaje al modelo. Espera fragmentos ya
    pasados por `isolate_evidence`."""
    if not items:
        return NO_CONTEXT
    blocks = []
    for index, item in enumerate(items, start=1):
        source = str(item.get("source_document") or "desconocido").replace('"', "'")
        section = str(item.get("section") or "").replace('"', "'")
        blocks.append(
            f'<fuente_externa n="{index}" documento="{source}" seccion="{section}">\n'
            f"{item.get('text', '')}\n"
            "</fuente_externa>"
        )
    return "\n\n".join(blocks)


def render_user_message(message: str) -> str:
    """El mensaje del usuario también va delimitado, y sin marcadores que
    pudieran cerrar su bloque antes de tiempo."""
    safe = _STRUCTURE_MARKERS.sub(" ", message or "").strip()
    return f"<mensaje_usuario>\n{safe}\n</mensaje_usuario>"
