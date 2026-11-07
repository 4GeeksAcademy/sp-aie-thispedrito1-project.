"use client";

import { useCallback, useEffect, useReducer, useRef, useState } from "react";

import { redirectToLogin } from "../services/http";
import { getAuthToken } from "../services/session";
import {
  CLOSE_UNAUTHENTICATED,
  FINAL_CLOSE_CODES,
  INITIAL_CHAT_STATE,
  applyChatEvent,
  authFrame,
  chatSocketUrl,
  interruptFrame,
  parseServerEvent,
  userMessageFrame,
  type ChatServerEvent,
  type ChatState,
} from "../types/chat";
import { reconnectDelay } from "../types/rfpEvents";

export type ChatConnectionStatus = "connecting" | "live" | "reconnecting" | "closed";

export interface ChatConnection {
  status: ChatConnectionStatus;
  /** Espera del próximo reintento, solo en "reconnecting". */
  retryInMs: number | null;
  /** Motivo, solo en "closed" (sesión cerrada, inexistente o ajena). */
  closedReason: string | null;
}

type Action = { type: "event"; event: ChatServerEvent } | { type: "reset" };

function reducer(state: ChatState, action: Action): ChatState {
  if (action.type === "reset") return INITIAL_CHAT_STATE;
  return applyChatEvent(state, action.event);
}

// Literal `process.env.NEXT_PUBLIC_…`: es lo único que Next.js sustituye en
// el bundle del navegador (gotcha real de telemetry.ts, ver CLAUDE.md).
const CHAT_WS_BASE = process.env.NEXT_PUBLIC_CHAT_WS_URL;

export interface UseChatSocketOptions {
  /** Sustituible en tests por un WebSocket falso. */
  createSocket?: (url: string) => WebSocket;
}

/**
 * Mantiene el WebSocket del chat abierto para `sessionId` mientras la
 * pantalla esté montada.
 *
 * - Autentica con el primer frame (`auth` + el JWT del backoffice), no con
 *   `?token=`: así el token no aparece en ningún log de acceso.
 * - "En directo" solo tras recibir `session_snapshot`: hasta haber
 *   rehidratado la conversación no se acepta entrada nueva.
 * - Si se corta, reconecta con backoff progresivo (1 s, 2 s, 4 s… hasta
 *   30 s) con el MISMO session_id, y el snapshot restaura el hilo (incluida
 *   la respuesta que se estuviera escribiendo). Si vuelve la red, reintenta ya.
 * - 4401 → login; 4403/4404/4409 → cerrado, sin reintentar.
 */
export function useChatSocket(sessionId: string | null, { createSocket }: UseChatSocketOptions = {}) {
  const [state, dispatch] = useReducer(reducer, INITIAL_CHAT_STATE);
  const [connection, setConnection] = useState<ChatConnection>({ status: "connecting", retryInMs: null, closedReason: null });
  const socketRef = useRef<WebSocket | null>(null);
  const liveRef = useRef(false);
  const stateRef = useRef(state);
  useEffect(() => {
    stateRef.current = state;
  });
  const factory = useRef(createSocket);

  useEffect(() => {
    if (!sessionId) return;
    let cancelled = false;
    let attempt = 0;
    let retryTimer: number | undefined;
    dispatch({ type: "reset" });

    function connect() {
      retryTimer = undefined;
      const token = getAuthToken();
      if (!token) {
        redirectToLogin();
        return;
      }
      setConnection((current) => (current.status === "reconnecting" ? current : { status: "connecting", retryInMs: null, closedReason: null }));
      const url = chatSocketUrl(sessionId as string, CHAT_WS_BASE, window.location);
      const socket = factory.current ? factory.current(url) : new WebSocket(url);
      socketRef.current = socket;

      socket.onopen = () => socket.send(authFrame(token));
      socket.onmessage = (message: MessageEvent) => {
        if (typeof message.data !== "string") return;
        const event = parseServerEvent(message.data);
        if (!event) return;
        if (event.event === "session_snapshot") {
          attempt = 0;
          liveRef.current = true;
          setConnection({ status: "live", retryInMs: null, closedReason: null });
        }
        dispatch({ type: "event", event });
      };
      socket.onclose = (close: CloseEvent) => {
        liveRef.current = false;
        if (socketRef.current === socket) socketRef.current = null;
        if (cancelled) return;
        if (close.code === CLOSE_UNAUTHENTICATED) {
          redirectToLogin();
          return;
        }
        const finalReason = FINAL_CLOSE_CODES[close.code];
        if (finalReason) {
          setConnection({ status: "closed", retryInMs: null, closedReason: finalReason });
          return;
        }
        // Red caída, API reiniciada (1012), cola llena (4429)…: reintentar.
        const delay = reconnectDelay(attempt);
        attempt += 1;
        setConnection({ status: "reconnecting", retryInMs: delay, closedReason: null });
        retryTimer = window.setTimeout(connect, delay);
      };
    }

    function reconnectNow() {
      if (retryTimer === undefined) return; // conectado o conectando
      window.clearTimeout(retryTimer);
      connect();
    }

    connect();
    window.addEventListener("online", reconnectNow);
    return () => {
      cancelled = true;
      window.removeEventListener("online", reconnectNow);
      if (retryTimer !== undefined) window.clearTimeout(retryTimer);
      socketRef.current?.close(1000);
      socketRef.current = null;
      liveRef.current = false;
    };
  }, [sessionId]);

  /**
   * Envía el mensaje. Si el agente está respondiendo, como interrupción:
   * el servidor aborta esa respuesta y abre un turno nuevo con este texto.
   * Devuelve false si no hay conexión (la pantalla conserva el borrador).
   */
  const sendMessage = useCallback((text: string): boolean => {
    const socket = socketRef.current;
    const current = stateRef.current;
    if (!socket || !liveRef.current || !current.sessionId || socket.readyState !== 1) return false;
    socket.send(current.generating ? interruptFrame(current.sessionId, text) : userMessageFrame(current.sessionId, text));
    return true;
  }, []);

  return { state, connection, sendMessage };
}
