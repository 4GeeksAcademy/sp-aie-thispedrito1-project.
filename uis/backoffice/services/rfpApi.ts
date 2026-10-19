import type { RfpTicketDetail, RfpTicketListItem, RfpTicketQueued } from "../types/rfp";
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
