/**
 * Chat en tiempo real con el Compliance assistant (Sistemas en Tiempo Real, Parte 2).
 *
 * Contrato del WebSocket (BRIEFING Parte 2, §4; servidor en
 * services/agent/chat/events.py): todo frame es `{event, data}` con nombres
 * explícitos. Aquí viven los tipos, la validación de lo que llega y el
 * reductor puro que convierte la secuencia de eventos en la conversación que
 * se pinta. Sin React ni red: se prueba en __tests__/chat.test.ts.
 */

export const CHAT_AGENT_ID = "compliance_assistant";
export const MAX_CHAT_INPUT_LENGTH = 1000; // mismo tope que el servidor

export type ChatSessionStatus = "active" | "interrupted" | "closed";
export type ChatRole = "user" | "assistant";
/** `streaming` solo existe en el navegador: la respuesta que se está escribiendo. */
export type ChatMessageStatus = "streaming" | "completed" | "interrupted" | "blocked" | "failed" | "redacted";

export interface ChatSession {
  session_id: string;
  agent_id: string;
  user_id: string;
  status: ChatSessionStatus;
  created_at: string;
}

export interface ChatMessage {
  message_id: string;
  role: ChatRole;
  /** null = mensaje del usuario retirado por contener datos de un paciente. */
  text: string | null;
  status: ChatMessageStatus;
  replies_to: string | null;
  /** Último `sequence` aplicado (solo respuestas del asistente en streaming). */
  lastSequence: number;
}

export interface InProgress {
  message_id: string;
  replies_to: string;
  text: string;
  sequence: number;
}

export type ChatServerEvent =
  | { event: "session_snapshot"; data: { session_id: string; status: ChatSessionStatus; messages: ChatMessage[]; in_progress: InProgress | null } }
  | { event: "user_message"; data: { session_id: string; message_id: string; text: string | null; status: ChatMessageStatus } }
  | { event: "token_chunk"; data: { session_id: string; message_id: string; token: string; sequence: number } }
  | { event: "generation_interrupted"; data: { session_id: string; message_id: string; status: "interrupted" } }
  | { event: "generation_completed"; data: { session_id: string; message_id: string; status: "completed"; text: string; outcome: string | null } }
  | { event: "generation_blocked"; data: { session_id: string; message_id: string; status: "blocked"; text: string } }
  | { event: "generation_failed"; data: { session_id: string; message_id: string; status: "failed"; text: string } }
  | { event: "chat_error"; data: { session_id: string; code: string; message: string } };

export interface ChatState {
  sessionId: string | null;
  sessionStatus: ChatSessionStatus | null;
  messages: ChatMessage[];
  /** message_id de la respuesta que se está generando, o null. */
  generating: string | null;
  /** true tras el primer session_snapshot de la conexión actual. */
  hydrated: boolean;
  error: string | null;
}

export const INITIAL_CHAT_STATE: ChatState = {
  sessionId: null,
  sessionStatus: null,
  messages: [],
  generating: null,
  hydrated: false,
  error: null,
};

// --------------------------------------------------------------------------
// Validación de lo que llega
// --------------------------------------------------------------------------

const isRecord = (value: unknown): value is Record<string, unknown> => typeof value === "object" && value !== null;
const isString = (value: unknown): value is string => typeof value === "string";

const MESSAGE_STATUSES: ChatMessageStatus[] = ["streaming", "completed", "interrupted", "blocked", "failed", "redacted"];

function toMessage(raw: unknown): ChatMessage | null {
  if (!isRecord(raw) || !isString(raw.message_id) || (raw.role !== "user" && raw.role !== "assistant")) return null;
  const status = MESSAGE_STATUSES.includes(raw.status as ChatMessageStatus) ? (raw.status as ChatMessageStatus) : "completed";
  return {
    message_id: raw.message_id,
    role: raw.role,
    text: isString(raw.text) ? raw.text : null,
    status,
    replies_to: isString(raw.replies_to) ? raw.replies_to : null,
    lastSequence: 0,
  };
}

/** Frame de texto del servidor → evento tipado, o null si no cumple el contrato. */
export function parseServerEvent(raw: string): ChatServerEvent | null {
  let parsed: unknown;
  try {
    parsed = JSON.parse(raw);
  } catch {
    return null;
  }
  if (!isRecord(parsed) || !isString(parsed.event) || !isRecord(parsed.data) || !isString(parsed.data.session_id)) return null;
  const data = parsed.data;
  switch (parsed.event) {
    case "session_snapshot": {
      if (!Array.isArray(data.messages)) return null;
      const messages = data.messages.map(toMessage).filter((m): m is ChatMessage => m !== null);
      const p = isRecord(data.in_progress) ? data.in_progress : {};
      const { message_id, text, sequence, replies_to } = p;
      const inProgress =
        isString(message_id) && isString(text) && typeof sequence === "number"
          ? { message_id, replies_to: isString(replies_to) ? replies_to : "", text, sequence }
          : null;
      return {
        event: "session_snapshot",
        data: { session_id: String(data.session_id), status: data.status as ChatSessionStatus, messages, in_progress: inProgress },
      };
    }
    case "token_chunk":
      if (!isString(data.message_id) || !isString(data.token) || typeof data.sequence !== "number") return null;
      return parsed as ChatServerEvent;
    case "user_message":
    case "generation_interrupted":
      if (!isString(data.message_id)) return null;
      return parsed as ChatServerEvent;
    case "generation_completed":
    case "generation_blocked":
    case "generation_failed":
      if (!isString(data.message_id) || !isString(data.text)) return null;
      return parsed as ChatServerEvent;
    case "chat_error":
      if (!isString(data.code) || !isString(data.message)) return null;
      return parsed as ChatServerEvent;
    default:
      return null;
  }
}

// --------------------------------------------------------------------------
// Reductor: eventos → conversación
// --------------------------------------------------------------------------

function upsert(messages: ChatMessage[], id: string, create: () => ChatMessage, update: (m: ChatMessage) => ChatMessage): ChatMessage[] {
  const index = messages.findIndex((m) => m.message_id === id);
  if (index === -1) return [...messages, update(create())];
  const next = messages.slice();
  next[index] = update(messages[index]);
  return next;
}

const emptyAssistant = (id: string): ChatMessage => ({
  message_id: id,
  role: "assistant",
  text: "",
  status: "streaming",
  replies_to: null,
  lastSequence: 0,
});

/**
 * Aplica un evento del servidor. Idempotente: el mismo evento dos veces (un
 * reenvío tras reconectar) no duplica mensajes ni texto, porque todo se
 * indexa por `message_id` y los tokens por `sequence`.
 */
export function applyChatEvent(state: ChatState, event: ChatServerEvent): ChatState {
  switch (event.event) {
    case "session_snapshot": {
      // Rehidratación: el servidor es la fuente de verdad; se sustituye todo.
      const { messages, in_progress, status, session_id } = event.data;
      const restored = in_progress
        ? [
            ...messages,
            { ...emptyAssistant(in_progress.message_id), text: in_progress.text, replies_to: in_progress.replies_to, lastSequence: in_progress.sequence },
          ]
        : messages;
      return {
        ...state,
        sessionId: session_id,
        sessionStatus: status,
        messages: restored,
        generating: in_progress ? in_progress.message_id : null,
        hydrated: true,
        error: null,
      };
    }
    case "user_message": {
      const { message_id, text, status } = event.data;
      if (state.messages.some((m) => m.message_id === message_id)) return state;
      const message: ChatMessage = { message_id, role: "user", text, status, replies_to: null, lastSequence: 0 };
      return { ...state, messages: [...state.messages, message], error: null };
    }
    case "token_chunk": {
      const { message_id, token, sequence } = event.data;
      const messages = upsert(state.messages, message_id, () => emptyAssistant(message_id), (m) =>
        sequence <= m.lastSequence || m.status !== "streaming"
          ? m // repetido (ya venía en el snapshot) o de una respuesta ya cerrada
          : { ...m, text: (m.text ?? "") + token, lastSequence: sequence },
      );
      return { ...state, messages, generating: message_id };
    }
    case "generation_interrupted": {
      // Se conserva el texto parcial tal cual: solo cambia el estado.
      const { message_id } = event.data;
      const messages = upsert(state.messages, message_id, () => emptyAssistant(message_id), (m) => ({ ...m, status: "interrupted" }));
      return { ...state, messages, sessionStatus: "interrupted", generating: state.generating === message_id ? null : state.generating };
    }
    case "generation_completed":
    case "generation_blocked":
    case "generation_failed": {
      // `text` es la versión final guardada (puede añadir la nota de memoria
      // del agente, o ser el texto fijo del guardarraíl si se retuvo).
      const { message_id, text, status } = event.data;
      const messages = upsert(state.messages, message_id, () => emptyAssistant(message_id), (m) => ({ ...m, text, status }));
      return {
        ...state,
        messages,
        sessionStatus: "active",
        generating: state.generating === message_id ? null : state.generating,
      };
    }
    case "chat_error":
      return { ...state, error: event.data.message };
  }
}

/** Frames que envía el navegador. */
export function userMessageFrame(sessionId: string, text: string): string {
  return JSON.stringify({ event: "user_message", data: { session_id: sessionId, text } });
}

export function interruptFrame(sessionId: string, newInput: string): string {
  return JSON.stringify({ event: "interrupt_requested", data: { session_id: sessionId, new_input: newInput } });
}

export function authFrame(token: string): string {
  return JSON.stringify({ event: "auth", data: { token } });
}

export function validateChatInput(text: string): string | null {
  const trimmed = text.trim();
  if (!trimmed) return "Escribe tu pregunta.";
  if (trimmed.length > MAX_CHAT_INPUT_LENGTH) return `La pregunta no puede superar ${MAX_CHAT_INPUT_LENGTH} caracteres.`;
  return null;
}

// --------------------------------------------------------------------------
// Conexión
// --------------------------------------------------------------------------

/** Cierres del servidor que no tiene sentido reintentar (events.py). */
export const CLOSE_UNAUTHENTICATED = 4401;
export const FINAL_CLOSE_CODES: Record<number, string> = {
  4403: "No tienes acceso a esta conversación.",
  4404: "Esta conversación ya no existe.",
  4409: "Esta conversación está cerrada.",
};

/**
 * URL del WebSocket. Va directa a la API, no por el rewrite `/api-proxy`:
 * los docs de Next.js 16 no garantizan que ese proxy reenvíe el upgrade a
 * WebSocket. Sin variable, el mismo host que la página en el puerto 8000.
 */
export function chatSocketUrl(sessionId: string, base: string | undefined, location: { protocol: string; hostname: string }): string {
  const root = (base && base.trim()) || `${location.protocol === "https:" ? "wss:" : "ws:"}//${location.hostname}:8000`;
  return `${root.replace(/\/$/, "")}/agent/chat/ws/${encodeURIComponent(sessionId)}`;
}
