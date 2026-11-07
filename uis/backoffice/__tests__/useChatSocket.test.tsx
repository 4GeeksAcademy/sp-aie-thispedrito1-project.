import { act } from "react";
import { createRoot, type Root } from "react-dom/client";

import { useChatSocket } from "../hooks/useChatSocket";
import { redirectToLogin } from "../services/http";
import { getAuthToken } from "../services/session";

jest.mock("../services/http", () => ({ redirectToLogin: jest.fn() }));
jest.mock("../services/session", () => ({ getAuthToken: jest.fn(() => "jwt-del-backoffice") }));

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const SID = "chat_0076";

/** WebSocket falso: el test decide cuándo abre, qué llega y cómo se cierra. */
class FakeSocket {
  static instances: FakeSocket[] = [];
  readyState = 0;
  sent: string[] = [];
  onopen: (() => void) | null = null;
  onmessage: ((event: { data: string }) => void) | null = null;
  onclose: ((event: { code: number }) => void) | null = null;

  constructor(public url: string) {
    FakeSocket.instances.push(this);
  }
  send(data: string) {
    this.sent.push(data);
  }
  close() {
    this.readyState = 3;
  }
  open() {
    this.readyState = 1;
    this.onopen?.();
  }
  receive(frame: unknown) {
    this.onmessage?.({ data: JSON.stringify(frame) });
  }
  drop(code = 1006) {
    this.readyState = 3;
    this.onclose?.({ code });
  }
}

const snapshotFrame = (messages: unknown[] = [], in_progress: unknown = null) => ({
  event: "session_snapshot",
  data: { session_id: SID, status: "active", messages, in_progress },
});

let container: HTMLDivElement;
let root: Root;
let latest: ReturnType<typeof useChatSocket>;

function Probe({ sessionId }: { sessionId: string }) {
  latest = useChatSocket(sessionId, { createSocket: (url) => new FakeSocket(url) as unknown as WebSocket });
  return null;
}

const lastSocket = () => FakeSocket.instances[FakeSocket.instances.length - 1];

beforeEach(() => {
  jest.useFakeTimers();
  jest.spyOn(Math, "random").mockReturnValue(0.5); // backoff sin azar: 1 s, 2 s, 4 s…
  FakeSocket.instances = [];
  container = document.createElement("div");
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  jest.useRealTimers();
  jest.restoreAllMocks();
  (redirectToLogin as jest.Mock).mockClear();
});

async function connected(): Promise<FakeSocket> {
  await act(async () => root.render(<Probe sessionId={SID} />));
  const socket = lastSocket();
  act(() => socket.open());
  act(() => socket.receive(snapshotFrame()));
  return socket;
}

it("se autentica con el primer frame (no en la URL) y está en directo tras el snapshot", async () => {
  await act(async () => root.render(<Probe sessionId={SID} />));
  const socket = lastSocket();
  expect(socket.url).toBe(`ws://localhost:8000/agent/chat/ws/${SID}`);
  expect(socket.url).not.toContain("token");
  act(() => socket.open());
  expect(JSON.parse(socket.sent[0])).toEqual({ event: "auth", data: { token: "jwt-del-backoffice" } });
  // Hasta rehidratar no se acepta entrada nueva.
  expect(latest.connection.status).toBe("connecting");
  expect(latest.sendMessage("hola")).toBe(false);

  act(() => socket.receive(snapshotFrame()));
  expect(latest.connection.status).toBe("live");
  expect(latest.state.hydrated).toBe(true);
});

it("pinta los tokens según llegan y, si está respondiendo, envía la interrupción", async () => {
  const socket = await connected();
  expect(latest.sendMessage("¿Plazo?")).toBe(true);
  expect(JSON.parse(socket.sent[1])).toEqual({ event: "user_message", data: { session_id: SID, text: "¿Plazo?" } });

  act(() => socket.receive({ event: "user_message", data: { session_id: SID, message_id: "msg_1", text: "¿Plazo?", status: "completed" } }));
  act(() => socket.receive({ event: "token_chunk", data: { session_id: SID, message_id: "msg_2", token: "Según ", sequence: 1 } }));
  expect(latest.state.messages[1].text).toBe("Según ");
  act(() => socket.receive({ event: "token_chunk", data: { session_id: SID, message_id: "msg_2", token: "HIPAA ", sequence: 2 } }));
  expect(latest.state.messages[1].text).toBe("Según HIPAA ");

  latest.sendMessage("espera, mi pregunta era sobre UK GDPR, no HIPAA");
  expect(JSON.parse(socket.sent[2])).toEqual({
    event: "interrupt_requested",
    data: { session_id: SID, new_input: "espera, mi pregunta era sobre UK GDPR, no HIPAA" },
  });
});

it("al caerse reconecta con backoff progresivo y el mismo session_id, y rehidrata", async () => {
  const first = await connected();
  act(() => first.drop());
  expect(latest.connection).toEqual({ status: "reconnecting", retryInMs: 1000, closedReason: null });
  expect(latest.sendMessage("hola")).toBe(false);

  await act(async () => jest.advanceTimersByTime(1000));
  const second = lastSocket();
  expect(second).not.toBe(first);
  act(() => second.drop()); // el servidor sigue caído: 2 s
  expect(latest.connection.retryInMs).toBe(2000);
  await act(async () => jest.advanceTimersByTime(2000));

  const third = lastSocket();
  expect(third.url).toBe(first.url); // mismo hilo de conversación
  act(() => third.open());
  act(() =>
    third.receive(
      snapshotFrame(
        [{ message_id: "msg_1", role: "user", text: "¿BAA?", status: "completed", replies_to: null }],
        { message_id: "msg_2", replies_to: "msg_1", text: "Un BAA ", sequence: 2 },
      ),
    ),
  );
  expect(latest.connection.status).toBe("live");
  expect(latest.state.messages.map((m) => m.text)).toEqual(["¿BAA?", "Un BAA "]);
  expect(latest.state.generating).toBe("msg_2");

  // Tras una conexión buena, el siguiente fallo vuelve a esperar 1 s.
  act(() => third.drop());
  expect(latest.connection.retryInMs).toBe(1000);
});

it("4401 lleva al login y una sesión cerrada no se reintenta", async () => {
  const socket = await connected();
  act(() => socket.drop(4409));
  expect(latest.connection).toEqual({ status: "closed", retryInMs: null, closedReason: "Esta conversación está cerrada." });
  await act(async () => jest.advanceTimersByTime(60000));
  expect(FakeSocket.instances).toHaveLength(1);

  act(() => root.unmount());
  root = createRoot(container);
  const again = await connected();
  act(() => again.drop(4401));
  expect(redirectToLogin).toHaveBeenCalled();
});

it("sin token ni siquiera abre el socket", async () => {
  (getAuthToken as jest.Mock).mockReturnValueOnce(null);
  await act(async () => root.render(<Probe sessionId={SID} />));
  expect(FakeSocket.instances).toHaveLength(0);
  expect(redirectToLogin).toHaveBeenCalled();
});
