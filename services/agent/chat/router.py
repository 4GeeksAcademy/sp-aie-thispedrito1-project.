"""Chat en tiempo real del Compliance assistant por WebSocket (Tiempo Real, Parte 2).

HTTP (con el JWT de siempre en `Authorization`):
- `POST /agent/chat/sessions`: abre una ChatSession nueva para quien llama.
- `GET /agent/chat/sessions`: sus sesiones, la más reciente primero.
- `POST /agent/chat/sessions/{session_id}/close`: la cierra (y corta la
  respuesta en curso, si la hay).

WebSocket `GET /agent/chat/ws/{session_id}`: el socket queda ligado a ESA
sesión (el `session_id` va en la URL). Autenticación con el mismo JWT que el
backoffice, de dos formas (el navegador no puede poner `Authorization` en el
handshake):
- `?token=<jwt>`: se valida ANTES de aceptar; si no vale, el handshake se
  rechaza (HTTP 403) sin llegar a abrir el socket. Ojo: uvicorn escribe la
  URL completa en su log de acceso, token incluido.
- Primer frame `{"event": "auth", "data": {"token": ...}}` (lo que usa el
  backoffice, para que el token no acabe en ningún log): el socket se acepta,
  pero hasta recibir un token válido no se envía ni un evento de chat. Sin
  él en 5 s, o con uno inválido, se cierra con 4401.

Tras autenticar: solo el dueño de la sesión puede escribir; un admin puede
conectarse a la misma sesión como supervisor (solo lectura) y recibe los
mismos eventos sin que el agente se ejecute dos veces (pub/sub, hub.py).
Lo primero que recibe cualquier socket es `session_snapshot`: el historial
guardado + la respuesta que se esté generando en ese momento. Así un
reconectar con el mismo `session_id` restaura el hilo, no un chat vacío.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, List, Optional, Tuple

import anyio.from_thread
from fastapi import APIRouter, Depends, HTTPException, Query, WebSocket, status
from sqlmodel import Session

from database import get_inventory_db
from rate_limit import check_llm_quota, rate_limit_message
from security import get_current_user, user_from_token
from services.agent.chat import events
from services.agent.chat.hub import SlowConsumer, Subscription
from services.agent.chat.models import SESSION_CLOSED
from services.agent.chat.schemas import ChatSessionOut
from services.agent.chat.service import CHAT, ChatService, SessionFactory, TurnDeps, default_session_factory
from services.agent.chat.store import ChatStore, message_out
from services.agent.router import get_agent, get_decision_classifier

logger = logging.getLogger("healthcore.agent.chat")

router = APIRouter(prefix="/agent/chat", tags=["agent-chat"])

AUTH_TIMEOUT_S = 5.0


def get_chat_session_factory() -> SessionFactory:
    """Dependencia sustituible en tests por sesiones contra SQLite."""
    return default_session_factory


def get_chat_service() -> ChatService:
    return CHAT


# --- HTTP -------------------------------------------------------------------------


@router.post("/sessions", response_model=ChatSessionOut, status_code=status.HTTP_201_CREATED)
def create_chat_session(
    user: Dict[str, Any] = Depends(get_current_user), db: Session = Depends(get_inventory_db)
) -> Any:
    return ChatStore(db).create_session(str(user["id"]))


@router.get("/sessions", response_model=List[ChatSessionOut])
def list_chat_sessions(
    user: Dict[str, Any] = Depends(get_current_user), db: Session = Depends(get_inventory_db)
) -> List[Any]:
    return ChatStore(db).list_sessions(str(user["id"]))


@router.post("/sessions/{session_id}/close", response_model=ChatSessionOut)
def close_chat_session(
    session_id: str,
    user: Dict[str, Any] = Depends(get_current_user),
    db: Session = Depends(get_inventory_db),
    session_factory: SessionFactory = Depends(get_chat_session_factory),
    service: ChatService = Depends(get_chat_service),
) -> Any:
    store = ChatStore(db)
    chat = store.get_session(session_id)
    if chat is None or chat.user_id != str(user["id"]):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Chat session not found.")
    # Las rutas síncronas corren en un hilo de anyio: desde ahí se puede
    # esperar a la corrutina que corta la respuesta en curso.
    anyio.from_thread.run(service.close, session_id, session_factory)
    store.set_status(session_id, SESSION_CLOSED)
    db.refresh(chat)
    return chat


# --- WebSocket --------------------------------------------------------------------


def _load_session(session_factory: SessionFactory, session_id: str) -> Optional[Tuple[str, str, List[Dict[str, Any]]]]:
    """(user_id, status, historial) de la sesión, o None si no existe.
    Lanza RuntimeError si no hay base de datos."""
    db = session_factory()
    if db is None:
        raise RuntimeError("Chat history store is not configured")
    try:
        store = ChatStore(db)
        chat = store.get_session(session_id)
        if chat is None:
            return None
        return chat.user_id, chat.status, [message_out(m) for m in store.history(session_id)]
    finally:
        db.close()


async def _authenticate_first_frame(websocket: WebSocket, session_id: str) -> Optional[Dict[str, Any]]:
    try:
        message = await asyncio.wait_for(websocket.receive(), AUTH_TIMEOUT_S)
    except asyncio.TimeoutError:
        return None
    if message.get("type") != "websocket.receive" or not message.get("text"):
        return None
    try:
        event = events.parse_client_event(message["text"], session_id)
    except events.ClientEventError:
        return None
    if event.event != events.AUTH:
        return None
    return user_from_token(event.token)


async def _pump(websocket: WebSocket, subscription: Subscription, in_progress: Optional[Dict[str, Any]]) -> None:
    """Cola de este socket → navegador. El único que escribe en el socket."""
    skip_message = in_progress["message_id"] if in_progress else None
    skip_upto = in_progress["sequence"] if in_progress else 0
    while True:
        event = await subscription.get()
        data = event["data"]
        # Tokens que ya venían dentro del snapshot: no repetirlos.
        if event["event"] == events.TOKEN_CHUNK and data["message_id"] == skip_message and data["sequence"] <= skip_upto:
            continue
        await websocket.send_json(event)


async def _receive(
    websocket: WebSocket,
    session_id: str,
    subscription: Subscription,
    service: ChatService,
    deps: TurnDeps,
    *,
    can_write: bool,
) -> None:
    """Navegador → productor. Los errores van a la cola de ESTE socket."""

    def reply_error(code: str, message: str) -> None:
        subscription.deliver(events.chat_error(session_id, code, message))

    while True:
        message = await websocket.receive()
        if message["type"] == "websocket.disconnect":
            return
        raw = message.get("text")
        if raw is None:
            reply_error("invalid_frame", "Solo se aceptan mensajes de texto JSON.")
            continue
        try:
            event = events.parse_client_event(raw, session_id)
        except events.ClientEventError as exc:
            reply_error(exc.code, str(exc))
            continue
        if event.event == events.AUTH:
            continue  # ya autenticado
        if not can_write:
            reply_error("read_only", "Estás observando esta conversación: solo su autor puede escribir.")
            continue
        if service.runtime(session_id).closed:
            reply_error("session_closed", "Esta conversación está cerrada. Abre una nueva.")
            continue
        # Rate limiting (clase NIST): el mismo cupo por usuario que los endpoints
        # HTTP. Solo cuentan los mensajes que pondrían en marcha al agente.
        quota = check_llm_quota(deps.user_id)
        if not quota.allowed:
            reply_error("rate_limited", rate_limit_message(quota.retry_after_s))
            continue
        try:
            await service.submit(session_id, event.text, deps)
        except RuntimeError:
            reply_error("unavailable", "No se pudo guardar el mensaje. Inténtalo de nuevo en unos segundos.")


@router.websocket("/ws/{session_id}")
async def chat_socket(
    websocket: WebSocket,
    session_id: str,
    token: Optional[str] = Query(default=None),
    agent: Any = Depends(get_agent),
    classify_fn: Any = Depends(get_decision_classifier),
    session_factory: SessionFactory = Depends(get_chat_session_factory),
    service: ChatService = Depends(get_chat_service),
) -> None:
    if token is not None:
        user = user_from_token(token)
        if user is None:
            await websocket.close(code=events.CLOSE_UNAUTHENTICATED)  # antes de aceptar: handshake rechazado
            return
        await websocket.accept()
    else:
        await websocket.accept()
        user = await _authenticate_first_frame(websocket, session_id)
        if user is None:
            await websocket.close(code=events.CLOSE_UNAUTHENTICATED)
            return

    try:
        loaded = await asyncio.to_thread(_load_session, session_factory, session_id)
    except Exception as exc:
        logger.warning("Chat %s: history store unavailable (%s)", session_id, type(exc).__name__)
        await websocket.close(code=events.CLOSE_UNAVAILABLE)
        return
    if loaded is None:
        await websocket.close(code=events.CLOSE_SESSION_NOT_FOUND)
        return
    owner_id, session_status, history = loaded
    user_id = str(user["id"])
    is_owner = owner_id == user_id
    if not is_owner and user.get("role") != "admin":
        await websocket.close(code=events.CLOSE_FORBIDDEN)
        return
    if session_status == SESSION_CLOSED:
        await websocket.close(code=events.CLOSE_SESSION_CLOSED)
        return

    # Suscribirse ANTES de leer el estado en curso: lo que se publique entre
    # medias queda en la cola y `_pump` descarta lo que ya trae el snapshot.
    subscription = service.hub.subscribe(session_id)
    try:
        in_progress = service.in_progress(session_id)
        await websocket.send_json(events.session_snapshot(session_id, session_status, history, in_progress))
        deps = TurnDeps(agent=agent, classify_fn=classify_fn, session_factory=session_factory, user_id=user_id)
        pump = asyncio.ensure_future(_pump(websocket, subscription, in_progress))
        receive = asyncio.ensure_future(
            _receive(websocket, session_id, subscription, service, deps, can_write=is_owner)
        )
        done, pending = await asyncio.wait({pump, receive}, return_when=asyncio.FIRST_COMPLETED)
        for task in pending:
            task.cancel()
        for task in done:
            error = task.exception()
            if isinstance(error, SlowConsumer):
                await websocket.close(code=events.CLOSE_SLOW_CONSUMER)
            elif error is not None and not _is_disconnect(error):
                logger.warning("Chat %s: socket ended with %s", session_id, type(error).__name__)
    finally:
        subscription.close()


def _is_disconnect(error: BaseException) -> bool:
    from starlette.websockets import WebSocketDisconnect

    return isinstance(error, (WebSocketDisconnect, RuntimeError))
