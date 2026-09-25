/**
 * Contrato de POST /knowledge/query (base de conocimiento RAG, Hito 7) y
 * ayudas puras de la pantalla /knowledge.
 *
 * La API devuelve solo el texto que redacta el modelo: nunca fragmentos,
 * fuentes en bruto ni puntuaciones de similitud.
 */
export type KnowledgeQueryRequest = { question: string };
export type KnowledgeQueryResponse = { answer: string };

/** Mismo límite que services/knowledge/schemas.py (MAX_QUESTION_LENGTH). */
export const MAX_QUESTION_LENGTH = 1000;

/** Preguntas de ejemplo del CONTEXT del hito (sección 7). */
export const EXAMPLE_QUESTIONS = [
  "¿Cobran cargo por cancelación con 12 horas de anticipación?",
  "¿Qué debo traer a mi primera cita?",
  "¿Aceptan Medicaid en todas las clínicas?",
  "¿Cuánto tarda una referencia a un especialista?",
];

/** Devuelve el mensaje de error de la pregunta, o `null` si es válida. */
export function validateQuestion(question: string): string | null {
  const trimmed = question.trim();
  if (!trimmed) return "Escribe una pregunta antes de consultar.";
  if (trimmed.length > MAX_QUESTION_LENGTH) {
    return `La pregunta debe tener ${MAX_QUESTION_LENGTH} caracteres o menos.`;
  }
  return null;
}

/**
 * Quita el formato Markdown que el modelo añada pese al prompt (que pide
 * texto plano): negritas/cursivas con `**`/`__` y almohadillas de título.
 * La pantalla muestra la respuesta como texto, así que esos símbolos se
 * verían literalmente. Las viñetas "- " se conservan.
 */
export function toPlainText(answer: string): string {
  return answer
    .replace(/(\*\*|__)(.+?)\1/g, "$2")
    .replace(/^#{1,6}\s+/gm, "")
    .trim();
}
