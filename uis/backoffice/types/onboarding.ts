// Contratos de /onboarding (services/onboarding/schemas.py) y ayudas puras
// para las pantallas del orquestador de incorporaciones.

export type StageStatus = "completed" | "current" | "upcoming";
export type Phase = "starting" | "awaiting_paperwork" | "awaiting_check" | "awaiting_approval" | "completed";
export type WaitKind = "paperwork" | "check" | "approval";

export interface OnboardingStage {
  number: number;
  key: string;
  title: string;
  deadline_day: number;
  due_at: string;
  status: StageStatus;
  approver: string | null;
  pass_threshold: number | null;
}

export interface WaitingFor {
  kind: WaitKind;
  stage: number;
  attempt?: number | null;
  approver?: string | null;
}

export interface OnboardingListItem {
  hire_id: string;
  full_name: string;
  job_title: string;
  department: string;
  site: string;
  framework: string;
  language: string;
  start_date: string;
  status: "active" | "completed";
  current_stage: number;
  current_stage_title: string;
  phase: Phase;
  current_due_at: string | null;
  blocked: boolean;
  blocked_reasons: string[];
  completed_stages: number[];
}

export interface AccessRequest {
  system_key: string;
  system_name: string;
  contains_patient_data: boolean;
  status: "requested" | "blocked";
  requested_by_type: "agent" | "human";
  created_at: string;
}

export interface AuditEvent {
  stage: number | null;
  action: string;
  actor_type: "agent" | "human";
  actor_ref: string;
  outcome: string | null;
  security_event: boolean;
  created_at: string;
}

export interface CheckResult {
  score: number;
  passed: boolean;
  correct: number;
  total: number;
  attempt: number;
  stage?: number;
  failed_sections: string[];
}

export interface Rejection {
  stage: number;
  comment: string;
  round: number;
}

export interface OnboardingDetail extends OnboardingListItem {
  role_variant: string;
  stages: OnboardingStage[];
  waiting_for: WaitingFor | null;
  paperwork: Record<string, boolean>;
  approvals: Record<string, { org_role: string; department: string }>;
  attempts: Record<string, number>;
  failed_attempts: Record<string, number>;
  last_check_result: CheckResult | null;
  rejection: Rejection | null;
  compliance_approved: boolean;
  access_requests: AccessRequest[];
  can_mark_paperwork: boolean;
  can_decide: boolean;
  decide_refusal: string | null;
  audit: AuditEvent[] | null;
}

export interface ContentSection {
  title: string;
  body: string;
  citation: string;
}

export interface OnboardingContent {
  doc_id: string;
  title: string;
  language: string;
  sections: ContentSection[];
  reinforcement: boolean;
}

export interface CheckQuestion {
  id: string;
  question: string;
  options: string[];
}

export interface OnboardingCheck {
  set_id: string;
  attempt: number;
  threshold: number;
  questions: CheckQuestion[];
}

export interface OnboardingQuestion {
  id: string;
  stage: number;
  question: string;
  status: "answered" | "routed";
  answer: string;
  citations: string[];
  routed_to: string | null;
  created_at: string;
}

export interface SystemInfo {
  key: string;
  name: string;
  contains_patient_data: boolean;
}

export interface MyOnboarding {
  hire_id: string;
  full_name: string;
  job_title: string;
  language: string;
  framework: string;
  status: "active" | "completed";
  phase: Phase;
  current_stage: number;
  stages: OnboardingStage[];
  remaining_stages: number[];
  content: OnboardingContent | null;
  check: OnboardingCheck | null;
  last_check_result: CheckResult | null;
  rejection: Rejection | null;
  waiting_for: WaitingFor | null;
  questions: OnboardingQuestion[];
  access_requests: AccessRequest[];
  systems: SystemInfo[];
  compliance_approved: boolean;
}

export interface OnboardingRole {
  is_hire: boolean;
  hire_id: string | null;
  org_role: string | null;
  department: string | null;
  can_view_all: boolean;
  can_view_reports: boolean;
  can_create: boolean;
}

export interface OnboardingNotification {
  id: number;
  hire_id: string;
  stage: number | null;
  kind: string;
  subject: string;
  body: string;
  sender: string;
  created_at: string;
}

export const POLL_INTERVAL_MS = 2500;

export const PHASE_LABELS: Record<Phase, string> = {
  starting: "Arrancando",
  awaiting_paperwork: "Esperando trámites",
  awaiting_check: "Comprobación pendiente",
  awaiting_approval: "Esperando aprobación",
  completed: "Completada",
};

export const APPROVER_LABELS: Record<string, string> = {
  people_team: "Equipo de Personas",
  compliance: "Claire Whitfield (Cumplimiento)",
  line_manager: "Responsable directo",
  vp_people: "Diane Foster",
};

export const DEPARTMENT_LABELS: Record<string, string> = {
  digital: "HealthCore Digital",
  revenue_cycle: "Ciclo de Ingresos",
  compliance: "Cumplimiento",
  patient_experience: "Experiencia del Paciente",
  people: "Personas",
  leadership: "Dirección",
};

export const SITE_LABELS: Record<string, string> = { austin: "Austin", london: "Londres" };
export const FRAMEWORK_LABELS: Record<string, string> = { hipaa: "HIPAA", uk_gdpr: "UK GDPR" };

export const PAPERWORK_LABELS: Record<string, string> = {
  contract_received: "Contrato firmado recibido",
  payroll_docs_received: "Documentación de nómina recibida",
  background_check_completed: "Verificación de antecedentes completada",
};

const ROUTED_LABELS: Record<string, string> = {
  compliance: "Derivada a Claire Whitfield (Cumplimiento)",
  vp_people: "Derivada a Diane Foster (Personas)",
};

export function routedLabel(routedTo: string | null): string {
  return (routedTo && ROUTED_LABELS[routedTo]) || "Derivada a una persona del equipo";
}

/** Tras enviar algo (202), el worker lo aplica en segundos: se consulta
 *  mientras haya una acción en vuelo o la incorporación esté arrancando. */
export function shouldPoll(phase: Phase | undefined, pendingSince: number | null, now: number = Date.now()): boolean {
  if (phase === "starting") return true;
  return pendingSince !== null && now - pendingSince < 60_000;
}

/** Respuestas listas para la API, o la lista de preguntas sin responder. */
export function buildAnswers(
  questions: CheckQuestion[],
  selected: Record<string, number | undefined>,
): { answers: Record<string, number> } | { missing: string[] } {
  const missing = questions.filter((q) => selected[q.id] === undefined).map((q) => q.id);
  if (missing.length > 0) return { missing };
  const answers: Record<string, number> = {};
  questions.forEach((q) => {
    answers[q.id] = selected[q.id] as number;
  });
  return { answers };
}

/** Firma del progreso: si cambia tras enviar algo, el agente ya lo aplicó. */
export function progressSignature(item: {
  phase: Phase;
  current_stage: number;
  waiting_for: WaitingFor | null;
  completed_stages?: number[];
  paperwork?: Record<string, boolean>;
  rejection?: Rejection | null;
}): string {
  const marks = item.paperwork ? Object.values(item.paperwork).filter(Boolean).length : 0;
  return [
    item.phase,
    item.current_stage,
    item.waiting_for?.kind ?? "",
    item.waiting_for?.attempt ?? "",
    (item.completed_stages ?? []).join(","),
    marks,
    item.rejection?.round ?? "",
  ].join("|");
}

export function formatScore(result: CheckResult): string {
  return `${result.correct}/${result.total} (${Math.round(result.score * 100)} %)`;
}

export function formatDue(iso: string | null): string {
  if (!iso) return "—";
  return new Date(iso).toLocaleString("es-ES", { dateStyle: "medium", timeStyle: "short" });
}

/** ¿Está vencida la etapa en curso? (la API manda el instante exacto en UTC). */
export function isOverdue(dueIso: string | null, now: number = Date.now()): boolean {
  return dueIso !== null && new Date(dueIso).getTime() <= now;
}

const ACTION_LABELS: Record<string, string> = {
  onboarding_created: "Alta de la incorporación",
  onboarding_started: "Inicio del onboarding",
  paperwork_marked: "Trámites marcados",
  paperwork_refused: "Trámites rechazados (sin permiso)",
  content_presented: "Material presentado",
  content_reinforced: "Refuerzo de puntos fallados",
  check_graded: "Comprobación corregida",
  approval_granted: "Etapa aprobada",
  approval_rejected: "Etapa rechazada",
  approval_refused: "Aprobación sin permiso",
  stage_completed: "Etapa completada",
  access_requests_issued: "Solicitudes de acceso emitidas",
  access_requested: "Acceso solicitado",
  access_request_processed: "Solicitud procesada",
  patient_data_access_blocked: "Acceso a datos de pacientes BLOQUEADO",
  deadline_reminder_sent: "Recordatorio de plazo",
  deadline_escalated: "Escalado al responsable",
  compliance_delay_reported: "Retraso de cumplimiento informado",
  question_answered: "Duda respondida con el material",
  question_routed: "Duda derivada a una persona",
  sensitive_data_redacted: "Datos sensibles eliminados de una duda",
  onboarding_completed: "Onboarding completado",
};

export function actionLabel(action: string): string {
  if (ACTION_LABELS[action]) return ACTION_LABELS[action];
  if (action.startsWith("notification_")) return `Aviso enviado (${action.slice("notification_".length)})`;
  return action;
}
