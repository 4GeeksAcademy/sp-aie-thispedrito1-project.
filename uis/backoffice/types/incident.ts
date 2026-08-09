export type IncidentStatus = "open" | "in_progress" | "resolved" | "discarded";

export type IncidentOrigin = "customer" | "branch" | "internal";

export type IncidentCategory =
  | "clinical_equipment"
  | "it_system"
  | "billing_error"
  | "compliance_breach"
  | "patient_experience"
  | "staff_issue"
  | "facility_issue"
  | "referral_issue"
  | "other";

export type IncidentBranch =
  | "central"
  | "austin_north"
  | "dallas_uptown"
  | "houston_med_center"
  | "san_antonio_west"
  | "miami_brickell"
  | "miami_doral"
  | "orlando_east"
  | "tampa_bay"
  | "atlanta_midtown"
  | "savannah"
  | "london_city"
  | "london_west"
  | "manchester_central";

export type Incident = {
  id: number;
  title: string;
  description: string;
  category: IncidentCategory;
  status: IncidentStatus;
  origin: IncidentOrigin;
  branch: IncidentBranch;
  created_at: string;
  updated_at: string;
};

export type IncidentCreateInput = {
  title: string;
  description: string;
  category: IncidentCategory | "";
  status: IncidentStatus;
  origin: IncidentOrigin | "";
  branch: IncidentBranch | "";
};

export type IncidentFilters = {
  status?: IncidentStatus | "";
  origin?: IncidentOrigin | "";
  branch?: IncidentBranch | "";
  category?: IncidentCategory | "";
};

export type IncidentSummary = {
  total: number;
  by_status: Record<string, number>;
  by_category: Record<string, number>;
  by_origin: Record<string, number>;
  by_branch: Record<string, number>;
};

type Option<T extends string> = { value: T; label: string };

export const STATUS_OPTIONS: Option<IncidentStatus>[] = [
  { value: "open", label: "Abierta" },
  { value: "in_progress", label: "En progreso" },
  { value: "resolved", label: "Resuelta" },
  { value: "discarded", label: "Descartada" },
];

export const ORIGIN_OPTIONS: Option<IncidentOrigin>[] = [
  { value: "customer", label: "Cliente" },
  { value: "branch", label: "Sede" },
  { value: "internal", label: "Interno" },
];

export const CATEGORY_OPTIONS: Option<IncidentCategory>[] = [
  { value: "clinical_equipment", label: "Equipo clínico" },
  { value: "it_system", label: "Sistema informático" },
  { value: "billing_error", label: "Error de facturación" },
  { value: "compliance_breach", label: "Incumplimiento normativo" },
  { value: "patient_experience", label: "Experiencia del paciente" },
  { value: "staff_issue", label: "Incidencia de personal" },
  { value: "facility_issue", label: "Incidencia de instalaciones" },
  { value: "referral_issue", label: "Incidencia de derivación" },
  { value: "other", label: "Otra" },
];

export const BRANCH_OPTIONS: Option<IncidentBranch>[] = [
  { value: "central", label: "Central — Clínica Principal de Austin" },
  { value: "austin_north", label: "Austin — Norte" },
  { value: "dallas_uptown", label: "Dallas Uptown" },
  { value: "houston_med_center", label: "Centro Médico de Houston" },
  { value: "san_antonio_west", label: "San Antonio Oeste" },
  { value: "miami_brickell", label: "Miami Brickell" },
  { value: "miami_doral", label: "Miami Doral" },
  { value: "orlando_east", label: "Orlando Este" },
  { value: "tampa_bay", label: "Tampa Bay" },
  { value: "atlanta_midtown", label: "Atlanta Midtown" },
  { value: "savannah", label: "Savannah" },
  { value: "london_city", label: "Londres — City" },
  { value: "london_west", label: "Londres — West End" },
  { value: "manchester_central", label: "Manchester Central" },
];

export const STATUS_TRANSITIONS: Record<IncidentStatus, IncidentStatus[]> = {
  open: ["in_progress", "discarded"],
  in_progress: ["resolved", "discarded"],
  resolved: [],
  discarded: [],
};

function toLabelMap<T extends string>(options: Option<T>[]): Record<string, string> {
  return Object.fromEntries(options.map((option) => [option.value, option.label]));
}

export const STATUS_LABELS = toLabelMap(STATUS_OPTIONS);
export const ORIGIN_LABELS = toLabelMap(ORIGIN_OPTIONS);
export const CATEGORY_LABELS = toLabelMap(CATEGORY_OPTIONS);
export const BRANCH_LABELS = toLabelMap(BRANCH_OPTIONS);
