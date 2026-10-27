"use client";

import { useEffect, useRef, useState } from "react";

import { redirectToLogin } from "../services/http";
import { RfpStreamAuthError, streamRfpEvents } from "../services/rfpEvents";
import { reconnectDelay, type RfpTicketCreatedEvent } from "../types/rfpEvents";

export type RfpStreamStatus = "connecting" | "live" | "reconnecting";

export interface RfpStreamState {
  status: RfpStreamStatus;
  /** Espera del próximo reintento, solo en "reconnecting". */
  retryInMs: number | null;
}

export interface UseRfpEventsOptions {
  /** Conexión aceptada. `isReconnect` es false solo la primera vez: es el
   * momento de recuperar lo que llegó mientras no había conexión. */
  onOpen: (isReconnect: boolean) => void;
  onTicketCreated: (event: RfpTicketCreatedEvent) => void;
}

/**
 * Mantiene abierto el stream SSE de RFPs mientras la pantalla esté montada.
 * Si se corta, reintenta con backoff progresivo (1 s, 2 s, 4 s… hasta 30 s;
 * vuelve a 1 s tras una conexión buena). Si el navegador avisa de que ha
 * vuelto la red, reintenta ya sin esperar. Un 401 no se reintenta: lleva al
 * login como cualquier otra petición del backoffice.
 *
 * Los callbacks se leen por ref: cambiar de función entre renders no reabre
 * la conexión.
 */
export function useRfpEvents({ onOpen, onTicketCreated }: UseRfpEventsOptions): RfpStreamState {
  const callbacks = useRef({ onOpen, onTicketCreated });
  useEffect(() => {
    callbacks.current = { onOpen, onTicketCreated };
  });
  const [state, setState] = useState<RfpStreamState>({ status: "connecting", retryInMs: null });

  useEffect(() => {
    let cancelled = false;
    let attempt = 0;
    let hasConnected = false;
    let retryTimer: number | undefined;
    let controller: AbortController | null = null;

    async function connect() {
      retryTimer = undefined;
      controller = new AbortController();
      try {
        await streamRfpEvents({
          signal: controller.signal,
          onOpen: () => {
            attempt = 0;
            setState({ status: "live", retryInMs: null });
            callbacks.current.onOpen(hasConnected);
            hasConnected = true;
          },
          onTicketCreated: (event) => callbacks.current.onTicketCreated(event),
        });
      } catch (error) {
        if (cancelled) return;
        if (error instanceof RfpStreamAuthError) {
          redirectToLogin();
          return;
        }
        // Red caída, servidor reiniciado, vigilante de inactividad…: reintentar.
      }
      if (cancelled) return;
      const delay = reconnectDelay(attempt);
      attempt += 1;
      setState({ status: "reconnecting", retryInMs: delay });
      retryTimer = window.setTimeout(connect, delay);
    }

    function reconnectNow() {
      if (retryTimer === undefined) return; // conectado o conectando: nada que adelantar
      window.clearTimeout(retryTimer);
      void connect();
    }

    void connect();
    window.addEventListener("online", reconnectNow);
    return () => {
      cancelled = true;
      controller?.abort();
      window.clearTimeout(retryTimer);
      window.removeEventListener("online", reconnectNow);
    };
  }, []);

  return state;
}
