import {
  INITIAL_CHAT_STATE,
  applyChatEvent,
  chatSocketUrl,
  interruptFrame,
  parseServerEvent,
  userMessageFrame,
  validateChatInput,
  type ChatServerEvent,
  type ChatState,
} from "../types/chat";

const SID = "chat_0076";

function run(events: ChatServerEvent[], start: ChatState = INITIAL_CHAT_STATE): ChatState {
  return events.reduce(applyChatEvent, start);
}

const snapshot = (overrides: Partial<Extract<ChatServerEvent, { event: "session_snapshot" }>["data"]> = {}): ChatServerEvent => ({
  event: "session_snapshot",
  data: { session_id: SID, status: "active", messages: [], in_progress: null, ...overrides },
});
const token = (message_id: string, value: string, sequence: number): ChatServerEvent => ({
  event: "token_chunk",
  data: { session_id: SID, message_id, token: value, sequence },
});
const userMessage = (message_id: string, text: string | null, status: "completed" | "redacted" = "completed"): ChatServerEvent => ({
  event: "user_message",
  data: { session_id: SID, message_id, text, status },
});

describe("reductor del chat", () => {
  it("escribe la respuesta en vivo token a token, sin esperar al final", () => {
    const states: string[] = [];
    let state = run([snapshot(), userMessage("msg_1", "¿BAA?")]);
    for (const [i, word] of ["Un ", "BAA ", "es…"].entries()) {
      state = applyChatEvent(state, token("msg_2", word, i + 1));
      states.push(state.messages[1].text ?? "");
    }
    expect(states).toEqual(["Un ", "Un BAA ", "Un BAA es…"]);
    expect(state.messages[1]).toMatchObject({ role: "assistant", status: "streaming" });
    expect(state.generating).toBe("msg_2");
  });

  it("ignora tokens repetidos o ya incluidos en el snapshot (por sequence)", () => {
    const state = run([
      snapshot({ in_progress: { message_id: "msg_2", replies_to: "msg_1", text: "Un BAA ", sequence: 2 } }),
      token("msg_2", "BAA ", 2), // ya estaba en el snapshot
      token("msg_2", "es ", 3),
      token("msg_2", "es ", 3), // reenvío
    ]);
    expect(state.messages[0].text).toBe("Un BAA es ");
  });

  it("una interrupción conserva el texto parcial y la respuesta redirigida es un mensaje nuevo", () => {
    const state = run([
      snapshot(),
      userMessage("msg_1", "¿Plazo de notificación?"),
      token("msg_2", "Según ", 1),
      token("msg_2", "HIPAA ", 2),
      { event: "generation_interrupted", data: { session_id: SID, message_id: "msg_2", status: "interrupted" } },
      token("msg_2", "tarde ", 3), // un token tardío de la generación cortada nunca se pinta
      userMessage("msg_3", "espera, mi pregunta era sobre UK GDPR, no HIPAA"),
      token("msg_4", "Bajo ", 1),
      token("msg_4", "UK GDPR…", 2),
      {
        event: "generation_completed",
        data: { session_id: SID, message_id: "msg_4", status: "completed", text: "Bajo UK GDPR…", outcome: "answered" },
      },
    ]);
    expect(state.messages.map((m) => [m.message_id, m.status, m.text])).toEqual([
      ["msg_1", "completed", "¿Plazo de notificación?"],
      ["msg_2", "interrupted", "Según HIPAA "],
      ["msg_3", "completed", "espera, mi pregunta era sobre UK GDPR, no HIPAA"],
      ["msg_4", "completed", "Bajo UK GDPR…"],
    ]);
    expect(state.generating).toBeNull();
  });

  it("el snapshot rehidrata la conversación entera al reconectar", () => {
    const before = run([snapshot(), userMessage("msg_1", "hola"), token("msg_2", "Hol", 1)]);
    const after = applyChatEvent(
      before,
      snapshot({
        messages: [
          { message_id: "msg_1", role: "user", text: "hola", status: "completed", replies_to: null, lastSequence: 0 },
          { message_id: "msg_2", role: "assistant", text: "Hola, ¿en qué te ayudo?", status: "completed", replies_to: "msg_1", lastSequence: 0 },
        ],
      }),
    );
    expect(after.messages.map((m) => m.text)).toEqual(["hola", "Hola, ¿en qué te ayudo?"]);
    expect(after.generating).toBeNull();
    expect(after.hydrated).toBe(true);
  });

  it("un evento repetido no duplica mensajes", () => {
    const state = run([snapshot(), userMessage("msg_1", "hola"), userMessage("msg_1", "hola")]);
    expect(state.messages).toHaveLength(1);
  });

  it("el texto final del servidor manda (guardarraíl o nota de memoria)", () => {
    const state = run([
      snapshot(),
      token("msg_2", "Escribe ", 1),
      { event: "generation_blocked", data: { session_id: SID, message_id: "msg_2", status: "blocked", text: "He retenido la respuesta…" } },
    ]);
    expect(state.messages[0]).toMatchObject({ status: "blocked", text: "He retenido la respuesta…" });
  });

  it("un mensaje con datos de paciente llega sin texto", () => {
    const state = run([snapshot(), userMessage("msg_1", null, "redacted")]);
    expect(state.messages[0]).toMatchObject({ text: null, status: "redacted" });
  });
});

describe("contrato del cable", () => {
  it("valida lo que llega y descarta lo que no cumple el contrato", () => {
    expect(parseServerEvent(JSON.stringify(token("msg_2", "Según", 9)))).toEqual(token("msg_2", "Según", 9));
    expect(parseServerEvent("no json")).toBeNull();
    expect(parseServerEvent(JSON.stringify({ event: "token_chunk", data: { session_id: SID, token: "x" } }))).toBeNull();
    expect(parseServerEvent(JSON.stringify({ event: "rfp_ticket_created", data: { session_id: SID } }))).toBeNull();
  });

  it("envía user_message o interrupt_requested con los nombres del BRIEFING", () => {
    expect(JSON.parse(userMessageFrame(SID, "hola"))).toEqual({ event: "user_message", data: { session_id: SID, text: "hola" } });
    expect(JSON.parse(interruptFrame(SID, "espera"))).toEqual({
      event: "interrupt_requested",
      data: { session_id: SID, new_input: "espera" },
    });
  });

  it("construye la URL del socket con el session_id y sin token", () => {
    expect(chatSocketUrl(SID, undefined, { protocol: "http:", hostname: "localhost" })).toBe(`ws://localhost:8000/agent/chat/ws/${SID}`);
    expect(chatSocketUrl(SID, "wss://api.example.com/", { protocol: "https:", hostname: "x" })).toBe(
      `wss://api.example.com/agent/chat/ws/${SID}`,
    );
  });

  it("valida la entrada antes de enviarla", () => {
    expect(validateChatInput("   ")).toBe("Escribe tu pregunta.");
    expect(validateChatInput("x".repeat(1001))).toMatch(/1000/);
    expect(validateChatInput("¿BAA?")).toBeNull();
  });
});
