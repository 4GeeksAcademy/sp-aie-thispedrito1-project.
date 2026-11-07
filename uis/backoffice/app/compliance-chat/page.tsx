"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import "./chat.css";
import { AsyncSection } from "../../components/AsyncSection";
import { useAsyncData } from "../../hooks/useAsyncData";
import { useChatSocket, type ChatConnection } from "../../hooks/useChatSocket";
import { createChatSession, openLatestChatSession } from "../../services/chatApi";
import { MAX_CHAT_INPUT_LENGTH, validateChatInput, type ChatMessage, type ChatSession } from "../../types/chat";

const STATUS_LABELS: Partial<Record<ChatMessage["status"], string>> = {
  interrupted: "Interrumpida",
  blocked: "Retenida por el guardarraíl",
  failed: "Sin respuesta",
};
const REDACTED_TEXT = "Mensaje retirado: contenía datos de un paciente y no se guarda (HIPAA / UK GDPR).";

function ConnectionIndicator({ status, retryInMs }: ChatConnection) {
  const label =
    status === "live"
      ? "En directo"
      : status === "reconnecting"
        ? `Sin conexión · reintento en ${Math.max(1, Math.round((retryInMs ?? 0) / 1000))} s`
        : status === "closed"
          ? "Desconectado"
          : "Conectando…";
  // Mismo indicador que /rfps (clases rfp-live-* de globals.css).
  return (
    <span className={`rfp-live rfp-live-${status === "closed" ? "connecting" : status}`} role="status">
      {label}
    </span>
  );
}

function Bubble({ message }: { message: ChatMessage }) {
  const label = STATUS_LABELS[message.status];
  const redacted = message.role === "user" && message.text === null;
  return (
    <li className={`chat-message chat-${message.role} chat-status-${message.status}`} data-testid={`message-${message.message_id}`}>
      <span className="chat-author">{message.role === "user" ? "Tú" : "Compliance assistant"}</span>
      <p className="chat-text">
        {redacted ? <em>{REDACTED_TEXT}</em> : message.text || (message.status === "interrupted" ? "(sin texto: interrumpida antes de empezar)" : "")}
        {message.status === "streaming" && <span className="chat-cursor" aria-hidden="true" />}
      </p>
      {label && <span className="chat-badge">{label}</span>}
    </li>
  );
}

/**
 * Chat en tiempo real con el Compliance assistant del área de Claire
 * Whitfield (Sistemas en Tiempo Real, Parte 2). Por WebSocket: la respuesta
 * llega palabra a palabra y se puede interrumpir a mitad enviando otro
 * mensaje; la respuesta cortada se queda visible como "Interrumpida" y la
 * redirigida llega como un mensaje nuevo. Solo personal interno.
 */
export default function ComplianceChatPage() {
  const loadSession = useCallback(() => openLatestChatSession(), []);
  const { data: initial, isLoading, error, reload } = useAsyncData<ChatSession>(loadSession, "No se pudo abrir la conversación.");
  const [session, setSession] = useState<ChatSession | null>(null);
  const current = session ?? initial ?? null;

  const { state, connection, sendMessage } = useChatSocket(current?.session_id ?? null);
  const [draft, setDraft] = useState("");
  const [inputError, setInputError] = useState<string | null>(null);
  const [isCreating, setIsCreating] = useState(false);
  const endRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    endRef.current?.scrollIntoView?.({ block: "end" });
  }, [state.messages]);

  const isLive = connection.status === "live";
  const isGenerating = state.generating !== null;

  const submit = (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const problem = validateChatInput(draft);
    if (problem) {
      setInputError(problem);
      return;
    }
    if (!sendMessage(draft.trim())) {
      setInputError("Sin conexión con el asistente: tu mensaje sigue aquí, envíalo cuando vuelva la conexión.");
      return;
    }
    setDraft("");
    setInputError(null);
  };

  const startNew = async () => {
    setIsCreating(true);
    try {
      setSession(await createChatSession());
    } catch {
      setInputError("No se pudo abrir una conversación nueva. Inténtalo de nuevo.");
    } finally {
      setIsCreating(false);
    }
  };

  return (
    <main className="shell chat-page">
      <div className="chat-header">
        <div>
          <h1>Chat de compliance</h1>
          <p className="chat-intro">
            Pregunta qué está permitido bajo HIPAA (EE. UU.) y UK GDPR (Reino Unido). La respuesta aparece mientras se
            escribe; si va por el camino equivocado, escribe otra cosa y pulsa «Interrumpir y enviar».
          </p>
        </div>
        <div className="chat-header-actions">
          <ConnectionIndicator {...connection} />
          <button type="button" onClick={() => void startNew()} disabled={isCreating}>
            {isCreating ? "Abriendo…" : "Nueva conversación"}
          </button>
        </div>
      </div>

      <p className="chat-warning" role="note">
        Solo para personal interno. No escribas nombres, fechas de nacimiento, números de historia clínica ni ningún
        otro dato de pacientes.
      </p>

      <AsyncSection isLoading={isLoading} error={error} onRetry={reload} loadingLabel="Abriendo la conversación…">
        <section className="panel chat-panel" aria-live="polite">
          {connection.status === "closed" ? (
            <div className="chat-empty">
              <p>{connection.closedReason}</p>
              <button type="button" className="primary" onClick={() => void startNew()} disabled={isCreating}>
                Empezar una conversación nueva
              </button>
            </div>
          ) : state.messages.length === 0 ? (
            <p className="chat-empty">
              {state.hydrated ? "Aún no hay mensajes. Por ejemplo: «¿Qué debe incluir un BAA con un proveedor?»" : "Recuperando la conversación…"}
            </p>
          ) : (
            <ol className="chat-messages">
              {state.messages.map((message) => (
                <Bubble key={message.message_id} message={message} />
              ))}
            </ol>
          )}
          <div ref={endRef} />
        </section>

        <form onSubmit={submit} className="panel chat-form" noValidate>
          <label>
            Tu mensaje
            <textarea
              value={draft}
              rows={3}
              maxLength={MAX_CHAT_INPUT_LENGTH}
              disabled={connection.status === "closed"}
              onChange={(event) => {
                setDraft(event.target.value);
                setInputError(null);
              }}
              onKeyDown={(event) => {
                if (event.key === "Enter" && !event.shiftKey) {
                  event.preventDefault();
                  event.currentTarget.form?.requestSubmit();
                }
              }}
            />
          </label>
          {(inputError || state.error) && (
            <p className="chat-error" role="alert">
              {inputError || state.error}
            </p>
          )}
          <div className="chat-form-actions">
            <span className="chat-hint">
              {isGenerating ? "El asistente está respondiendo: enviar ahora lo interrumpe." : "Intro para enviar · Mayús+Intro para salto de línea"}
            </span>
            <button type="submit" className="primary" disabled={!isLive}>
              {isGenerating ? "Interrumpir y enviar" : "Enviar"}
            </button>
          </div>
        </form>
      </AsyncSection>
    </main>
  );
}
