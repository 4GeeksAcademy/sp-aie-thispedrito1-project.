"""Productor del chat: turnos del agente, streaming de tokens y abort.

Un `SessionRuntime` por sesión con, como mucho, UNA generación viva. Cada
mensaje del usuario (`submit`) sigue siempre el mismo orden:

1. Si hay una generación en curso, se interrumpe: se activa su `stop` (el
   nodo `generate` lo comprueba entre trozos del modelo y cierra la conexión
   con el proveedor), se guarda lo ya mostrado como mensaje `interrupted` y
   se publica `generation_interrupted`.
2. Se guarda el mensaje del usuario (sin texto si trae datos de un paciente)
   y se publica su eco `user_message`.
3. Arranca un turno NUEVO del asistente, con su propio `message_id`: el
   mensaje interrumpido nunca se sobrescribe.

El turno es el de siempre: `memory.conversation.handle_turn` (guardarraíles,
memoria y grafo), sin tocar su lógica. Solo cambia el `run_fn` que recibe:
`run_agent` con `on_token` y `should_stop` (ver tracing.py y streaming.py).

La puerta de los tokens: cada `token_chunk` se publica bajo `runtime.lock`
y solo si su generación sigue siendo la actual. La interrupción quita la
generación actual con ese mismo candado ANTES de publicar
`generation_interrupted`, y el hub entrega en orden de publicación: después
del aviso de interrupción no puede llegar ni un token de esa generación.

Guardarraíl de salida en streaming (decisión del usuario): ningún texto se
publica sin pasar antes `check_output` sobre todo lo acumulado. Se libera
por palabras completas (`split_ready_text`); si el guardarraíl salta a
mitad, se aborta la generación y el mensaje se sustituye por el texto fijo
del guardarraíl (`generation_blocked`), igual que en POST /agent/query.
"""

from __future__ import annotations

import asyncio
import logging
import re
import threading
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from sqlmodel import Session

from services.agent.chat import events
from services.agent.chat.hub import HUB, ChatHub
from services.agent.chat.models import (
    MESSAGE_BLOCKED,
    MESSAGE_COMPLETED,
    MESSAGE_FAILED,
    MESSAGE_INTERRUPTED,
    ROLE_ASSISTANT,
    SESSION_ACTIVE,
    SESSION_INTERRUPTED,
)
from services.agent.chat.store import ChatStore, new_message_id, utcnow
from services.agent.guardrails.monitor import MONITOR
from services.agent.guardrails.output_guard import OutputVerdict, check_output
from services.agent.memory.conversation import OUTCOME_GUARDRAIL_BLOCKED, handle_turn
from services.agent.memory.store import MemoryStore
from services.agent.streaming import GenerationAborted
from services.agent.tracing import AgentRunError, run_agent

logger = logging.getLogger("healthcore.agent.chat")

SessionFactory = Callable[[], Optional[Session]]

FAILED_TEXT = (
    "No he podido responder ahora mismo: el asistente no está disponible. "
    "Inténtalo de nuevo en unos segundos."
)

_WORDS = re.compile(r"\S+\s*")


def default_session_factory() -> Optional[Session]:
    """Una sesión de base de datos nueva por operación (cada turno corre en su
    hilo). None si Supabase no está configurado."""
    from database import get_inventory_engine  # services/api: importado al usarse

    try:
        engine = get_inventory_engine()
    except RuntimeError:
        return None
    return Session(engine)


def split_ready_text(pending: str) -> Tuple[str, str]:
    """Del texto recibido y aún no mostrado, ¿qué parte se puede publicar ya?

    Devuelve (listo, resto). Solo palabras completas: el guardarraíl revisa
    todo lo acumulado antes de cada publicación, y una palabra a medias
    ("Joh" de un nombre) todavía no se puede juzgar."""
    # Learn by Doing delegado: corte en el último espacio o salto de línea.
    cut = max(pending.rfind(" "), pending.rfind("\n"))
    if cut < 0:
        return "", pending
    return pending[: cut + 1], pending[cut + 1 :]


def word_pieces(text: str) -> List[str]:
    """Texto ya validado (fijo o completo) → trozos de una palabra."""
    return _WORDS.findall(text) or ([text] if text else [])


@dataclass
class Generation:
    message_id: str
    replies_to: str
    question: str
    started_at: datetime
    stop: threading.Event = field(default_factory=threading.Event)
    text: str = ""  # lo ya publicado (lo que el usuario ha visto)
    pending: str = ""  # recibido del modelo y aún sin publicar
    sequence: int = 0
    blocked: Optional[OutputVerdict] = None


@dataclass(frozen=True)
class TurnDeps:
    agent: Any
    classify_fn: Any
    session_factory: SessionFactory
    user_id: str


class SessionRuntime:
    def __init__(self, session_id: str) -> None:
        self.session_id = session_id
        self.lock = threading.Lock()  # protege `current` y el estado de la generación
        self.current: Optional[Generation] = None
        self.closed = False
        self.tasks: Set["asyncio.Future[Any]"] = set()
        self._submit_lock: Optional[asyncio.Lock] = None

    @property
    def submit_lock(self) -> asyncio.Lock:
        # Dos pestañas del mismo usuario enviando a la vez: un turno detrás de otro.
        if self._submit_lock is None:
            self._submit_lock = asyncio.Lock()
        return self._submit_lock


class ChatService:
    def __init__(self, hub: ChatHub = HUB, run_fn: Callable[..., Any] = run_agent) -> None:
        self.hub = hub
        self.run_fn = run_fn
        self._runtimes: Dict[str, SessionRuntime] = {}
        self._lock = threading.Lock()

    # --- Estado que leen los consumidores ---------------------------------

    def runtime(self, session_id: str) -> SessionRuntime:
        with self._lock:
            runtime = self._runtimes.get(session_id)
            if runtime is None:
                runtime = self._runtimes[session_id] = SessionRuntime(session_id)
            return runtime

    def in_progress(self, session_id: str) -> Optional[Dict[str, Any]]:
        """La respuesta que se está generando ahora mismo (para el snapshot de
        quien reconecta): lo ya publicado y el último `sequence`."""
        runtime = self.runtime(session_id)
        with runtime.lock:
            gen = runtime.current
            if gen is None:
                return None
            return {
                "message_id": gen.message_id,
                "replies_to": gen.replies_to,
                "text": gen.text,
                "sequence": gen.sequence,
            }

    # --- Entrada del usuario -------------------------------------------------

    async def submit(self, session_id: str, text: str, deps: TurnDeps) -> None:
        """Un mensaje del usuario: interrumpe lo que esté en curso y abre un turno nuevo."""
        runtime = self.runtime(session_id)
        async with runtime.submit_lock:
            await self._interrupt(runtime, deps.session_factory)
            user = await asyncio.to_thread(self._save_user_message, deps.session_factory, session_id, text)
            self.hub.publish(session_id, events.user_message(session_id, user["message_id"], user["text"], user["status"]))
            gen = Generation(
                message_id=new_message_id(), replies_to=user["message_id"], question=text, started_at=utcnow()
            )
            with runtime.lock:
                runtime.current = gen
            task = asyncio.ensure_future(asyncio.to_thread(self._run_turn, runtime, gen, deps))
            runtime.tasks.add(task)
            task.add_done_callback(runtime.tasks.discard)

    async def interrupt(self, session_id: str, session_factory: SessionFactory) -> Optional[str]:
        """Interrumpe sin turno nuevo (cerrar la sesión). Devuelve el mensaje cortado."""
        return await self._interrupt(self.runtime(session_id), session_factory)

    async def close(self, session_id: str, session_factory: SessionFactory) -> None:
        runtime = self.runtime(session_id)
        runtime.closed = True
        await self._interrupt(runtime, session_factory)

    def stop_all(self) -> None:
        """Apagado de la API: que ningún hilo siga pidiendo tokens al proveedor."""
        with self._lock:
            runtimes = list(self._runtimes.values())
        for runtime in runtimes:
            with runtime.lock:
                if runtime.current is not None:
                    runtime.current.stop.set()

    async def wait_idle(self, session_id: str) -> None:
        """Espera a que terminen los hilos de la sesión (tests y apagado ordenado)."""
        tasks = list(self.runtime(session_id).tasks)
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _interrupt(self, runtime: SessionRuntime, session_factory: SessionFactory) -> Optional[str]:
        with runtime.lock:
            gen = runtime.current
            if gen is None:
                return None
            gen.stop.set()  # el nodo generate lo ve en el siguiente trozo y corta
            runtime.current = None  # puerta cerrada: ningún token más de esta generación
            partial = gen.text
        await asyncio.to_thread(
            self._persist_assistant,
            session_factory,
            runtime.session_id,
            gen,
            partial,
            MESSAGE_INTERRUPTED,
            None,
            SESSION_INTERRUPTED,
        )
        self.hub.publish(runtime.session_id, events.generation_interrupted(runtime.session_id, gen.message_id))
        logger.info("Chat %s: generation %s interrupted after %d chunks", runtime.session_id, gen.message_id, gen.sequence)
        return gen.message_id

    # --- El turno (hilo aparte) ---------------------------------------------

    def _run_turn(self, runtime: SessionRuntime, gen: Generation, deps: TurnDeps) -> None:
        db = deps.session_factory()
        try:
            store = MemoryStore(db) if db is not None else None

            def streaming_run(agent: Any, question: str, *, memories: Any) -> Any:
                result = self.run_fn(
                    agent,
                    question,
                    memories=memories,
                    on_token=lambda delta: self._on_delta(runtime, gen, delta),
                    should_stop=gen.stop.is_set,
                )
                # La corrida pudo terminar justo cuando llegaba la interrupción:
                # handle_turn no debe ofrecer memoria de una respuesta cortada.
                if gen.stop.is_set():
                    raise AgentRunError(result.trace_id, None, GenerationAborted("stopped after the run"))
                return result

            try:
                result = handle_turn(
                    deps.agent,
                    gen.question,
                    user_id=deps.user_id,
                    store=store,
                    classify_fn=deps.classify_fn,
                    run_fn=streaming_run,
                )
            except AgentRunError as exc:
                if isinstance(exc.cause, GenerationAborted):
                    if gen.blocked is not None:
                        self._finish_blocked(runtime, gen, deps, exc.trace_id)
                    return  # interrupción: quien interrumpió ya guardó y avisó
                logger.warning("Chat %s: agent failed at node %s", runtime.session_id, exc.node)
                self._finish(runtime, gen, deps, FAILED_TEXT, MESSAGE_FAILED, exc.trace_id, None)
                return
            except Exception as exc:  # nunca dejar la generación colgada
                logger.error("Chat %s: unexpected turn error (%s)", runtime.session_id, type(exc).__name__)
                self._finish(runtime, gen, deps, FAILED_TEXT, MESSAGE_FAILED, None, None)
                return
            self._complete(runtime, gen, deps, result)
        finally:
            if db is not None:
                db.close()

    def _on_delta(self, runtime: SessionRuntime, gen: Generation, delta: str) -> None:
        with runtime.lock:
            if runtime.current is not gen or gen.stop.is_set():
                return
            gen.pending += delta
            ready, gen.pending = split_ready_text(gen.pending)
            if ready:
                self._publish_checked(runtime, gen, ready)

    def _publish_checked(self, runtime: SessionRuntime, gen: Generation, ready: str) -> None:
        """Bajo runtime.lock. Publica `ready` solo si todo lo acumulado pasa el guardarraíl."""
        candidate = gen.text + ready
        if candidate.strip():
            verdict = check_output(candidate)
            if verdict.blocked:
                gen.blocked = verdict
                gen.stop.set()
                return
        self._publish_token(runtime, gen, ready)

    def _publish_token(self, runtime: SessionRuntime, gen: Generation, token: str) -> None:
        gen.text += token
        gen.sequence += 1
        self.hub.publish(runtime.session_id, events.token_chunk(runtime.session_id, gen.message_id, token, gen.sequence))

    def _complete(self, runtime: SessionRuntime, gen: Generation, deps: TurnDeps, result: Any) -> None:
        final = result.answer or ""
        with runtime.lock:
            if runtime.current is not gen:
                return  # interrumpida mientras terminaba: ya está guardada como interrupted
            if gen.pending:
                ready, gen.pending = gen.pending, ""
                self._publish_checked(runtime, gen, ready)
            if gen.blocked is None:
                if not gen.text.strip():
                    # Nada streameado: textos fijos (guardarraíl, sin información,
                    # decisión de memoria) o modo general. Ya están validados.
                    for piece in word_pieces(final):
                        self._publish_token(runtime, gen, piece)
                elif final.startswith(gen.text) and len(final) > len(gen.text):
                    # La nota de memoria que el agente añade al final.
                    for piece in word_pieces(final[len(gen.text):]):
                        self._publish_token(runtime, gen, piece)
        if gen.blocked is not None:
            self._finish_blocked(runtime, gen, deps, result.trace_id)
            return
        if result.outcome == OUTCOME_GUARDRAIL_BLOCKED and gen.text and not final.startswith(gen.text):
            # El guardarraíl de salida de handle_turn retuvo la respuesta completa.
            self._finish(runtime, gen, deps, final, MESSAGE_BLOCKED, result.trace_id, None)
            return
        self._finish(runtime, gen, deps, final, MESSAGE_COMPLETED, result.trace_id, result.outcome)

    def _finish_blocked(self, runtime: SessionRuntime, gen: Generation, deps: TurnDeps, trace_id: Optional[str]) -> None:
        verdict = gen.blocked
        assert verdict is not None
        MONITOR.record(verdict.event, user_id=deps.user_id, trace_id=trace_id)
        self._finish(runtime, gen, deps, verdict.answer, MESSAGE_BLOCKED, trace_id, None)

    def _finish(
        self,
        runtime: SessionRuntime,
        gen: Generation,
        deps: TurnDeps,
        text: str,
        status: str,
        trace_id: Optional[str],
        outcome: Optional[str],
    ) -> None:
        with runtime.lock:
            if runtime.current is not gen:
                return
            runtime.current = None
        self._persist_assistant(deps.session_factory, runtime.session_id, gen, text, status, trace_id, SESSION_ACTIVE)
        sid = runtime.session_id
        if status == MESSAGE_BLOCKED:
            event = events.generation_blocked(sid, gen.message_id, text)
        elif status == MESSAGE_FAILED:
            event = events.generation_failed(sid, gen.message_id, text)
        else:
            event = events.generation_completed(sid, gen.message_id, text, outcome)
        self.hub.publish(sid, event)

    # --- Persistencia ----------------------------------------------------------

    @staticmethod
    def _save_user_message(session_factory: SessionFactory, session_id: str, text: str) -> Dict[str, Any]:
        db = session_factory()
        if db is None:
            raise RuntimeError("Chat history store is not configured")
        try:
            message = ChatStore(db).add_user_message(session_id, text)
            return {"message_id": message.message_id, "text": message.text, "status": message.status}
        finally:
            db.close()

    @staticmethod
    def _persist_assistant(
        session_factory: SessionFactory,
        session_id: str,
        gen: Generation,
        text: str,
        status: str,
        trace_id: Optional[str],
        session_status: str,
    ) -> None:
        db = session_factory()
        if db is None:
            logger.warning("Chat %s: history store unavailable, message %s not saved", session_id, gen.message_id)
            return
        try:
            store = ChatStore(db)
            store.add_message(
                session_id=session_id,
                role=ROLE_ASSISTANT,
                text=text,
                status=status,
                message_id=gen.message_id,
                replies_to=gen.replies_to,
                trace_id=trace_id,
                created_at=gen.started_at,
            )
            store.set_status(session_id, session_status)
        except Exception as exc:  # la respuesta ya se mostró: no tumbar el turno
            logger.error("Chat %s: message %s not saved (%s)", session_id, gen.message_id, type(exc).__name__)
            db.rollback()
        finally:
            db.close()


CHAT = ChatService()
