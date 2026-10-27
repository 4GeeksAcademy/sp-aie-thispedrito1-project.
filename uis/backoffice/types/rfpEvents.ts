import type { RfpStatus, RfpTicketListItem } from "./rfp";

/**
 * Notificaciones en tiempo real de RFPs (`GET /rfp-tickets/events`, SSE).
 * Lógica pura: lector del formato SSE, espera entre reintentos y fusión de
 * los avisos con la lista sin duplicar tickets. Sin React ni red, para
 * poder probarla sola (ver docs/sse-notifications.md).
 */

export const RFP_TICKET_CREATED = "rfp_ticket_created";

/** Payload del BRIEFING de tiempo real (§3). `status` es el del sistema de RFPs: "analizando". */
export interface RfpTicketCreatedEvent {
  ticket_id: string;
  rfp_id: string;
  client_name: string | null;
  client_country: "US" | "UK" | null;
  program_type: string[];
  status: RfpStatus;
  created_at: string;
}

/** Un ticket nuevo tal como lo muestra la pantalla: en directo o recuperado al reconectar. */
export interface RfpArrival {
  ticket: RfpTicketCreatedEvent;
  source: "live" | "recovered";
}

// --------------------------------------------------------------------------
// Lector SSE
// --------------------------------------------------------------------------

export interface SseFrame {
  event: string;
  data: string;
  id: string | null;
}

/**
 * Lector incremental del formato SSE (spec de WHATWG, lo justo que usa esta
 * API): los trozos que da `ReadableStream` cortan por cualquier sitio, así
 * que guarda la línea incompleta hasta el siguiente `push`. Un frame termina
 * en línea en blanco; `:` al principio es un comentario (keep-alive); varias
 * líneas `data:` se unen con "\n"; sin `event:` el nombre es "message".
 */
export function createSseParser(onFrame: (frame: SseFrame) => void): { push(text: string): void } {
  let buffer = "";
  let event = "";
  let data: string[] = [];
  let id: string | null = null;

  function dispatch() {
    if (data.length > 0) onFrame({ event: event || "message", data: data.join("\n"), id });
    event = "";
    data = [];
  }

  function processLine(line: string) {
    if (line === "") return dispatch();
    if (line.startsWith(":")) return;
    const colon = line.indexOf(":");
    const field = colon === -1 ? line : line.slice(0, colon);
    let value = colon === -1 ? "" : line.slice(colon + 1);
    if (value.startsWith(" ")) value = value.slice(1);
    if (field === "event") event = value;
    else if (field === "data") data.push(value);
    else if (field === "id") id = value;
  }

  let pendingCR = false;

  return {
    push(text: string) {
      // Un "\r\n" partido entre dos trozos es UN salto de línea, no dos (si
      // no, la línea vacía de más despacharía el frame a medias).
      if (pendingCR && text.startsWith("\n")) text = text.slice(1);
      pendingCR = text.endsWith("\r");
      buffer += text;
      const lines = buffer.split(/\r\n|\r|\n/);
      // El último trozo puede ser una línea a medias: espera al siguiente push.
      buffer = lines.pop() ?? "";
      lines.forEach(processLine);
    },
  };
}

const isRecord = (value: unknown): value is Record<string, unknown> => typeof value === "object" && value !== null;

/** Frame → evento tipado, o null si no es un `rfp_ticket_created` bien formado. */
export function toRfpTicketCreated(frame: SseFrame): RfpTicketCreatedEvent | null {
  if (frame.event !== RFP_TICKET_CREATED) return null;
  let payload: unknown;
  try {
    payload = JSON.parse(frame.data);
  } catch {
    return null;
  }
  if (!isRecord(payload)) return null;
  const { ticket_id, rfp_id, client_name, client_country, program_type, status, created_at } = payload;
  if (typeof ticket_id !== "string" || typeof rfp_id !== "string" || typeof status !== "string" || typeof created_at !== "string") {
    return null;
  }
  return {
    ticket_id,
    rfp_id,
    client_name: typeof client_name === "string" ? client_name : null,
    client_country: client_country === "US" || client_country === "UK" ? client_country : null,
    program_type: Array.isArray(program_type) ? program_type.filter((p): p is string => typeof p === "string") : [],
    status: status as RfpStatus,
    created_at,
  };
}

// --------------------------------------------------------------------------
// Reconexión
// --------------------------------------------------------------------------

export const RECONNECT_BASE_MS = 1000;
export const RECONNECT_MAX_MS = 30000;
/** 3 keep-alives del servidor (15 s) sin un byte: la conexión está muerta aunque `fetch` no lo diga. */
export const STREAM_IDLE_TIMEOUT_MS = 45000;

/**
 * Espera antes del reintento nº `attempt` (0, 1, 2…): 1 s, 2 s, 4 s… hasta
 * 30 s, ±20 % de azar para que 50 pestañas no reconecten en el mismo
 * milisegundo tras una caída del servidor.
 */
export function reconnectDelay(attempt: number, random: () => number = Math.random): number {
  const base = Math.min(RECONNECT_BASE_MS * 2 ** attempt, RECONNECT_MAX_MS);
  return Math.round(base * (0.8 + 0.4 * random()));
}

// --------------------------------------------------------------------------
// Avisos + lista, sin duplicados
// --------------------------------------------------------------------------

/** Lista del servidor (incompleta para un ticket recién clasificado) → forma del aviso. */
export function arrivalFromListItem(item: RfpTicketListItem): RfpTicketCreatedEvent {
  return {
    ticket_id: item.ticket_id,
    rfp_id: item.rfp_id,
    client_name: item.client_name,
    client_country: item.client_country,
    program_type: [],
    status: item.status,
    created_at: item.created_at,
  };
}

/**
 * Añade los avisos a la lista una sola vez por `ticket_id`. Un ticket que ya
 * está en la lista no se repite: se completa con el cliente y el país del
 * aviso mientras la lista no los traiga (la Parte 1 los guarda al terminar,
 * y el aviso llega antes). Los que aún no están en la lista van arriba.
 */
export function mergeArrivals(list: RfpTicketListItem[], arrivals: Map<string, RfpArrival>): RfpTicketListItem[] {
  const listed = new Set(list.map((t) => t.ticket_id));
  const missing: RfpTicketListItem[] = [];
  arrivals.forEach(({ ticket }) => {
    if (listed.has(ticket.ticket_id)) return;
    missing.push({
      ticket_id: ticket.ticket_id,
      rfp_id: ticket.rfp_id,
      status: ticket.status,
      discard_reason: null,
      requires_compliance_review: false,
      client_name: ticket.client_name,
      client_country: ticket.client_country,
      created_at: ticket.created_at,
      updated_at: ticket.created_at,
    });
  });
  missing.sort((a, b) => b.created_at.localeCompare(a.created_at));
  const completed = list.map((item) => {
    const arrival = arrivals.get(item.ticket_id);
    if (!arrival) return item;
    return {
      ...item,
      client_name: item.client_name ?? arrival.ticket.client_name,
      client_country: item.client_country ?? arrival.ticket.client_country,
    };
  });
  return [...missing, ...completed];
}

// En directo nunca habrían avisado: el aviso sale solo si el clasificador
// acepta el documento (no-RFP y PHI acaban `descartado`; un fallo, `error`).
const NEVER_NOTIFIED: RfpStatus[] = ["descartado", "error"];

/**
 * Estrategia de recuperación (docs/sse-notifications.md): cada lista nueva
 * (al reconectar y en cada refresco) se compara con los tickets que ya se
 * conocían. Devuelve los que no llegaron en directo y merecen aviso: los
 * mismos que habrían avisado en directo.
 *
 * Criterio delegado al asistente ("hazlo tú", 2026-10-27): un ticket
 * `analizando` sin cliente aún puede no haber pasado el clasificador (y
 * acabar descartado), así que todavía no se avisa. No se pierde: si es una
 * RFP válida, o llega su evento en directo o un refresco posterior lo ve ya
 * con cliente (su evento pudo salir durante el corte).
 */
export function findMissedTickets(known: ReadonlySet<string>, fetched: RfpTicketListItem[]): RfpTicketListItem[] {
  return fetched.filter(
    (ticket) =>
      !known.has(ticket.ticket_id) &&
      !NEVER_NOTIFIED.includes(ticket.status) &&
      !(ticket.status === "analizando" && ticket.client_name === null),
  );
}
