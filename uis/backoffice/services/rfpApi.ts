import type { RfpFinalDocument, RfpTicketDetail, RfpTicketListItem, RfpTicketQueued, RfpTraceEvent } from "../types/rfp";
import { requestJson } from "./http";

/**
 * Recepción de RFPs (services/rfp_intake). La subida responde 202 en cuanto
 * el ticket existe; el análisis corre en el worker y la pantalla consulta el
 * detalle hasta que deja de estar `analizando`.
 */
export function uploadRfp(file: File): Promise<RfpTicketQueued> {
  const body = new FormData();
  body.append("file", file);
  return requestJson<RfpTicketQueued>("/rfp-tickets", { method: "POST", body }, { authRequired: true });
}

export function listRfpTickets(): Promise<RfpTicketListItem[]> {
  return requestJson<RfpTicketListItem[]>("/rfp-tickets", undefined, { authRequired: true });
}

export function getRfpTicket(ticketId: string): Promise<RfpTicketDetail> {
  return requestJson<RfpTicketDetail>(`/rfp-tickets/${encodeURIComponent(ticketId)}`, undefined, {
    authRequired: true,
  });
}

/**
 * Parte 3. Las decisiones responden 202: la API las valida y las guarda, y
 * el worker reanuda el grafo. Un 400 llega como ApiFieldError (por campo).
 */
export interface DecisionQueued {
  ticket_id: string;
  department_id: string | null;
  approval_status: string;
}

function ticketPath(ticketId: string): string {
  return `/rfp-tickets/${encodeURIComponent(ticketId)}`;
}

export function submitSectionDecision(
  ticketId: string,
  departmentId: string,
  body: { decision: string; comments: string | null; confirmed_capacity: number | null; draft_digest: string },
): Promise<DecisionQueued> {
  return requestJson<DecisionQueued>(
    `${ticketPath(ticketId)}/sections/${encodeURIComponent(departmentId)}/decision`,
    { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) },
    { authRequired: true },
  );
}

export function submitArbitrationDecision(
  ticketId: string,
  body: { option: string; new_population: number | null; note: string | null },
): Promise<DecisionQueued> {
  return requestJson<DecisionQueued>(
    `${ticketPath(ticketId)}/arbitration`,
    { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) },
    { authRequired: true },
  );
}

export function getFinalDocument(ticketId: string): Promise<RfpFinalDocument> {
  return requestJson<RfpFinalDocument>(`${ticketPath(ticketId)}/final-document`, undefined, { authRequired: true });
}

export function getRfpTrace(ticketId: string): Promise<RfpTraceEvent[]> {
  return requestJson<RfpTraceEvent[]>(`${ticketPath(ticketId)}/trace`, undefined, { authRequired: true });
}
