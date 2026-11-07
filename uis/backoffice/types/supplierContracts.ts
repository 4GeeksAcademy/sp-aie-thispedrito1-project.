// Proveedores y contratos (services/supplier_contracts). Tipos del contrato de
// la API y ayudas puras de la pantalla /supplier-contracts.

export type OrgRole = "clinical_ops_director" | "compliance_officer" | "ceo";

export interface StaffRole {
  org_role: OrgRole | null;
  display_name: string | null;
}

export interface SupplierListItem {
  supplier_id: string;
  name: string;
  country: string;
  category: string;
  category_label: string;
  currency: string;
  processes_patient_data: boolean;
  contract_id: string | null;
  latest_price: string | null;
  latest_month: string | null;
  unit: string | null;
  monthly_volume: number | null;
  source: string;
}

export interface SupplierAlert {
  alert_key: string;
  kind: string;
  supplier_id: string;
  contract_id: string | null;
  recipient_role: OrgRole;
  recipient: string | null;
  due_date: string | null;
  summary: string;
  refs: string[];
  raised_by: string;
  created_at: string;
}

export interface Consultation {
  agent_id: string;
  intent: string;
  supplier_id: string | null;
  status: string;
}

export interface AskResponse {
  request_id: string;
  answer: string;
  refused: boolean;
  plan_source: "model" | "rules";
  consultations: Consultation[];
  citations: string[];
  missing: string[];
  limit_reached: boolean;
  brief_supplier_id: string | null;
}

export interface BriefResponse {
  request_id: string;
  status: "ready" | "blocked" | "not_found";
  supplier_id: string;
  supplier_name: string | null;
  message: string | null;
  recommendation: string[];
  citations: string[];
  missing: string[];
  consultations: Consultation[];
  markdown: string;
}

export type DraftPurpose = "price_increase_objection" | "renegotiation_request" | "non_renewal_notice";
export type DraftStatus = "pending_approval" | "approved" | "rejected" | "sent";

export interface DraftApproval {
  role: OrgRole;
  approver: string;
  decision: "pending" | "approved" | "rejected";
  comment: string | null;
}

export interface CommunicationDraft {
  draft_id: string;
  supplier_id: string;
  contract_id: string | null;
  purpose: DraftPurpose;
  subject: string;
  body: string;
  status: DraftStatus;
  draft_source: "model" | "template";
  drafted_by: string;
  refs: string[];
  approval_reasons: string[];
  approvals: DraftApproval[];
  created_at: string;
  sent_at: string | null;
}

export const ALERT_LABELS: Record<string, string> = {
  price_increase: "Subida de precio",
  expiry_90: "Vence en ≤ 90 días",
  expiry_30: "Vence en ≤ 30 días",
  notice_deadline: "Fecha límite de preaviso",
  data_protection_missing: "Falta BAA/DPA",
};

export const PURPOSE_LABELS: Record<DraftPurpose, string> = {
  price_increase_objection: "Objetar una subida de precio",
  renegotiation_request: "Pedir revisión de precio",
  non_renewal_notice: "Aviso de no renovación",
};

export const STATUS_LABELS: Record<DraftStatus, string> = {
  pending_approval: "Pendiente de aprobación",
  approved: "Aprobado (sin enviar)",
  rejected: "Rechazado",
  sent: "Enviado",
};

export const AGENT_LABELS: Record<string, string> = {
  "agent:supplier-supervisor": "Supervisor",
  "agent:pricing-specialist": "Especialista de precios",
  "agent:contracts-specialist": "Especialista de contratos",
};

export const EXAMPLE_QUESTIONS: string[] = [
  "¿ClearPath Labs puede subirnos un 7%?",
  "¿Cuánto nos cuesta al año la subida de ClearPath Labs?",
  "¿Qué alternativa tenemos a ClearPath Labs y a qué precio?",
  "¿Qué proveedores procesan datos de pacientes sin acuerdo de protección de datos firmado?",
  "¿Cuándo tenemos que avisar a ClearPath Labs si no queremos renovar?",
  "¿Qué dice el contrato de BritMed Supplies?",
  "Prepárame un brief para renegociar con ClearPath Labs.",
];

/** Alertas urgentes primero (protección de datos, preaviso, vencimiento a 30). */
const ALERT_PRIORITY: Record<string, number> = {
  data_protection_missing: 0,
  notice_deadline: 1,
  expiry_30: 2,
  price_increase: 3,
  expiry_90: 4,
};

export function sortAlerts(alerts: SupplierAlert[]): SupplierAlert[] {
  return [...alerts].sort(
    (a, b) =>
      (ALERT_PRIORITY[a.kind] ?? 9) - (ALERT_PRIORITY[b.kind] ?? 9) ||
      (a.due_date ?? "9999").localeCompare(b.due_date ?? "9999"),
  );
}

/** ¿Puede este rol decidir ahora sobre el borrador? */
export function canDecide(draft: CommunicationDraft, role: OrgRole | null): boolean {
  if (!role || draft.status !== "pending_approval") return false;
  return draft.approvals.some((a) => a.role === role && a.decision === "pending");
}

/** Solo Operaciones Clínicas marca como enviado, y solo con todo aprobado. */
export function canSend(draft: CommunicationDraft, role: OrgRole | null): boolean {
  return role === "clinical_ops_director" && draft.status === "approved" && draft.approvals.every((a) => a.decision === "approved");
}

export function approvalProgress(draft: CommunicationDraft): string {
  const done = draft.approvals.filter((a) => a.decision === "approved").length;
  return `${done}/${draft.approvals.length} aprobaciones`;
}
