import type {
  AskResponse,
  BriefResponse,
  CommunicationDraft,
  DraftPurpose,
  StaffRole,
  SupplierAlert,
  SupplierListItem,
} from "../types/supplierContracts";
import { requestJson } from "./http";

/**
 * Proveedores y contratos (services/supplier_contracts). Un brief detenido
 * por falta de BAA/DPA llega como ApiRequestError 409 con el motivo en el
 * mensaje; un 400 por campo, como ApiFieldError.
 */
const auth = { authRequired: true };
const post = (body: unknown): RequestInit => ({ method: "POST", body: JSON.stringify(body) });

export function getMyRole(): Promise<StaffRole> {
  return requestJson<StaffRole>("/supplier-contracts/me", undefined, auth);
}

export function listSuppliers(): Promise<SupplierListItem[]> {
  return requestJson<SupplierListItem[]>("/supplier-contracts/suppliers", undefined, auth);
}

export function listAlerts(mine: boolean): Promise<SupplierAlert[]> {
  return requestJson<SupplierAlert[]>(`/supplier-contracts/alerts${mine ? "?mine=true" : ""}`, undefined, auth);
}

export function runAlertSweep(): Promise<{ created: number }> {
  return requestJson<{ created: number }>("/supplier-contracts/alerts/sweep", { method: "POST" }, auth);
}

export function askSupervisor(question: string): Promise<AskResponse> {
  return requestJson<AskResponse>("/supplier-contracts/ask", post({ question }), auth);
}

export function createBrief(supplierId: string): Promise<BriefResponse> {
  return requestJson<BriefResponse>("/supplier-contracts/briefs", post({ supplier_id: supplierId }), auth);
}

export function clearSupplier(supplierId: string): Promise<unknown> {
  return requestJson("/supplier-contracts/compliance-clearances", post({ supplier_id: supplierId }), auth);
}

export function listDrafts(): Promise<CommunicationDraft[]> {
  return requestJson<CommunicationDraft[]>("/supplier-contracts/drafts", undefined, auth);
}

export function createDraft(supplierId: string, purpose: DraftPurpose): Promise<CommunicationDraft> {
  return requestJson<CommunicationDraft>("/supplier-contracts/drafts", post({ supplier_id: supplierId, purpose }), auth);
}

export function decideDraft(draftId: string, decision: "approved" | "rejected", comment: string): Promise<CommunicationDraft> {
  const body = { decision, comment: comment.trim() || null };
  return requestJson<CommunicationDraft>(`/supplier-contracts/drafts/${encodeURIComponent(draftId)}/decision`, post(body), auth);
}

export function sendDraft(draftId: string): Promise<CommunicationDraft> {
  return requestJson<CommunicationDraft>(`/supplier-contracts/drafts/${encodeURIComponent(draftId)}/send`, { method: "POST" }, auth);
}
