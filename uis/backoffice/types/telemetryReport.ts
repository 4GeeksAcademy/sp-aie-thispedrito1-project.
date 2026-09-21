/**
 * Contrato de GET /telemetry/report y utilidades puras de la pantalla
 * /telemetry. Separado de types/telemetry.ts a proposito: aquel describe los
 * eventos que el frontend ENVIA (envelope de TelemetryService), este describe
 * el reporte tecnico que el frontend RECIBE.
 *
 * Las filas replican los esquemas Pydantic de services/api/models.py
 * (EventsPerDayRow, ErrorRateRow, WebVitalLatencyRow, AuthFailureRateRow).
 * Si el backend cambia una columna, este archivo es el primer sitio a tocar.
 */

export type EventsPerDayRow = {
  /** Fecha UTC en formato YYYY-MM-DD. */
  date: string;
  event_type: string;
  count: number;
};

export type ErrorRateRow = {
  date: string;
  total_events: number;
  error_events: number;
  /** Razon entre 0 y 1, no porcentaje. */
  error_rate: number;
};

export type WebVitalLatencyRow = {
  date: string;
  /** LCP, INP, CLS, FCP, TTFB... tal como los emite next/web-vitals. */
  metric_name: string;
  avg_value: number;
};

export type AuthFailureRateRow = {
  date: string;
  total_attempts: number;
  failed_attempts: number;
  /** Razon entre 0 y 1, no porcentaje. */
  failure_rate: number;
};

export type TelemetryReport = {
  period: { from: string; to: string };
  metrics: {
    events_per_day: EventsPerDayRow[];
    error_rate_by_day: ErrorRateRow[];
    web_vital_latency_by_day: WebVitalLatencyRow[];
    auth_failure_rate: AuthFailureRateRow[];
  };
};

/** Rango tal como lo manejan los <input type="date">: dias completos YYYY-MM-DD, ambos incluidos. */
export type DateRange = {
  start: string;
  end: string;
};

/** Parametros que espera la API: ISO 8601 en UTC, inicio inclusivo y fin EXCLUSIVO. */
export type ReportQuery = {
  start_date: string;
  end_date: string;
};

/** Misma ventana por defecto que el backend (REPORT_DEFAULT_WINDOW_DAYS). */
export const DEFAULT_WINDOW_DAYS = 7;

const MS_PER_DAY = 24 * 60 * 60 * 1000;

function toIsoDay(date: Date): string {
  return date.toISOString().slice(0, 10);
}

/**
 * Ultimos 7 dias naturales contando hoy (hoy y los 6 anteriores), en UTC.
 * Se trabaja en UTC y no en hora local porque el backend agrupa por
 * `timestamp.dt.date` sobre fechas UTC: si la pantalla usara la fecha local,
 * alguien en Madrid a las 00:30 veria "hoy" un dia por delante de los grupos.
 */
export function getDefaultDateRange(now: Date = new Date()): DateRange {
  const start = new Date(now.getTime() - (DEFAULT_WINDOW_DAYS - 1) * MS_PER_DAY);
  return { start: toIsoDay(start), end: toIsoDay(now) };
}

/** Devuelve un mensaje legible si el rango no es valido, o `null` si lo es. */
export function validateDateRange(range: DateRange): string | null {
  if (!range.start || !range.end) {
    return "Selecciona una fecha de inicio y una de fin.";
  }
  if (range.start > range.end) {
    return "La fecha de inicio no puede ser posterior a la de fin.";
  }
  return null;
}

/**
 * Traduce los dias que eligio la persona (ambos incluidos) al contrato de la
 * API (inicio inclusivo, fin exclusivo, UTC).
 */
export function toReportQuery(range: DateRange): ReportQuery {
  // La "Z" fija el instante en UTC; sin ella JavaScript usaria la hora local.
  const start = new Date(`${range.start}T00:00:00Z`);
  // Fin exclusivo: medianoche del dia SIGUIENTE, para que el ultimo dia
  // elegido entre completo. Sumar 24h exactas es seguro en UTC (sin cambios
  // de horario) y cruza bien fin de mes/año.
  const endExclusive = new Date(new Date(`${range.end}T00:00:00Z`).getTime() + MS_PER_DAY);
  return { start_date: start.toISOString(), end_date: endExclusive.toISOString() };
}

/** 0.125 -> "12,5 %". Formato es-ES, coherente con el resto de la UI. */
export function formatRate(rate: number): string {
  return new Intl.NumberFormat("es-ES", { style: "percent", maximumFractionDigits: 1 }).format(rate);
}

/**
 * Suma los conteos diarios por tipo de evento y ordena de mayor a menor.
 * La tabla diaria responde "que paso cada dia"; este total responde "que
 * tipo de evento domina el periodo", que es lo que muestran las barras.
 */
export function totalEventsByType(rows: EventsPerDayRow[]): { event_type: string; count: number }[] {
  const totals = rows.reduce<Record<string, number>>((acc, row) => {
    acc[row.event_type] = (acc[row.event_type] ?? 0) + row.count;
    return acc;
  }, {});

  return Object.entries(totals)
    .map(([event_type, count]) => ({ event_type, count }))
    .sort((a, b) => b.count - a.count || a.event_type.localeCompare(b.event_type));
}

/** CLS no tiene unidad; el resto de Web Vitals se miden en milisegundos. */
export function formatVitalValue(metricName: string, value: number): string {
  if (metricName === "CLS") {
    return value.toFixed(3);
  }
  return `${Math.round(value)} ms`;
}
