// Roles y Permisos (BRIEFING de HealthCore): contratos de la API y lógica
// pura de la interfaz. Aquí solo se decide QUÉ SE MUESTRA; quien decide el
// acceso es la API, que vuelve a comprobar rol y departamento en cada
// endpoint (un botón oculto no protege nada).

export type Capability =
  | "records:read"
  | "records:create"
  | "records:update"
  | "records:approve"
  | "records:delete"
  | "users:manage"
  | "access:manage"
  | "departments:read_all"
  // Registro de Auditoría: Supervisor (su departamento) y Admin (todo).
  | "audit:read";

export interface Role {
  id: string;
  code: string;
  name: string;
  rank: number;
  capabilities: Capability[];
}

export interface Department {
  id: string;
  code: string;
  name: string;
}

/** Lo que devuelve GET /auth/me sobre el acceso de quien inicia sesión. */
export interface AccessProfile {
  role: string | null;
  role_rank: number;
  department: string | null;
  department_id: string | null;
  capabilities: Capability[];
}

export interface ManagedUser {
  id: string;
  name: string | null;
  role: string | null;
  role_id: string | null;
  department: string | null;
  department_id: string | null;
  is_active: boolean;
  created_at: string;
}

export interface UserAssignmentPayload {
  role_id?: string;
  department_id?: string;
}

export interface AdminUserCreatePayload {
  email: string;
  password: string;
  name?: string;
  role_id: string;
  department_id: string;
}

export interface InternalReport {
  id: string;
  department_id: string;
  department: string | null;
  title: string;
  body: string;
  created_by: string;
  created_at: string;
  approved_by: string | null;
  approved_at: string | null;
}

export interface InternalReportPayload {
  title: string;
  body: string;
  department_id?: string;
}

// Nombres visibles del BRIEFING (tabla de roles y de departamentos).
export const ROLE_LABELS: Record<string, string> = {
  employee: "Empleado",
  supervisor: "Supervisor",
  admin: "Admin",
};

export const DEPARTMENT_LABELS: Record<string, string> = {
  clinical_operations: "Operaciones clínicas",
  patient_experience: "Experiencia y acceso del paciente",
  revenue_cycle: "Ciclo de ingresos y facturación",
  compliance: "Compliance y gobierno de datos",
  people_workforce: "Personas y plantilla",
  technology: "Tecnología",
};

/** Etiqueta de un rol o departamento; si un admin creó uno nuevo, su nombre. */
export function roleLabel(code: string | null, fallbackName?: string): string {
  if (!code) return "Sin rol";
  return ROLE_LABELS[code] ?? fallbackName ?? code;
}

export function departmentLabel(code: string | null, fallbackName?: string): string {
  if (!code) return "Sin departamento";
  return DEPARTMENT_LABELS[code] ?? fallbackName ?? code;
}

/** ¿El rol de este perfil permite la acción? Sin perfil (aún cargando o
 * error), no: la interfaz nunca muestra una acción por defecto. */
export function can(profile: AccessProfile | null | undefined, capability: Capability): boolean {
  return Boolean(profile?.capabilities.includes(capability));
}

export function isAdmin(profile: AccessProfile | null | undefined): boolean {
  return can(profile, "access:manage");
}

/** Texto para la cabecera de una vista con alcance departamental. */
export function scopeDescription(profile: AccessProfile | null | undefined): string {
  if (!profile) return "";
  if (can(profile, "departments:read_all")) return "Ves los informes de todos los departamentos (Admin).";
  if (!profile.department) return "Tu cuenta no tiene departamento asignado: pide a un Admin que te asigne uno.";
  return `Ves solo los informes de ${departmentLabel(profile.department)}.`;
}

// Mensajes de los 403 de la API (cuerpo `detail.code`), en español.
export const FORBIDDEN_MESSAGES: Record<string, string> = {
  insufficient_role: "Tu rol no permite esta acción.",
  department_out_of_scope: "Este registro pertenece a otro departamento.",
  department_required: "Tu cuenta no tiene departamento asignado. Pide a un Admin que te asigne uno.",
};
