/**
 * Contrato de /reporting (pipeline de desempeño de negocio) y utilidades
 * puras de la pantalla /reporting.
 *
 * Es un dashboard para la CEO y la CCO (CONTEXT del pipeline, sección 1): todo
 * lo que sale de aquí hacia la pantalla debe leerse sin conocimientos técnicos.
 * Nada de event_type, run_id ni códigos de estado.
 *
 * Los tipos replican services/reporting/schemas.py
 * (MonthlyClinicSupplyPerformanceResponse, PipelineRunStatus).
 */

export type ReportCountry = "US" | "UK";
export type ReportCurrency = "USD" | "GBP";

export type ClinicSupplyPerformance = {
  clinic_id: string;
  country: ReportCountry;
  total_supply_cost: number;
  supply_consumption_count: number;
  critical_stockout_count: number;
  expiry_risk_count: number;
  currency: ReportCurrency;
};

export type MonthlyClinicSupplyPerformance = {
  /** Primer día del mes, YYYY-MM-01 (UTC). */
  month_start: string;
  clinics: ClinicSupplyPerformance[];
};

export type PipelineRunStatus = {
  run_id: string;
  status: "queued" | "running" | "completed" | "completed_with_warnings" | "failed" | "crashed" | "cancelled";
  month_start: string;
  started_at: string | null;
  finished_at: string | null;
  records_processed: number | null;
  warnings: string[];
  /** Clínicas con compras sin coste registrado en el mes de esa corrida: su costo está incompleto. */
  clinics_with_unrecorded_cost: string[];
  is_stale: boolean;
};

export type KpiKey =
  | "total_supply_cost"
  | "supply_consumption_count"
  | "critical_stockout_count"
  | "expiry_risk_count";

export type KpiDefinition = {
  key: KpiKey;
  /** Nombre EXACTO del KPI en la sección "KPIs a medir" del CONTEXT. */
  name: string;
  /** Columna "Qué mide" del CONTEXT. */
  description: string;
  kind: "money" | "count";
};

/** Los 4 KPIs del CONTEXT, en el orden en que los presenta el documento. */
export const KPI_DEFINITIONS: KpiDefinition[] = [
  {
    key: "total_supply_cost",
    name: "Costo de insumos por clínica",
    description: "Cuánto gastó una clínica comprando insumos médicos durante el mes.",
    kind: "money",
  },
  {
    key: "supply_consumption_count",
    name: "Volumen de consumo de insumos",
    description: "Cuántos eventos de consumo de insumos registró una clínica durante el mes.",
    kind: "count",
  },
  {
    key: "critical_stockout_count",
    name: "Frecuencia de quiebre crítico",
    description: "Cuántas veces durante el mes una clínica cayó por debajo del umbral mínimo de un insumo.",
    kind: "count",
  },
  {
    key: "expiry_risk_count",
    name: "Conteo de riesgo de vencimiento",
    description: "Cuántos lotes de insumo en una clínica fueron marcados por acercarse a su fecha de vencimiento durante el mes.",
    kind: "count",
  },
];

const COUNTRY_LABELS: Record<ReportCountry, string> = { US: "EE. UU.", UK: "Reino Unido" };

export function countryLabel(country: ReportCountry): string {
  return COUNTRY_LABELS[country] ?? country;
}

/** Sin catálogo de nombres de clínica en el proyecto: el id real, legible. */
export function clinicLabel(clinicId: string): string {
  return `Clínica ${clinicId}`;
}

/** 18420.5 + "USD" -> "18.420,50 US$". Nunca convierte ni suma monedas. */
export function formatMoney(amount: number, currency: ReportCurrency): string {
  return new Intl.NumberFormat("es-ES", { style: "currency", currency, minimumFractionDigits: 2 }).format(amount);
}

export function formatCount(value: number): string {
  return new Intl.NumberFormat("es-ES").format(value);
}

/** "2026-08-01" -> "Agosto de 2026". En UTC: el mes del informe es un mes UTC. */
export function formatMonthLabel(monthStart: string): string {
  const parsed = new Date(`${monthStart.slice(0, 10)}T00:00:00Z`);
  if (Number.isNaN(parsed.getTime())) return monthStart;
  const label = new Intl.DateTimeFormat("es-ES", { month: "long", year: "numeric", timeZone: "UTC" }).format(parsed);
  return label.charAt(0).toUpperCase() + label.slice(1);
}

/** Último día del mes para mostrar el período completo: "2026-08-01" -> 31. */
export function lastDayOfMonth(monthStart: string): number {
  const [year, month] = monthStart.split("-").map(Number);
  return new Date(Date.UTC(year, month, 0)).getUTCDate();
}

/** Valor de <input type="month"> ("2026-08") -> parámetro de la API ("2026-08-01"). */
export function toMonthStartParam(monthInput: string): string | null {
  return /^\d{4}-(0[1-9]|1[0-2])$/.test(monthInput) ? `${monthInput}-01` : null;
}

/** "2026-08-01" -> "2026-08" para rellenar el <input type="month">. */
export function toMonthInputValue(monthStart: string): string {
  return monthStart.slice(0, 7);
}

export type KpiEntry = { clinicId: string; country: ReportCountry; value: number };

/** Filas de un KPI de conteo, de mayor a menor (desempate por clínica). */
export function rankClinicsByKpi(clinics: ClinicSupplyPerformance[], key: KpiKey): KpiEntry[] {
  return clinics
    .map((clinic) => ({ clinicId: clinic.clinic_id, country: clinic.country, value: clinic[key] }))
    .sort((a, b) => b.value - a.value || Number(a.clinicId) - Number(b.clinicId));
}

export type CurrencyGroup = {
  currency: ReportCurrency;
  country: ReportCountry;
  entries: KpiEntry[];
  total: number;
};

/**
 * Costo agrupado por moneda: un grupo por país con su propio total. Restricción
 * de negocio del CONTEXT: USD y GBP se reportan lado a lado, nunca sumados, así
 * que NO existe un total de red para el costo. EE. UU. primero, como en el CONTEXT.
 */
export function groupCostByCurrency(clinics: ClinicSupplyPerformance[]): CurrencyGroup[] {
  const groups: CurrencyGroup[] = [];
  for (const currency of ["USD", "GBP"] as ReportCurrency[]) {
    const inCurrency = clinics.filter((clinic) => clinic.currency === currency);
    if (inCurrency.length === 0) continue;
    const entries = rankClinicsByKpi(inCurrency, "total_supply_cost");
    const cents = entries.reduce((sum, entry) => sum + Math.round(entry.value * 100), 0);
    groups.push({ currency, country: inCurrency[0].country, entries, total: cents / 100 });
  }
  return groups;
}

/** Suma de un KPI de conteo en toda la red (los conteos no tienen moneda). */
export function networkTotal(clinics: ClinicSupplyPerformance[], key: KpiKey): number {
  return clinics.reduce((sum, clinic) => sum + clinic[key], 0);
}

export type ReportNotice = { tone: "warning" | "critical"; message: string };

/**
 * Avisos para la dirección, en lenguaje llano (actividad adicional de la
 * Parte 3, preguntas de diseño 4 y 6):
 *
 * - Silencio: `is_stale` = el último mes cerrado todavía no está calculado.
 *   Sin este aviso, la CEO vería el mes anterior sin saber que falta uno.
 * - Última actualización fallida del mes que se está viendo.
 * - Captura incompleta: la corrida detectó actividad real de inventario que
 *   no llegó al sistema de medición, así que las cifras pueden quedarse cortas.
 *
 * Los avisos de una corrida solo se aplican si esa corrida es del mismo mes
 * que se muestra. Avisos puramente técnicos (p. ej. eval_snapshot_failed) no
 * se enseñan: no cambian lo que significan los números.
 */
export function describeReportNotices(run: PipelineRunStatus | null, monthStart: string | null): ReportNotice[] {
  const notices: ReportNotice[] = [];
  if (!run) return notices;

  if (run.is_stale) {
    notices.push({
      tone: "warning",
      message: "El informe del último mes cerrado todavía no se ha generado. Es posible que estés viendo un mes anterior.",
    });
  }

  const sameMonth = monthStart !== null && run.month_start === monthStart;
  if (!sameMonth) return notices;

  if (run.status === "failed" || run.status === "crashed") {
    notices.push({
      tone: "critical",
      message: "La última actualización de este mes no pudo completarse. Las cifras son las de la actualización anterior.",
    });
  }
  const unrecorded = clinicsWithUnrecordedCost(run, monthStart);
  if (unrecorded.length > 0) {
    notices.push({
      tone: "warning",
      message: `Algunas compras de este mes se registraron sin coste (${unrecorded.map(clinicLabel).join(", ")}): el costo de insumos de esas clínicas está incompleto, no es un gasto cero.`,
    });
  }
  const captureWarnings = run.warnings.filter(
    (warning) => warning.startsWith("low_capture_ratio") || warning === "coverage_unavailable",
  );
  if (captureWarnings.length > 0) {
    notices.push({
      tone: "warning",
      message:
        "Parte de la actividad de inventario de este mes no quedó registrada en el sistema de medición: algunas cifras pueden estar por debajo de la realidad.",
    });
  }
  return notices;
}

/**
 * Clínicas cuyo costo del mes mostrado está incompleto porque alguna compra
 * llegó sin `unit_cost`. Principio del pipeline: coste desconocido no es coste
 * cero, así que la pantalla no puede enseñar ese "0,00" como un gasto real.
 * Solo aplica si la última corrida es del mismo mes que se muestra.
 */
export function clinicsWithUnrecordedCost(run: PipelineRunStatus | null, monthStart: string | null): string[] {
  if (!run || monthStart === null || run.month_start !== monthStart) return [];
  return run.clinics_with_unrecorded_cost ?? [];
}

const UPDATED_AT_FORMAT =new Intl.DateTimeFormat("es-ES", { dateStyle: "long", timeStyle: "short", timeZone: "UTC" });

/** Fecha de la última actualización del mes mostrado, o null si no aplica. */
export function describeLastUpdate(run: PipelineRunStatus | null, monthStart: string | null): string | null {
  if (!run || !run.finished_at || run.month_start !== monthStart) return null;
  if (run.status !== "completed" && run.status !== "completed_with_warnings") return null;
  const parsed = new Date(run.finished_at);
  return Number.isNaN(parsed.getTime()) ? null : `${UPDATED_AT_FORMAT.format(parsed)} (UTC)`;
}
