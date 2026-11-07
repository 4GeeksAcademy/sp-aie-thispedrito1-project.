// Pacientes SINTÉTICOS (/patients). Cada apertura de ficha exige un motivo y
// queda en el registro de auditoría.

export type PatientSection = "clinical" | "billing";

export interface PatientListItem {
  patient_ref: string;
  department: string | null;
  country: string;
  assigned_to_me: boolean;
  sections: PatientSection[];
  pending_claims: number;
}

export interface Claim {
  claim_ref: string;
  amount_cents: number;
  currency: string;
  status: "pending" | "approved" | "denied";
  reason_code: string | null;
  decided_at: string | null;
}

export interface PatientDetail {
  patient_ref: string;
  department: string | null;
  country: string;
  sections: PatientSection[];
  clinical: {
    full_name: string;
    date_of_birth: string;
    diagnosis: string;
    clinical_notes: string;
    documentation_draft: string | null;
  } | null;
  billing: {
    insurance_plan: string;
    outstanding_balance_cents: number;
    currency: string;
    claims: Claim[];
  } | null;
  audit_sequence: number;
}

export interface PatientCatalog {
  purposes: { code: string; label: string }[];
  claim_reason_codes: { approve: string[]; deny: string[] };
}

export const CLAIM_STATUS_LABELS: Record<Claim["status"], string> = {
  pending: "Pendiente",
  approved: "Aprobada",
  denied: "Denegada",
};

export const REASON_CODE_LABELS: Record<string, string> = {
  covered_service: "Servicio cubierto",
  medically_necessary: "Necesidad médica",
  not_covered: "No cubierto",
  missing_documentation: "Falta documentación",
  duplicate_claim: "Reclamación duplicada",
  eligibility: "Sin elegibilidad",
};

export function formatMoney(cents: number, currency: string): string {
  return new Intl.NumberFormat("es-ES", { style: "currency", currency }).format(cents / 100);
}

/** "123,45" o "123.45" → céntimos. null si no es un importe válido ≥ 0. */
export function parseAmountToCents(value: string): number | null {
  const normalized = value.trim().replace(",", ".");
  if (!/^\d+(\.\d{1,2})?$/.test(normalized)) return null;
  return Math.round(Number(normalized) * 100);
}
