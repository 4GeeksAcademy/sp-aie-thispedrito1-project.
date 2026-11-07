import type { ChatSession } from "../types/chat";
import { requestJson } from "./http";

/** Sesiones de chat de quien llama, la más reciente primero. */
export function listChatSessions(): Promise<ChatSession[]> {
  return requestJson<ChatSession[]>("/agent/chat/sessions", { method: "GET" }, { authRequired: true });
}

export function createChatSession(): Promise<ChatSession> {
  return requestJson<ChatSession>("/agent/chat/sessions", { method: "POST" }, { authRequired: true });
}

/**
 * La conversación con la que se abre la pantalla: la última que siga
 * abierta (así recargar la página no pierde el hilo) o una nueva.
 */
export async function openLatestChatSession(): Promise<ChatSession> {
  const sessions = await listChatSessions();
  const open = sessions.find((session) => session.status !== "closed");
  return open ?? createChatSession();
}
