import { TextDecoder, TextEncoder } from "util";

import { RfpStreamAuthError, streamRfpEvents } from "../services/rfpEvents";
import { AUTH_TOKEN_KEY } from "../services/session";
import type { RfpTicketListItem } from "../types/rfp";
import {
  RECONNECT_MAX_MS,
  createSseParser,
  findMissedTickets,
  mergeArrivals,
  reconnectDelay,
  toRfpTicketCreated,
  type RfpArrival,
  type RfpTicketCreatedEvent,
  type SseFrame,
} from "../types/rfpEvents";

// jsdom no trae TextDecoder; el de Node es el mismo estándar.
Object.assign(globalThis, { TextDecoder, TextEncoder });

const TICKET: RfpTicketCreatedEvent = {
  ticket_id: "6f1c1e5e-2d0b-4d7e-9a43-0d6c2a7c1b11",
  rfp_id: "RFP-20261027-0001",
  client_name: "Westbrook Manufacturing",
  client_country: "US",
  program_type: ["occupational_health"],
  status: "analizando",
  created_at: "2026-10-27T14:32:00Z",
};
const WIRE = `id: ${TICKET.ticket_id}\nevent: rfp_ticket_created\ndata: ${JSON.stringify(TICKET)}\n\n`;

function collect(chunks: string[]): SseFrame[] {
  const frames: SseFrame[] = [];
  const parser = createSseParser((frame) => frames.push(frame));
  chunks.forEach((chunk) => parser.push(chunk));
  return frames;
}

describe("createSseParser", () => {
  it("lee un evento con nombre, id y data", () => {
    expect(collect([WIRE])).toEqual([{ event: "rfp_ticket_created", data: JSON.stringify(TICKET), id: TICKET.ticket_id }]);
  });

  it("une un evento partido en trozos arbitrarios", () => {
    const pieces = [WIRE.slice(0, 7), WIRE.slice(7, 40), WIRE.slice(40, WIRE.length - 1), WIRE.slice(WIRE.length - 1)];
    expect(collect(pieces)).toHaveLength(1);
    expect(collect(pieces)[0].event).toBe("rfp_ticket_created");
  });

  it("no despacha nada por los comentarios de keep-alive", () => {
    expect(collect([": connected\n\n", ": keep-alive\n\n"])).toEqual([]);
  });

  it("trata un \\r\\n partido entre dos trozos como un solo salto de línea", () => {
    const frames = collect(["event: rfp_ticket_created\r", "\ndata: {}\r\n\r\n"]);
    expect(frames).toEqual([{ event: "rfp_ticket_created", data: "{}", id: null }]);
  });

  it("une varias líneas data y usa 'message' si no hay event", () => {
    expect(collect(["data: a\ndata: b\n\n"])).toEqual([{ event: "message", data: "a\nb", id: null }]);
  });
});

describe("toRfpTicketCreated", () => {
  it("acepta el payload del BRIEFING", () => {
    expect(toRfpTicketCreated({ event: "rfp_ticket_created", data: JSON.stringify(TICKET), id: null })).toEqual(TICKET);
  });

  it("ignora otros eventos, JSON roto y payloads sin ticket_id", () => {
    expect(toRfpTicketCreated({ event: "message", data: JSON.stringify(TICKET), id: null })).toBeNull();
    expect(toRfpTicketCreated({ event: "rfp_ticket_created", data: "{roto", id: null })).toBeNull();
    expect(toRfpTicketCreated({ event: "rfp_ticket_created", data: JSON.stringify({ rfp_id: "x" }), id: null })).toBeNull();
  });
});

describe("reconnectDelay", () => {
  it("dobla la espera en cada intento hasta el tope", () => {
    const noJitter = () => 0.5;
    expect([0, 1, 2, 3].map((n) => reconnectDelay(n, noJitter))).toEqual([1000, 2000, 4000, 8000]);
    expect(reconnectDelay(10, noJitter)).toBe(RECONNECT_MAX_MS);
  });

  it("el azar se queda dentro de ±20 %", () => {
    expect(reconnectDelay(2, () => 0)).toBe(3200);
    expect(reconnectDelay(2, () => 1)).toBe(4800);
  });
});

function listItem(overrides: Partial<RfpTicketListItem> = {}): RfpTicketListItem {
  return {
    ticket_id: TICKET.ticket_id,
    rfp_id: TICKET.rfp_id,
    status: "analizando",
    discard_reason: null,
    requires_compliance_review: false,
    client_name: null,
    client_country: null,
    created_at: TICKET.created_at,
    updated_at: TICKET.created_at,
    ...overrides,
  };
}

describe("mergeArrivals", () => {
  const arrivals = new Map<string, RfpArrival>([[TICKET.ticket_id, { ticket: TICKET, source: "live" }]]);

  it("añade arriba un ticket que la lista aún no tiene", () => {
    const older = listItem({ ticket_id: "otro", rfp_id: "RFP-OLD", created_at: "2026-10-01T00:00:00Z" });
    const rows = mergeArrivals([older], arrivals);
    expect(rows.map((r) => r.ticket_id)).toEqual([TICKET.ticket_id, "otro"]);
  });

  it("no duplica un ticket que ya está en la lista y le completa cliente y país", () => {
    const rows = mergeArrivals([listItem()], arrivals);
    expect(rows).toHaveLength(1);
    expect(rows[0]).toMatchObject({ client_name: "Westbrook Manufacturing", client_country: "US" });
  });

  it("lo que ya trae la lista manda sobre el aviso", () => {
    const rows = mergeArrivals([listItem({ client_name: "Nombre guardado", status: "analisis_completo" })], arrivals);
    expect(rows[0]).toMatchObject({ client_name: "Nombre guardado", status: "analisis_completo" });
  });
});

describe("findMissedTickets (recuperación al reconectar)", () => {
  const known = new Set(["ya-visto"]);

  it("devuelve las RFP válidas que no se conocían", () => {
    const fetched = [
      listItem({ ticket_id: "ya-visto", status: "analisis_completo", client_name: "A" }),
      listItem({ ticket_id: "nueva-completa", status: "analisis_completo", client_name: "B" }),
      listItem({ ticket_id: "nueva-avanzada", status: "esperando_aprobación", client_name: "C" }),
    ];
    expect(findMissedTickets(known, fetched).map((t) => t.ticket_id)).toEqual(["nueva-completa", "nueva-avanzada"]);
  });

  it("no avisa de lo que en directo tampoco habría avisado", () => {
    const fetched = [
      listItem({ ticket_id: "no-rfp", status: "descartado", discard_reason: "not_rfp" }),
      listItem({ ticket_id: "con-phi", status: "descartado", discard_reason: "phi_detected" }),
      listItem({ ticket_id: "fallo", status: "error" }),
      // Puede no haber pasado aún el clasificador: si es válida, llegará por el stream.
      listItem({ ticket_id: "sin-clasificar", status: "analizando", client_name: null }),
    ];
    expect(findMissedTickets(known, fetched)).toEqual([]);
  });
});

// --------------------------------------------------------------------------
// streamRfpEvents con un fetch falso (jsdom no trae ReadableStream)
// --------------------------------------------------------------------------

function fakeResponse(status: number, chunks: string[] = []): Response {
  const encoder = new TextEncoder();
  const queue = chunks.map((chunk) => encoder.encode(chunk));
  const reader = {
    read: async () => (queue.length ? { done: false, value: queue.shift() } : { done: true, value: undefined }),
  };
  return { status, ok: status >= 200 && status < 300, body: { getReader: () => reader } } as unknown as Response;
}

describe("streamRfpEvents", () => {
  beforeEach(() => window.localStorage.setItem(AUTH_TOKEN_KEY, "jwt-de-prueba"));
  afterEach(() => window.localStorage.clear());

  it("envía el JWT del backoffice y entrega los eventos aunque lleguen partidos", async () => {
    const fetchImpl = jest.fn(async () => fakeResponse(200, [": connected\n\n", WIRE.slice(0, 30), WIRE.slice(30)]));
    const onOpen = jest.fn();
    const received: RfpTicketCreatedEvent[] = [];

    await streamRfpEvents({
      signal: new AbortController().signal,
      onOpen,
      onTicketCreated: (event) => received.push(event),
      fetchImpl: fetchImpl as unknown as typeof fetch,
    });

    const [url, init] = fetchImpl.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe("/api-proxy/rfp-tickets/events");
    expect((init.headers as Record<string, string>).Authorization).toBe("Bearer jwt-de-prueba");
    expect(onOpen).toHaveBeenCalledTimes(1);
    expect(received).toEqual([TICKET]);
  });

  it("un 401 es RfpStreamAuthError (no se reintenta)", async () => {
    const fetchImpl = jest.fn(async () => fakeResponse(401));
    await expect(
      streamRfpEvents({
        signal: new AbortController().signal,
        onOpen: jest.fn(),
        onTicketCreated: jest.fn(),
        fetchImpl: fetchImpl as unknown as typeof fetch,
      }),
    ).rejects.toBeInstanceOf(RfpStreamAuthError);
  });

  it("sin token ni siquiera abre la conexión", async () => {
    window.localStorage.clear();
    const fetchImpl = jest.fn();
    await expect(
      streamRfpEvents({
        signal: new AbortController().signal,
        onOpen: jest.fn(),
        onTicketCreated: jest.fn(),
        fetchImpl: fetchImpl as unknown as typeof fetch,
      }),
    ).rejects.toBeInstanceOf(RfpStreamAuthError);
    expect(fetchImpl).not.toHaveBeenCalled();
  });

  it("el vigilante corta una conexión que deja de enviar bytes", async () => {
    // fetch que se queda colgado hasta que alguien aborta su señal.
    const fetchImpl = jest.fn(
      (_url: string, init: RequestInit) =>
        new Promise<Response>((_resolve, reject) => {
          init.signal?.addEventListener("abort", () => reject(new DOMException("aborted", "AbortError")));
        }),
    );
    await expect(
      streamRfpEvents({
        signal: new AbortController().signal,
        onOpen: jest.fn(),
        onTicketCreated: jest.fn(),
        fetchImpl: fetchImpl as unknown as typeof fetch,
        idleTimeoutMs: 20,
      }),
    ).rejects.toThrow("aborted");
  });
});
