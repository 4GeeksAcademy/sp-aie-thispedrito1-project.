/**
 * Contrato de /rfp-tickets (services/rfp_intake/schemas.py) y ayudas puras
 * de la pantalla /rfps. Estados de la Parte 1 del Hito 9 + `error`.
 */

export type RfpStatus = "analizando" | "descartado" | "analisis_completo" | "error";
export type DiscardReason = "not_rfp" | "phi_detected";

export interface RfpTicketQueued {
  ticket_id: string;
  rfp_id: string;
  status: RfpStatus;
}

export interface RfpTicketListItem {
  ticket_id: string;
  rfp_id: string;
  status: RfpStatus;
  discard_reason: DiscardReason | null;
  requires_compliance_review: boolean;
  client_name: string | null;
  client_country: "US" | "UK" | null;
  created_at: string;
  updated_at: string;
}

export interface WhoToAsk {
  department_id: string;
  department: string;
  owner: string;
  request: string;
}

export interface RfpConflict {
  department_ids: string[];
  description: string;
}

export interface RfpMetadata {
  document_kind: string | null;
  classifier_confidence: number | null;
  client_name: string | null;
  client_country: "US" | "UK" | null;
  currency: "USD" | "GBP" | null;
  program_type: string[];
  covered_population: number | null;
  covered_population_unit: string | null;
  deadline: string | null;
  deadline_text: string | null;
  budget_range: string | null;
  departments_needed: string[];
  unmapped_departments: string[];
  open_questions: string[];
  word_count: number | null;
  sentence_count: number | null;
  estimated_tokens: number | null;
  flesch_kincaid_grade: number | null;
  gunning_fog: number | null;
  flesch_reading_ease: number | null;
  readability_note: string | null;
  summary: string | null;
  who_to_ask: WhoToAsk[];
  conflicts: RfpConflict[];
}

export interface RfpDepartmentSection {
  department_id: string;
  department: string;
  owner: string;
  key_aspects: string[];
  open_questions: string[];
  unsupported_claims_removed: number;
}

export interface RfpTicketDetail extends RfpTicketListItem {
  classifier_reason: string | null;
  phi_categories: string[];
  error_message: string | null;
  attempts: number;
  next_stage: string | null;
  completed_at: string | null;
  metadata: RfpMetadata | null;
  sections: RfpDepartmentSection[];
}

export const MAX_PDF_BYTES = 10 * 1024 * 1024;
/** Cada cuánto se consulta un ticket que sigue `analizando`. */
export const POLL_INTERVAL_MS = 3000;

export const STATUS_LABELS: Record<RfpStatus, string> = {
  analizando: "Analizando",
  descartado: "Descartado",
  analisis_completo: "Análisis completo",
  error: "Error",
};

/** Clase CSS del badge (definidas en globals.css con tokens del tema). */
export const STATUS_CLASS: Record<RfpStatus, string> = {
  analizando: "rfp-status-analyzing",
  descartado: "rfp-status-discarded",
  analisis_completo: "rfp-status-complete",
  error: "rfp-status-error",
};

const PROGRAM_LABELS: Record<string, string> = {
  occupational_health: "Salud ocupacional",
  corporate_wellness: "Bienestar corporativo",
  referral_network: "Red de referidos",
  other: "Otro",
};

const POPULATION_UNITS: Record<string, string> = {
  employees: "empleados",
  students: "estudiantes",
  members: "miembros",
};

const PHI_LABELS: Record<string, string> = {
  patient_name: "nombre de paciente",
  medical_record_number: "número de historia clínica",
  ssn: "número de la seguridad social",
  nhs_number: "número del NHS",
  insurance_number: "número de póliza",
  date_of_birth: "fecha de nacimiento",
  clinical_content: "información clínica",
};

export function isInProgress(status: RfpStatus): boolean {
  return status === "analizando";
}

/** Validación en el navegador, antes de enviar nada (el servidor repite la
 *  comprobación por contenido: esto solo ahorra una subida inútil). */
export function validatePdfFile(file: File | null): string | null {
  if (!file) return "Selecciona un PDF.";
  const looksLikePdf = file.type === "application/pdf" || file.name.toLowerCase().endsWith(".pdf");
  if (!looksLikePdf) return "El archivo debe ser un PDF.";
  if (file.size === 0) return "El archivo está vacío.";
  if (file.size > MAX_PDF_BYTES) return "El PDF supera el máximo de 10 MB.";
  return null;
}

export function describeOutcome(ticket: Pick<RfpTicketDetail, "status" | "discard_reason" | "classifier_reason" | "error_message">): string {
  switch (ticket.status) {
    case "analizando":
      return "Convirtiendo el PDF y analizando la RFP. Esta página se actualiza sola.";
    case "analisis_completo":
      return "Análisis terminado: abajo está qué pedir a cada departamento.";
    case "error":
      return ticket.error_message ?? "El análisis falló por un problema técnico.";
    case "descartado":
      if (ticket.discard_reason === "phi_detected") {
        return "El documento contiene datos de pacientes. Se ha detenido sin analizarlo y queda pendiente de revisión por Compliance (Claire Whitfield).";
      }
      return ticket.classifier_reason
        ? `No es una RFP: ${ticket.classifier_reason}`
        : "El clasificador determinó que el documento no es una RFP.";
  }
}

export function formatPhiCategories(categories: string[]): string {
  return categories.map((category) => PHI_LABELS[category] ?? category).join(", ");
}

export function formatProgramTypes(types: string[]): string {
  return types.length ? types.map((type) => PROGRAM_LABELS[type] ?? type).join(" + ") : "—";
}

export function formatPopulation(count: number | null, unit: string | null): string {
  if (count === null) return "Sin confirmar";
  const unitLabel = unit ? POPULATION_UNITS[unit] ?? unit : "";
  return `${count.toLocaleString("es-ES")} ${unitLabel}`.trim();
}
