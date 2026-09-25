"use client";

import { useRef, useState } from "react";

import { askKnowledgeBase, describeKnowledgeError } from "../../services/knowledgeApi";
import { EXAMPLE_QUESTIONS, MAX_QUESTION_LENGTH, toPlainText, validateQuestion } from "../../types/knowledge";

type Result = { question: string; answer: string };

/**
 * Asistente de la base de conocimiento (Hito 7, RAG) para los coordinadores
 * de pacientes: pregunta en lenguaje natural → respuesta redactada por el
 * modelo a partir de las políticas internas (POST /knowledge/query).
 *
 * Tres estados explícitos: consultando, error con "Reintentar" y respuesta.
 * Un fallo nunca se muestra como una respuesta vacía.
 */
export default function KnowledgeAssistantPage() {
  const [question, setQuestion] = useState("");
  const [validationError, setValidationError] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<Result | null>(null);
  // Última pregunta enviada: "Reintentar" repite esa, aunque el campo haya cambiado.
  const lastAsked = useRef<string | null>(null);
  // Solo la petición más reciente puede pintar su resultado.
  const requestId = useRef(0);

  const ask = async (text: string) => {
    const message = validateQuestion(text);
    if (message) {
      setValidationError(message);
      return;
    }
    const trimmed = text.trim();
    const id = ++requestId.current;
    lastAsked.current = trimmed;
    setValidationError(null);
    setError(null);
    setResult(null);
    setIsLoading(true);
    try {
      const answer = await askKnowledgeBase(trimmed);
      if (id === requestId.current) setResult({ question: trimmed, answer });
    } catch (requestError) {
      if (id === requestId.current) setError(describeKnowledgeError(requestError));
    } finally {
      if (id === requestId.current) setIsLoading(false);
    }
  };

  const submit = (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    void ask(question);
  };

  const pickExample = (example: string) => {
    setQuestion(example);
    setValidationError(null);
  };

  return (
    <main className="shell" style={{ padding: "24px 0 48px" }}>
      <h1>Asistente de políticas</h1>
      <p style={{ color: "var(--muted)", maxWidth: 760 }}>
        Pregunta por seguros aceptados, citas y cancelaciones, referencias internas o lo que necesita un
        paciente nuevo. La respuesta la redacta un modelo de IA usando solo las políticas internas de
        HealthCore; si no las cubren, te lo dirá en vez de inventar.
      </p>

      <form onSubmit={submit} className="panel" style={{ maxWidth: 860 }} noValidate>
        <label>
          Tu pregunta
          <textarea
            value={question}
            rows={3}
            maxLength={MAX_QUESTION_LENGTH}
            onChange={(event) => {
              setQuestion(event.target.value);
              setValidationError(null);
            }}
            placeholder="Por ejemplo: ¿cobran cargo por cancelación con 12 horas de anticipación?"
          />
          <span style={{ color: "var(--muted)", fontSize: 12 }}>
            {question.trim().length}/{MAX_QUESTION_LENGTH}
          </span>
          {validationError && <span className="error-text">{validationError}</span>}
        </label>

        <div style={{ display: "flex", flexWrap: "wrap", gap: 8, marginTop: 10 }} aria-label="Preguntas de ejemplo">
          {EXAMPLE_QUESTIONS.map((example) => (
            <button
              key={example}
              type="button"
              onClick={() => pickExample(example)}
              disabled={isLoading}
              style={{ background: "transparent", color: "var(--text)", border: "1px solid var(--line)", fontWeight: 400 }}
            >
              {example}
            </button>
          ))}
        </div>

        <p
          role="note"
          style={{
            marginTop: 14,
            border: "1px solid var(--warning)",
            color: "var(--warning)",
            borderRadius: 10,
            padding: "8px 12px",
            fontSize: 13,
            fontWeight: 600,
          }}
        >
          No escribas datos que identifiquen a un paciente (nombre, fecha de nacimiento, número de historia
          clínica). Pregunta por la política, no por el caso concreto.
        </p>

        <div style={{ marginTop: 12 }}>
          <button type="submit" disabled={isLoading}>
            {isLoading ? "Consultando…" : "Preguntar"}
          </button>
        </div>
      </form>

      <section aria-live="polite" style={{ maxWidth: 860, marginTop: 18 }}>
        {isLoading && (
          <div className="panel" style={{ color: "var(--muted)" }}>
            Consultando la base de conocimiento…
          </div>
        )}

        {error && !isLoading && (
          <div className="panel" role="alert">
            <p className="error-text" style={{ marginTop: 0 }}>
              {error}
            </p>
            <button type="button" onClick={() => lastAsked.current && void ask(lastAsked.current)}>
              Reintentar
            </button>
          </div>
        )}

        {result && !isLoading && (
          <article className="panel">
            <p style={{ color: "var(--muted)", fontSize: 13, marginTop: 0 }}>
              Pregunta: <span style={{ color: "var(--text)" }}>{result.question}</span>
            </p>
            <div style={{ whiteSpace: "pre-wrap", lineHeight: 1.55 }} data-testid="knowledge-answer">
              {toPlainText(result.answer)}
            </div>
            <p style={{ color: "var(--muted)", fontSize: 12, marginBottom: 0 }}>
              Respuesta generada por IA a partir de las políticas internas. Ante cualquier duda sobre una
              cobertura, confírmala con facturación.
            </p>
          </article>
        )}
      </section>
    </main>
  );
}
