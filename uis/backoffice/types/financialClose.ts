// Cierre financiero mensual (services/financial_close). Contrato con la API y
// ayudas puras de la pantalla /financial-close.

export type ReportStatus = "pending_review" | "approved" | "rejected" | "distributed";
export type ReportLanguage = "en" | "es";

export interface StaffRole {
  org_role: string | null;
  display_name: string | null;
  is_reviewer: boolean;
  next_scheduled_run: string;
}

export interface ReportListItem {
  id: string;
  period: string;
  version: number;
  status: ReportStatus;
  trigger: string;
  narrative_source: string;
  figures_sha256: string;
  supersedes_id: string | null;
  created_at: string;
}

export interface FigureSource {
  kind: "ledger" | "budget" | "operations" | "fx" | "calculation";
  entry_id?: string;
  source_file?: string;
  source_row?: number;
  reference?: string;
  of?: unknown;
}

export interface Figure {
  value: string | null;
  unit: string;
  sources: FigureSource[];
  status?: string;
}

export interface ReviewDecision {
  decision: "approve" | "reject";
  comments: string | null;
  decided_by: string;
  decided_at: string;
}

export interface Distribution {
  recipient_role: string;
  recipient_name: string;
  delivered_at: string;
}

export interface ReportDetail extends ReportListItem {
  language: ReportLanguage;
  markdown: string;
  figures: Record<string, Figure>;
  quality: Record<string, unknown>;
  decisions: ReviewDecision[];
  distribution: Distribution[];
  reviewer: string;
  can_decide: boolean;
}

export interface TraceRecord {
  entry_id: string;
  source_file: string;
  source_row: number;
  source_ref: string;
  entry_date: string | null;
  country: string;
  payer: string;
  category: string;
  currency: string;
  amount: string | null;
  status: string;
  description: string | null;
  description_withheld: boolean;
}

export interface FigureTrace {
  figure_id: string;
  value: string | null;
  unit: string;
  sources: FigureSource[];
  ledger_records: TraceRecord[];
}

export interface InboxItem {
  kind: string;
  period: string;
  report_id: string | null;
  entry_id: string | null;
  summary: string;
  created_at: string;
}

export const STATUS_LABELS: Record<ReportStatus, string> = {
  pending_review: "Pendiente de revisión",
  approved: "Aprobado",
  rejected: "Rechazado",
  distributed: "Distribuido",
};

export const TRIGGER_LABELS: Record<string, string> = {
  scheduled: "Programado",
  manual: "Manual",
  revision: "Revisión",
};

export const INBOX_LABELS: Record<string, string> = {
  report_pending_review: "Informe pendiente de revisión",
  patient_data_in_source: "Registro con posibles datos de paciente",
  report_distributed: "Informe distribuido",
};

const MONTHS = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre", "octubre",
  "noviembre", "diciembre"];

/** "2026-10-01" → "octubre de 2026" (sin zona horaria: el periodo es una fecha, no un instante). */
export function periodLabel(period: string): string {
  const [year, month] = period.split("-").map(Number);
  if (!year || !month || month < 1 || month > 12) return period;
  return `${MONTHS[month - 1]} de ${year}`;
}

// --- Decisión del revisor ----------------------------------------------------------------

export type Decision = "approve" | "reject";

/** Misma regla que la API: rechazar exige comentarios. Devuelve el error o null. */
export function validateDecision(decision: Decision, comments: string): string | null {
  if (decision === "reject" && comments.trim().length === 0) {
    return "Para rechazar, escribe qué debe cambiar en la nueva versión.";
  }
  if (comments.length > 2000) return "Máximo 2000 caracteres.";
  return null;
}

export function decisionBody(decision: Decision, comments: string): { decision: Decision; comments?: string } {
  const text = comments.trim();
  return text ? { decision, comments: text } : { decision };
}

// --- Markdown del informe → bloques ------------------------------------------------------
// El informe es markdown generado por el propio backend con una forma fija
// (títulos, párrafos, listas, tablas y citas). Se convierte en bloques que la
// pantalla pinta con elementos de React: nunca se inyecta HTML.

export type Block =
  | { kind: "heading"; level: number; text: string }
  | { kind: "paragraph"; text: string }
  | { kind: "quote"; text: string }
  | { kind: "list"; items: string[] }
  | { kind: "table"; header: string[]; rows: string[][] };

function cells(line: string): string[] {
  return line.trim().replace(/^\|/, "").replace(/\|$/, "").split("|").map((c) => c.trim());
}

export function parseReport(markdown: string): Block[] {
  const blocks: Block[] = [];
  const lines = markdown.split("\n");
  let i = 0;
  while (i < lines.length) {
    const line = lines[i];
    if (!line.trim()) {
      i += 1;
      continue;
    }
    const heading = /^(#{1,6})\s+(.*)$/.exec(line);
    if (heading) {
      blocks.push({ kind: "heading", level: heading[1].length, text: heading[2] });
      i += 1;
    } else if (line.startsWith("|")) {
      const header = cells(line);
      const rows: string[][] = [];
      i += 2; // cabecera + separador |---|
      while (i < lines.length && lines[i].startsWith("|")) {
        rows.push(cells(lines[i]));
        i += 1;
      }
      blocks.push({ kind: "table", header, rows });
    } else if (line.startsWith("- ")) {
      const items: string[] = [];
      while (i < lines.length && lines[i].startsWith("- ")) {
        items.push(lines[i].slice(2));
        i += 1;
      }
      blocks.push({ kind: "list", items });
    } else if (line.startsWith("> ")) {
      blocks.push({ kind: "quote", text: line.slice(2) });
      i += 1;
    } else {
      blocks.push({ kind: "paragraph", text: line.replace(/^\\\*/, "*") });
      i += 1;
    }
  }
  return blocks;
}

export type Inline = { kind: "text"; text: string } | { kind: "code"; text: string } | { kind: "strong"; text: string };

/** Separa `código`, **negrita** y texto. Los `código` que sean ids de cifra se vuelven botones. */
export function splitInline(text: string): Inline[] {
  const out: Inline[] = [];
  const pattern = /`([^`]+)`|\*\*([^*]+)\*\*|_([^_]+)_$/g;
  let last = 0;
  let match: RegExpExecArray | null;
  while ((match = pattern.exec(text)) !== null) {
    if (match.index > last) out.push({ kind: "text", text: text.slice(last, match.index) });
    if (match[1] !== undefined) out.push({ kind: "code", text: match[1] });
    else if (match[2] !== undefined) out.push({ kind: "strong", text: match[2] });
    else out.push({ kind: "text", text: match[3] });
    last = pattern.lastIndex;
  }
  if (last < text.length) out.push({ kind: "text", text: text.slice(last) });
  return out;
}

export function isFigureId(code: string, figures: Record<string, Figure>): boolean {
  return Object.prototype.hasOwnProperty.call(figures, code);
}

export function sourceLabel(source: FigureSource): string {
  switch (source.kind) {
    case "ledger":
      return `ledger.csv · ${source.entry_id}`;
    case "budget":
    case "operations":
      return `${source.source_file} fila ${source.source_row}`;
    case "fx":
      return `Tipo de cambio (${source.reference})`;
    default:
      return "Cálculo";
  }
}
