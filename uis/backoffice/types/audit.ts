// Registro de Auditoría (BRIEFING de HealthCore): contratos de /audit-log y
// lógica pura de la pantalla. Quien decide qué entradas ve cada persona es la
// API (rol + departamento); aquí solo se decide cómo se muestran.

export type ActorType = "human" | "process" | "anonymous";
export type Outcome = "success" | "denied" | "blocked";

export interface AuditEntry {
  sequence: number;
  entry_id: string;
  occurred_at: string;
  actor_type: ActorType;
  actor_id: string | null;
  actor_label: string;
  on_behalf_of: string | null;
  on_behalf_of_label: string | null;
  action: string;
  resource_type: string;
  resource_id: string | null;
  patient_ref: string | null;
  department: string | null;
  context: string | null;
  origin: string;
  outcome: Outcome;
  details: Record<string, unknown>;
  prev_hash: string;
  entry_hash: string;
}

export interface AuditPage {
  items: AuditEntry[];
  next_cursor: number | null;
  total: number;
  scope: string;
}

export interface AuditActor {
  actor_type: ActorType;
  actor_id: string | null;
  label: string;
  entries: number;
}

export interface AuditCatalog {
  actions: Record<string, string>;
  resource_types: string[];
  actor_types: ActorType[];
  outcomes: Outcome[];
  purposes: Record<string, string>;
}

export interface IntegrityResult {
  ok: boolean;
  checked: number;
  head_sequence: number | null;
  head_hash: string;
  broken_at: number | null;
  reason: "tampered_entry" | "broken_link" | "missing_entry" | null;
  verified_at: string;
}

export interface UnusualAccess {
  actor_id: string;
  actor_label: string;
  recent_count: number;
  baseline_average: number;
}

export interface AnomaliesResult {
  window_hours: number;
  baseline_days: number;
  items: UnusualAccess[];
}

export interface MyPatientAccess {
  sequence: number;
  occurred_at: string;
  actor_type: ActorType;
  actor_label: string;
  action: string;
  patient_ref: string | null;
  context: string | null;
  outcome: Outcome;
}

export interface MyPatientAccessPage {
  items: MyPatientAccess[];
  next_cursor: number | null;
  total: number;
}

// --- Etiquetas -----------------------------------------------------------------

export const ACTION_LABELS: Record<string, string> = {
  login_succeeded: "Inicio de sesión",
  login_failed: "Inicio de sesión fallido",
  user_created: "Alta de usuario",
  user_updated: "Cambio de usuario",
  user_assignment_changed: "Cambio de rol o departamento",
  user_deleted: "Baja de usuario",
  role_created: "Rol creado",
  role_updated: "Rol modificado",
  department_created: "Departamento creado",
  department_updated: "Departamento modificado",
  patient_record_viewed: "Consulta de ficha de paciente",
  patient_clinical_updated: "Cambio de datos clínicos",
  patient_billing_updated: "Cambio de datos de facturación",
  patient_documentation_drafted: "Borrador de documentación clínica",
  insurance_claim_approved: "Reclamación aprobada",
  insurance_claim_denied: "Reclamación denegada",
  patient_data_exported: "Exportación de datos de pacientes",
  access_request_issued: "Solicitud de acceso a sistema",
  access_request_blocked: "Solicitud de acceso bloqueada",
  financial_report_prepared: "Informe de cierre preparado",
};

export const RESOURCE_LABELS: Record<string, string> = {
  session: "Sesión",
  user: "Usuario",
  role: "Rol",
  department: "Departamento",
  patient_record: "Ficha de paciente",
  insurance_claim: "Reclamación de seguro",
  patient_export: "Exportación de pacientes",
  access_request: "Solicitud de acceso",
  financial_report: "Informe financiero",
};

export const ACTOR_TYPE_LABELS: Record<ActorType, string> = {
  human: "Persona",
  process: "Proceso automático",
  anonymous: "Sin cuenta identificada",
};

export const OUTCOME_LABELS: Record<Outcome, string> = {
  success: "Realizada",
  denied: "Denegada",
  blocked: "Bloqueada",
};

export function label(map: Record<string, string>, code: string | null | undefined): string {
  if (!code) return "—";
  return map[code] ?? code;
}

// --- Filtros ---------------------------------------------------------------------

export interface AuditFilters {
  actorKey: string; // "" | "<actor_type>:<actor_id>"
  action: string;
  resourceType: string;
  resourceId: string;
  patientRef: string;
  dateFrom: string; // YYYY-MM-DD (día completo, incluido)
  dateTo: string; // YYYY-MM-DD (día completo, incluido)
}

export const EMPTY_FILTERS: AuditFilters = {
  actorKey: "",
  action: "",
  resourceType: "",
  resourceId: "",
  patientRef: "",
  dateFrom: "",
  dateTo: "",
};

const MS_PER_DAY = 24 * 60 * 60 * 1000;

export function actorKey(actor: Pick<AuditActor, "actor_type" | "actor_id">): string {
  return `${actor.actor_type}:${actor.actor_id ?? ""}`;
}

/** Valida el rango de fechas antes de pedir nada. null = válido. */
export function validateFilters(filters: AuditFilters): string | null {
  if (filters.dateFrom && filters.dateTo && filters.dateFrom > filters.dateTo) {
    return "La fecha de inicio no puede ser posterior a la fecha de fin.";
  }
  if (filters.patientRef && !/^PT-[0-9A-F]{8}$/.test(filters.patientRef.trim().toUpperCase())) {
    return "La referencia de paciente tiene la forma PT-XXXXXXXX.";
  }
  return null;
}

/**
 * Filtros de la pantalla → parámetros de GET /audit-log.
 *
 * Mismo contrato de fechas que /telemetry: los <input type="date"> son días
 * completos con los dos extremos incluidos, y la API espera inicio inclusivo
 * y fin EXCLUSIVO en UTC. "Del 6 al 12" es [6 00:00Z, 13 00:00Z); mandar el 12
 * tal cual perdería las entradas de ese día sin ningún error.
 */
export function toAuditQuery(filters: AuditFilters, options: { limit: number; before?: number | null }): URLSearchParams {
  const params = new URLSearchParams();
  params.set("limit", String(options.limit));
  if (options.before) params.set("before", String(options.before));
  if (filters.actorKey) {
    const [type, ...rest] = filters.actorKey.split(":");
    const id = rest.join(":");
    params.set("actor_type", type);
    if (id) params.set("actor_id", id);
  }
  if (filters.action) params.set("action", filters.action);
  if (filters.resourceType) params.set("resource_type", filters.resourceType);
  if (filters.resourceId.trim()) params.set("resource_id", filters.resourceId.trim());
  if (filters.patientRef.trim()) params.set("patient_ref", filters.patientRef.trim().toUpperCase());
  if (filters.dateFrom) params.set("date_from", new Date(`${filters.dateFrom}T00:00:00Z`).toISOString());
  if (filters.dateTo) {
    const endExclusive = new Date(new Date(`${filters.dateTo}T00:00:00Z`).getTime() + MS_PER_DAY);
    params.set("date_to", endExclusive.toISOString());
  }
  return params;
}

// --- Presentación ----------------------------------------------------------------

/** "2026-11-07T10:15:00.123456Z" → fecha y hora locales en español. */
export function formatAuditTime(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString("es-ES", { dateStyle: "short", timeStyle: "medium" });
}

/** Hash abreviado para la tabla (el completo va en el detalle). */
export function shortHash(hash: string): string {
  return hash.length > 12 ? `${hash.slice(0, 6)}…${hash.slice(-6)}` : hash;
}

/** Detalles de una entrada en texto legible; nunca hay PHI (solo códigos). */
export function describeDetails(details: Record<string, unknown>): string {
  return Object.entries(details)
    .filter(([key]) => key !== "seed_batch")
    .map(([key, value]) => {
      if (key === "patient_refs" && Array.isArray(value)) return `${value.length} pacientes`;
      if (Array.isArray(value)) return `${key}: ${value.join(", ")}`;
      if (key.endsWith("_hash") || key === "figures_sha256") return `${key}: ${shortHash(String(value))}`;
      return `${key}: ${String(value)}`;
    })
    .join(" · ");
}

const BREAK_REASONS: Record<NonNullable<IntegrityResult["reason"]>, string> = {
  tampered_entry: "su contenido no coincide con su huella: alguien la modificó después de escribirla",
  broken_link: "no apunta a la huella de la entrada anterior: la anterior fue modificada y re-firmada",
  missing_entry: "falta la entrada previa en la numeración: alguien borró entradas",
};

/** Resultado de la verificación en lenguaje de Compliance. */
export function describeIntegrity(result: IntegrityResult): string {
  if (result.ok) {
    return result.checked === 0
      ? "El registro está vacío: no hay nada que verificar."
      : `Cadena íntegra: ${result.checked} entradas verificadas, ninguna alterada.`;
  }
  const reason = result.reason ? BREAK_REASONS[result.reason] : "la cadena no es coherente";
  return `Cadena ROTA en la entrada #${result.broken_at}: ${reason}. Las ${result.checked} entradas anteriores siguen verificadas.`;
}
