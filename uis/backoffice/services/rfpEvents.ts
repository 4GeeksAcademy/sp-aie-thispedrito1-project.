import {
  STREAM_IDLE_TIMEOUT_MS,
  createSseParser,
  toRfpTicketCreated,
  type RfpTicketCreatedEvent,
} from "../types/rfpEvents";
import { apiUrl } from "./http";
import { getAuthToken } from "./session";

/**
 * Una conexión al stream SSE de RFPs (`GET /rfp-tickets/events`).
 *
 * `fetch` + `ReadableStream`, no `EventSource`: `EventSource` no deja enviar
 * `Authorization: Bearer …`, y el stream exige el mismo JWT que el resto del
 * backoffice. Tampoco pasa por `requestJson`: esa función espera un JSON que
 * aquí nunca termina de llegar.
 *
 * Resuelve cuando el servidor cierra el stream y lanza si se corta: decidir
 * si se reintenta y cuándo es cosa de `useRfpEvents`.
 */

/** 401: token ausente o caducado. Reintentar no lo arregla. */
export class RfpStreamAuthError extends Error {
  constructor() {
    super("La sesión no es válida para recibir avisos en tiempo real.");
    this.name = "RfpStreamAuthError";
  }
}

export interface StreamRfpEventsOptions {
  signal: AbortSignal;
  /** El servidor aceptó la conexión (cabeceras recibidas, ya suscrito). */
  onOpen: () => void;
  onTicketCreated: (event: RfpTicketCreatedEvent) => void;
  fetchImpl?: typeof fetch;
  idleTimeoutMs?: number;
}

export async function streamRfpEvents({
  signal,
  onOpen,
  onTicketCreated,
  fetchImpl = fetch,
  idleTimeoutMs = STREAM_IDLE_TIMEOUT_MS,
}: StreamRfpEventsOptions): Promise<void> {
  const token = getAuthToken();
  if (!token) throw new RfpStreamAuthError();

  // Controlador propio: lo aborta quien llama (desmontar la pantalla) o el
  // vigilante de inactividad. Una red caída a veces no da error en `fetch`:
  // la lectura se queda esperando para siempre sin el vigilante.
  const controller = new AbortController();
  const abort = () => controller.abort();
  if (signal.aborted) abort();
  signal.addEventListener("abort", abort);
  let watchdog: ReturnType<typeof setTimeout> | undefined;
  const rearm = () => {
    clearTimeout(watchdog);
    watchdog = setTimeout(abort, idleTimeoutMs);
  };

  try {
    rearm();
    const response = await fetchImpl(apiUrl("/rfp-tickets/events"), {
      headers: { Authorization: `Bearer ${token}`, Accept: "text/event-stream" },
      cache: "no-store",
      signal: controller.signal,
    });
    if (response.status === 401) throw new RfpStreamAuthError();
    if (!response.ok || !response.body) throw new Error(`El stream de RFPs respondió ${response.status}.`);
    onOpen();

    const parser = createSseParser((frame) => {
      const event = toRfpTicketCreated(frame);
      if (event) onTicketCreated(event);
    });
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    for (;;) {
      const { done, value } = await reader.read();
      if (done) return;
      rearm(); // cualquier byte (también un keep-alive) demuestra que la conexión vive
      parser.push(decoder.decode(value, { stream: true }));
    }
  } finally {
    clearTimeout(watchdog);
    signal.removeEventListener("abort", abort);
  }
}
