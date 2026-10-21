/**
 * Contrato de /rfp-tickets (services/rfp_intake/schemas.py) y ayudas puras
 * de la pantalla /rfps. Estados de las Partes 1, 2 y 3 del Hito 9, más
 * `error` y `evaluacion_completa` (añadidos por decisión del usuario).
 */

export type RfpStatus =
  | "analizando"
  | "descartado"
  | "analisis_completo"
  | "error"
  | "generando_borrador"
  | "en_evaluación"
  | "needs_human_review"
  | "evaluacion_completa"
  | "esperando_aprobación"
  | "terminado";

export type SectionDraftStatus =
  | "pendiente"
  | "generando_borrador"
  | "en_evaluación"
  | "aprobada_por_evaluadores"
  | "needs_human_review";
export type DiscardReason = "not_rfp" | "phi_detected" | "rejected_in_approval" | "arbitration_limit";

/** Parte 3: aprobación humana de cada sección. */
export type ApprovalStatus =
  | "pendiente"
  | "decision_registrada"
  | "en_revisión"
  | "bloqueada"
  | "aprobada"
  | "rechazada"
  | "cancelada";

export type ApprovalDecision = "approve" | "request_changes" | "reject";

/** Lo que el grafo enseña al responsable al pausarse (payload del interrupt). */
export interface ApprovalRequest {
  department_id: string;
  department: string;
  approver: string;
  draft: string;
  draft_digest: string;
  provisional: boolean;
  evaluation: {
    overall_pass: boolean | null;
    readability_pass: boolean | null;
    readability_score: number | null;
    missing_aspects: string[];
    compliance_violations: string[];
    feedback: string | null;
  };
  key_aspects: string[];
  fixed_facts: { client_country: string | null; currency: string | null; data_agreement: string | null; regulation: string | null };
  covered_population: number | null;
  covered_population_unit: string | null;
  asks_capacity: boolean;
  revision_round: number;
  max_revision_rounds: number;
  can_request_changes: boolean;
  arbitration_round: number;
  last_error?: string | null;
}

export interface ApprovalHistoryItem {
  decision: ApprovalDecision;
  round: number;
  decided_by: string | null;
  decided_at: string | null;
  has_comments: boolean;
  confirmed_capacity: number | null;
}

export interface ArbitrationConflict {
  trigger_id: "phi-detected" | "baa-dpa-mismatch" | "capacity-vs-population";
  departments: string[];
  arbiter: string;
  arbiter_kind: "rule" | "human";
  details: Record<string, unknown>;
}

export interface ArbitrationRequest {
  trigger_id: string;
  arbiter: string;
  departments: string[];
  covered_population: number;
  covered_population_unit: string | null;
  confirmed_capacity: number;
  options: ("reduce_population" | "add_sites")[];
  round: number;
  max_rounds: number;
}

export interface RfpArbitration {
  pending: boolean;
  request: ArbitrationRequest | null;
  decision_registered: boolean;
  applying?: boolean;
  last_error?: string | null;
  round: number;
  max_rounds: number | null;
  last?: {
    round: number;
    resolution: "none" | "rule" | "human" | "limit" | "rejected";
    conflicts: ArbitrationConflict[];
    forced_departments?: string[];
    rejected_by?: string[];
  };
}

export interface RfpFinalDocument {
  ticket_id: string;
  rfp_id: string;
  sections: { department_id: string; department: string; title: string; content: string; approver: string }[];
  currency: string | null;
  client_country: string | null;
  approvals: { department_id: string; department: string; approver: string; decided_by: string | null; approved_at: string | null; revision_rounds: number }[];
  content_markdown: string;
  generated_at: string;
}

export interface RfpTraceEvent {
  event_id: string;
  part: number;
  node: string;
  agent: string;
  department_id: string | null;
  input: Record<string, unknown>;
  output: Record<string, unknown>;
  timestamp: string;
}

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
  approval_status: ApprovalStatus | null;
  approval_request: ApprovalRequest | null;
  approval_round: number;
  approval_history: ApprovalHistoryItem[];
  approver: string | null;
  approved_at: string | null;
  confirmed_capacity: number | null;
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
  arbitration: RfpArbitration | null;
  has_final_document: boolean;
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
  "esperando_aprobación": "Esperando aprobación",
  terminado: "Terminado",
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
  "esperando_aprobación": "rfp-status-analyzing",
  terminado: "rfp-status-complete",
};

export const APPROVAL_STATUS_LABELS: Record<ApprovalStatus, string> = {
  pendiente: "Esperando aprobación",
  decision_registrada: "Aplicando decisión…",
  "en_revisión": "Reescribiendo con tus cambios…",
  bloqueada: "Bloqueada por Compliance",
  aprobada: "Aprobada",
  rechazada: "Rechazada",
  cancelada: "Cancelada",
};

export const APPROVAL_STATUS_CLASS: Record<ApprovalStatus, string> = {
  pendiente: "rfp-status-analyzing",
  decision_registrada: "rfp-status-analyzing",
  "en_revisión": "rfp-status-analyzing",
  bloqueada: "rfp-status-error",
  aprobada: "rfp-status-complete",
  rechazada: "rfp-status-error",
  cancelada: "rfp-status-discarded",
};

export const TRIGGER_LABELS: Record<string, string> = {
  "phi-detected": "Datos de paciente en una sección",
  "baa-dpa-mismatch": "Cláusula BAA/DPA incorrecta para el país",
  "capacity-vs-population": "La capacidad clínica no cubre la población",
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
const APPROVAL_WORKING: ApprovalStatus[] = ["decision_registrada", "en_revisión"];

export function isInProgress(status: RfpStatus): boolean {
  return WORKING_STATUSES.includes(status);
}

/** La pantalla sigue consultando mientras hay trabajo en curso y también en
 *  `analisis_completo`: la Parte 2 arranca sola justo después. */
export function shouldPoll(status: RfpStatus): boolean {
  return isInProgress(status) || status === "analisis_completo" || status === "evaluacion_completa" || status === "needs_human_review";
}

/** Igual que `shouldPoll`, y además mientras el worker aplica una decisión
 *  humana. Esperar a una persona puede durar días: ahí no se consulta. */
export function shouldPollTicket(ticket: Pick<RfpTicketDetail, "status" | "sections" | "arbitration">): boolean {
  if (shouldPoll(ticket.status)) return true;
  const applying = ticket.sections.some((s) => s.approval_status !== null && APPROVAL_WORKING.includes(s.approval_status));
  // Una revisión que falló (p. ej. timeout del proxy) deja el ticket en
  // `error` unos segundos mientras Celery la reintenta: se sigue consultando.
  // Lo detectó el recorrido real en Chrome, no los tests.
  if (ticket.status === "error") return applying;
  if (ticket.status !== "esperando_aprobación") return false;
  return applying || Boolean(ticket.arbitration?.decision_registered || ticket.arbitration?.applying);
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
    case "esperando_aprobación":
      return "Cada responsable aprueba, pide cambios o rechaza su sección por separado. El documento final se genera solo cuando aprueban todos (Compliance siempre).";
    case "terminado":
      return "Todos los departamentos aprobaron: el documento final está listo para Revenue Cycle.";
    case "error":
      return ticket.error_message ?? "El proceso falló por un problema técnico.";
    case "descartado":
      if (ticket.discard_reason === "rejected_in_approval") {
        return "Un departamento rechazó su sección: la propuesta se ha descartado y no se generará documento final.";
      }
      if (ticket.discard_reason === "arbitration_limit") {
        return "El conflicto entre departamentos no se resolvió en el máximo de rondas de arbitraje: la propuesta se ha descartado.";
      }
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

export interface DecisionDraft {
  decision: ApprovalDecision | "";
  comments: string;
  confirmedCapacity: string;
}

/** Comprobación en el navegador antes de enviar (el servidor repite todo,
 *  incluido el detector de PHI): motivo obligatorio para cambios/rechazo,
 *  rondas restantes y capacidad de Clinical. Devuelve errores por campo. */
export function validateDecisionDraft(draft: DecisionDraft, request: Pick<ApprovalRequest, "asks_capacity" | "can_request_changes">): Record<string, string> {
  const errors: Record<string, string> = {};
  if (!draft.decision) errors.decision = "Elige una decisión.";
  if (draft.decision === "request_changes" && !request.can_request_changes) {
    errors.decision = "No quedan rondas de cambios: aprueba tal cual o rechaza.";
  }
  if ((draft.decision === "request_changes" || draft.decision === "reject") && draft.comments.trim().length < 10) {
    errors.comments = "Explica el motivo (al menos 10 caracteres).";
  }
  if (draft.decision === "approve" && request.asks_capacity) {
    const capacity = Number(draft.confirmedCapacity);
    if (!draft.confirmedCapacity.trim() || !Number.isInteger(capacity) || capacity <= 0) {
      errors.confirmed_capacity = "Indica cuántas personas pueden cubrir las sedes y el personal asignados.";
    }
  }
  return errors;
}

export function toDecisionPayload(draft: DecisionDraft, request: Pick<ApprovalRequest, "asks_capacity" | "draft_digest">) {
  return {
    decision: draft.decision,
    comments: draft.comments.trim() || null,
    confirmed_capacity: draft.decision === "approve" && request.asks_capacity ? Number(draft.confirmedCapacity) : null,
    draft_digest: request.draft_digest,
  };
}

/** Resumen legible de la última vuelta del árbitro (o null si no hubo conflicto). */
export function describeArbitration(arbitration: RfpArbitration | null): string | null {
  const last = arbitration?.last;
  if (!last || last.resolution === "none") return null;
  const triggers = last.conflicts.map((c) => TRIGGER_LABELS[c.trigger_id] ?? c.trigger_id).join("; ");
  switch (last.resolution) {
    case "rule":
      return `Ronda ${last.round}: ${triggers}. Claire Whitfield (Compliance) obliga a revisar: ${(last.forced_departments ?? []).join(", ")}.`;
    case "human":
      return `Ronda ${last.round}: ${triggers}. Tom Callahan decidió; se revisan: ${(last.forced_departments ?? []).join(", ")}.`;
    case "limit":
      return `Conflicto sin resolver tras ${last.round} rondas: ${triggers}.`;
    case "rejected":
      return `Rechazada por: ${(last.rejected_by ?? []).join(", ")}.`;
  }
  return null;
}

export const TRACE_PART_LABELS: Record<number, string> = { 1: "Recepción", 2: "Borradores", 3: "Aprobación" };

/** Salida de un paso del trace en una línea (claves y valores simples). */
export function summarizeTraceOutput(output: Record<string, unknown>): string {
  return Object.entries(output)
    .filter(([, value]) => value !== null && value !== undefined && typeof value !== "object")
    .map(([key, value]) => `${key}: ${String(value)}`)
    .join(" · ");
}

/** Descarga el Markdown del documento final como archivo .md. */
export function finalDocumentFileName(document: Pick<RfpFinalDocument, "rfp_id">): string {
  return `propuesta-${document.rfp_id}.md`;
}
