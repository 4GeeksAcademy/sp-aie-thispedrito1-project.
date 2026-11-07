import type {
  Decision,
  FigureTrace,
  InboxItem,
  ReportDetail,
  ReportLanguage,
  ReportListItem,
  StaffRole,
} from "../types/financialClose";
import { decisionBody } from "../types/financialClose";
import { requestJson } from "./http";

/**
 * Cierre financiero (services/financial_close). Un rechazo sin comentarios o
 * con datos sensibles llega como ApiFieldError (400); una decisión repetida,
 * como ApiRequestError 409.
 */
const auth = { authRequired: true };

export function getMyRole(): Promise<StaffRole> {
  return requestJson<StaffRole>("/financial-close/me", undefined, auth);
}

export function listReports(): Promise<ReportListItem[]> {
  return requestJson<ReportListItem[]>("/financial-close/reports", undefined, auth);
}

export function getReport(id: string, lang: ReportLanguage): Promise<ReportDetail> {
  return requestJson<ReportDetail>(`/financial-close/reports/${encodeURIComponent(id)}?lang=${lang}`, undefined, auth);
}

export function traceFigure(id: string, figureId: string): Promise<FigureTrace> {
  // El id de cifra lleva "/" ("US.n/a.laboratory"): la ruta de la API lo acepta como path.
  const path = figureId.split("/").map(encodeURIComponent).join("/");
  return requestJson<FigureTrace>(`/financial-close/reports/${encodeURIComponent(id)}/figures/${path}`, undefined, auth);
}

export function decideReport(id: string, decision: Decision, comments: string): Promise<ReportListItem> {
  return requestJson<ReportListItem>(
    `/financial-close/reports/${encodeURIComponent(id)}/decision`,
    { method: "POST", body: JSON.stringify(decisionBody(decision, comments)) },
    auth,
  );
}

export function getInbox(): Promise<InboxItem[]> {
  return requestJson<InboxItem[]>("/financial-close/inbox", undefined, auth);
}
