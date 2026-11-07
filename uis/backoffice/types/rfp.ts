/**
 * Contrato de /rfp-tickets (services/rfp_intake/schemas.py) y ayudas puras
 * de la pantalla /rfps. Estados de las Partes 1 y 2 del Hito 9, más `error`
 * y `evaluacion_completa` (añadidos por decisión del usuario).
 */

export type RfpStatus =
  | "analizando"
  | "descartado"
  | "analisis_completo"
  | "error"
  | "generando_borrador"
  | "en_evaluación"
  | "needs_human_review"
  | "evaluacion_completa";

export type SectionDraftStatus =
  | "pendiente"
  | "generando_borrador"
  | "en_evaluación"
  | "aprobada_por_evaluadores"
  | "needs_human_review";
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

/** EvaluationResult (README de la Parte 2; data/pipelines/rfp_intake/evaluators.py). */
export interface EvaluationResult {
  section_id: string;
  department_id: string;
  iteration: number;
  readability: { pass: boolean; score: number | null; details: { problems?: string[]; words?: number; words_per_sentence?: number } };
  relevance: { pass: boolean; missing_aspects: string[] };
  compliance: {
    pass: boolean;
    rule_ids: string[];
    violations: { rule_id: string; message: string }[];
    contains_phi: boolean;
  };
  overall_pass: boolean;
  feedback_for_generator: string;
  retryable: boolean;
  evaluated_at: string;
}

export interface RfpDepartmentSection {
  department_id: string;
  department: string;
  owner: string;
  key_aspects: string[];
  open_questions: string[];
  unsupported_claims_removed: number;
  draft_status: SectionDraftStatus | null;
  draft_iterations: number;
  draft_content: string | null;
  drafted_at: string | null;
  evaluation_results: EvaluationResult | null;
  evaluation_history: EvaluationResult[];
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
  generando_borrador: "Generando borrador",
  "en_evaluación": "En evaluación",
  needs_human_review: "Revisión humana",
  evaluacion_completa: "Borradores aprobados",
};

/** Clase CSS del badge (definidas en globals.css con tokens del tema). */
export const STATUS_CLASS: Record<RfpStatus, string> = {
  analizando: "rfp-status-analyzing",
  descartado: "rfp-status-discarded",
  analisis_completo: "rfp-status-complete",
  error: "rfp-status-error",
  generando_borrador: "rfp-status-analyzing",
  "en_evaluación": "rfp-status-analyzing",
  needs_human_review: "rfp-status-review",
  evaluacion_completa: "rfp-status-complete",
};

export const SECTION_STATUS_LABELS: Record<SectionDraftStatus, string> = {
  pendiente: "Pendiente",
  generando_borrador: "Redactando",
  "en_evaluación": "En evaluación",
  aprobada_por_evaluadores: "Aprobada por los evaluadores",
  needs_human_review: "Provisional · revisión humana",
};

export const SECTION_STATUS_CLASS: Record<SectionDraftStatus, string> = {
  pendiente: "rfp-status-discarded",
  generando_borrador: "rfp-status-analyzing",
  "en_evaluación": "rfp-status-analyzing",
  aprobada_por_evaluadores: "rfp-status-complete",
  needs_human_review: "rfp-status-review",
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

const WORKING_STATUSES: RfpStatus[] = ["analizando", "generando_borrador", "en_evaluación"];

export function isInProgress(status: RfpStatus): boolean {
  return WORKING_STATUSES.includes(status);
}

/** La pantalla sigue consultando mientras hay trabajo en curso y también en
 *  `analisis_completo`: la Parte 2 arranca sola justo después. */
export function shouldPoll(status: RfpStatus): boolean {
  return isInProgress(status) || status === "analisis_completo";
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
      return "Análisis terminado: abajo está qué pedir a cada departamento. La redacción de borradores arranca sola en unos segundos.";
    case "generando_borrador":
      return "Redactando un borrador por departamento a partir del análisis. Esta página se actualiza sola.";
    case "en_evaluación":
      return "Revisando cada borrador (legibilidad, pertinencia y cumplimiento) y corrigiendo los que fallan.";
    case "evaluacion_completa":
      return "Las tres secciones superaron la evaluación. Pendiente de aprobación por cada responsable.";
    case "needs_human_review":
      return "Alguna sección no superó la evaluación tras el máximo de intentos: su borrador es provisional y debe revisarse antes de usarse.";
    case "error":
      return ticket.error_message ?? "El proceso falló por un problema técnico.";
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

export interface EvaluationCheck {
  label: string;
  passed: boolean;
  detail: string;
}

/** Los tres evaluadores en lenguaje de negocio, para la tarjeta de la sección. */
export function describeEvaluation(result: EvaluationResult): EvaluationCheck[] {
  const problems = result.readability.details.problems ?? [];
  const score = result.readability.score;
  return [
    {
      label: "Legibilidad",
      passed: result.readability.pass,
      detail: [score === null ? "Sin nota" : `Fernández Huerta ${score.toFixed(0)}`, ...problems].join(" · "),
    },
    {
      label: "Pertinencia",
      passed: result.relevance.pass,
      detail: result.relevance.missing_aspects.length
        ? `Falta: ${result.relevance.missing_aspects.join("; ")}`
        : "Cubre los aspectos clave de la RFP",
    },
    {
      label: "Cumplimiento",
      passed: result.compliance.pass,
      detail: result.compliance.violations.length
        ? result.compliance.violations.map((v) => v.message).join(" ")
        : `Reglas verificadas: ${result.compliance.rule_ids.join(", ")}`,
    },
  ];
}

export function isProvisional(section: Pick<RfpDepartmentSection, "draft_status">): boolean {
  return section.draft_status === "needs_human_review";
}
