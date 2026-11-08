// Lecturas del Registro de Auditoría y del módulo de pacientes sintéticos.
// Todo por requestJson: un 403 llega como ApiRequestError con el mensaje ya
// en español (FORBIDDEN_MESSAGES).

import type {
  AnomaliesResult,
  AuditActor,
  AuditCatalog,
  AuditPage,
  IntegrityResult,
  MyPatientAccessPage,
} from "../types/audit";
import type { PatientCatalog, PatientDetail, PatientListItem } from "../types/patients";
import { requestJson } from "./http";

const AUTH = { authRequired: true } as const;

function send<T>(path: string, method: string, body?: unknown): Promise<T> {
  return requestJson<T>(path, { method, body: body === undefined ? undefined : JSON.stringify(body) }, AUTH);
}

export const auditApi = {
  catalog: () => requestJson<AuditCatalog>("/audit-log/catalog", undefined, AUTH),
  list: (params: URLSearchParams) => requestJson<AuditPage>(`/audit-log?${params.toString()}`, undefined, AUTH),
  actors: () => requestJson<AuditActor[]>("/audit-log/actors", undefined, AUTH),
  anomalies: () => requestJson<AnomaliesResult>("/audit-log/anomalies", undefined, AUTH),
  integrity: () => requestJson<IntegrityResult>("/audit-log/integrity", undefined, AUTH),
  myPatients: (before?: number | null) =>
    requestJson<MyPatientAccessPage>(`/audit-log/my-patients${before ? `?before=${before}` : ""}`, undefined, AUTH),
};

const ref = (patientRef: string) => encodeURIComponent(patientRef);

export const patientsApi = {
  catalog: () => requestJson<PatientCatalog>("/patients/catalog", undefined, AUTH),
  list: () => requestJson<PatientListItem[]>("/patients", undefined, AUTH),
  open: (patientRef: string, purpose: string) =>
    requestJson<PatientDetail>(`/patients/${ref(patientRef)}?purpose=${encodeURIComponent(purpose)}`, undefined, AUTH),
  updateClinical: (patientRef: string, body: { purpose: string; diagnosis?: string; clinical_notes?: string }) =>
    send<PatientDetail>(`/patients/${ref(patientRef)}/clinical`, "PATCH", body),
  updateBilling: (
    patientRef: string,
    body: { purpose: string; insurance_plan?: string; outstanding_balance_cents?: number },
  ) => send<PatientDetail>(`/patients/${ref(patientRef)}/billing`, "PATCH", body),
  decideClaim: (patientRef: string, claimRef: string, body: { decision: "approve" | "deny"; reason_code: string }) =>
    send<PatientDetail>(`/patients/${ref(patientRef)}/claims/${encodeURIComponent(claimRef)}/decision`, "POST", body),
  draftDocumentation: (patientRef: string, purpose: string) =>
    send<PatientDetail>(`/patients/${ref(patientRef)}/documentation-draft`, "POST", { purpose }),
  exportCsv: (purpose: string) =>
    requestJson<string>(
      "/patients/export",
      { method: "POST", body: JSON.stringify({ purpose }) },
      { ...AUTH, responseType: "text" },
    ),
};
