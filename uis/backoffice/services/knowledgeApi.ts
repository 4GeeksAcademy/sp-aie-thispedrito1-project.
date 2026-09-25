import { MAX_QUESTION_LENGTH, type KnowledgeQueryRequest, type KnowledgeQueryResponse } from "../types/knowledge";
import { ApiFieldError, ApiRequestError, requestJson } from "./http";

/** Pregunta al asistente de la base de conocimiento (services/knowledge). */
export async function askKnowledgeBase(question: string): Promise<string> {
  const body: KnowledgeQueryRequest = { question: question.trim() };
  const response = await requestJson<KnowledgeQueryResponse>(
    "/knowledge/query",
    { method: "POST", body: JSON.stringify(body) },
    { authRequired: true },
  );
  return response.answer;
}

/**
 * Mensaje para el coordinador a partir del error de la petición. Un 503
 * significa que el asistente (Qdrant o el modelo) no está disponible, no
 * que la pregunta esté mal: se dice así para que no la reformule en vano.
 */
export function describeKnowledgeError(error: unknown): string {
  if (error instanceof ApiRequestError && error.status === 503) {
    return "El asistente no está disponible en este momento. Inténtalo de nuevo en unos minutos o consulta el documento de políticas.";
  }
  if (error instanceof ApiFieldError) {
    return `La pregunta no es válida. Escríbela de nuevo (máximo ${MAX_QUESTION_LENGTH} caracteres).`;
  }
  if (error instanceof Error && error.message) {
    return error.message;
  }
  return "No se pudo obtener la respuesta. Inténtalo de nuevo.";
}
