"use client";

import { useCallback, useState, type FormEvent, type ReactNode } from "react";

import { AsyncSection } from "../../components/AsyncSection";
import { useAsyncData } from "../../hooks/useAsyncData";
import { getTelemetryReport } from "../../services/telemetryReportApi";
import {
  formatRate,
  formatVitalValue,
  getDefaultDateRange,
  toReportQuery,
  totalEventsByType,
  validateDateRange,
  type DateRange,
  type TelemetryReport,
} from "../../types/telemetryReport";

type BarItem = {
  label: string;
  value: number;
  /** Texto a la derecha de la barra (el numero ya formateado). */
  display: string;
};

type BarListProps = {
  items: BarItem[];
  /** Valor que ocupa el 100% del ancho. Para tasas es 1; para conteos, el mayor. */
  max: number;
  tone: "brand" | "critical";
};

/**
 * Barras horizontales solo con CSS: sin libreria de graficos y con los
 * tokens del tema, asi que funcionan igual en modo oscuro y claro. Son
 * decorativas (aria-hidden): los valores exactos viven en la tabla de cada
 * panel, que es lo que lee un lector de pantalla.
 */
function BarList({ items, max, tone }: BarListProps) {
  const color = tone === "critical" ? "var(--critical)" : "var(--brand)";

  return (
    <div aria-hidden="true" style={{ display: "grid", gap: 8, marginBottom: 14 }}>
      {items.map((item) => {
        const width = max > 0 ? Math.min(100, (item.value / max) * 100) : 0;
        return (
          <div
            key={item.label}
            style={{ display: "grid", gridTemplateColumns: "minmax(90px, 30%) 1fr auto", gap: 10, alignItems: "center" }}
          >
            {/* title: los event_type largos (web_vital_recorded...) se cortan con
                ellipsis en esta columna; al pasar el raton se ve el nombre entero. */}
            <span
              className="mono"
              title={item.label}
              style={{ fontSize: 12, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}
            >
              {item.label}
            </span>
            <span style={{ background: "var(--line)", borderRadius: 2, height: 10, overflow: "hidden" }}>
              <span style={{ display: "block", width: `${width}%`, height: "100%", background: color }} />
            </span>
            <span className="mono" style={{ fontSize: 12, color: "var(--muted)" }}>
              {item.display}
            </span>
          </div>
        );
      })}
    </div>
  );
}

type MetricPanelProps = {
  title: string;
  /** La pregunta tecnica/operacional que responde la metrica. */
  question: string;
  isEmpty: boolean;
  children: ReactNode;
};

function MetricPanel({ title, question, isEmpty, children }: MetricPanelProps) {
  return (
    <section className="panel">
      <h2 style={{ marginTop: 0, marginBottom: 4, fontSize: 17 }}>{title}</h2>
      <p style={{ marginTop: 0, color: "var(--muted)", fontSize: 13 }}>{question}</p>
      {isEmpty ? (
        <p style={{ margin: 0, color: "var(--muted)" }}>Sin eventos de este tipo en el período seleccionado.</p>
      ) : (
        children
      )}
    </section>
  );
}

/** Tabla con el detalle exacto, plegada por defecto para no alargar el panel. */
function DetailTable({ headers, rows }: { headers: string[]; rows: ReactNode[][] }) {
  return (
    <details>
      <summary style={{ cursor: "pointer", color: "var(--muted)", fontSize: 13 }}>Ver detalle ({rows.length} filas)</summary>
      <div style={{ overflowX: "auto", marginTop: 8 }}>
        <table className="table">
          <thead>
            <tr>
              {headers.map((header) => (
                <th key={header}>{header}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((cells, rowIndex) => (
              <tr key={rowIndex}>
                {cells.map((cell, cellIndex) => (
                  <td key={cellIndex} className="mono">
                    {cell}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </details>
  );
}

const PERIOD_FORMAT = new Intl.DateTimeFormat("es-ES", {
  dateStyle: "medium",
  timeStyle: "short",
  timeZone: "UTC",
});

function formatPeriodBound(iso: string): string {
  const parsed = new Date(iso);
  return Number.isNaN(parsed.getTime()) ? iso : `${PERIOD_FORMAT.format(parsed)} UTC`;
}

function ReportBody({ report }: { report: TelemetryReport }) {
  const { events_per_day, error_rate_by_day, web_vital_latency_by_day, auth_failure_rate } = report.metrics;
  const eventTotals = totalEventsByType(events_per_day);

  return (
    <>
      <div className="panel" style={{ marginBottom: 16 }}>
        <span style={{ color: "var(--muted)", fontSize: 13 }}>Período del reporte: </span>
        <span className="mono">{formatPeriodBound(report.period.from)}</span>
        <span style={{ color: "var(--muted)" }}> → </span>
        <span className="mono">{formatPeriodBound(report.period.to)}</span>
        <span style={{ color: "var(--muted)", fontSize: 12 }}> (fin no incluido)</span>
      </div>

      {/* 420px de minimo: 2x2 en escritorio (con 320px salian 3 columnas y el
          cuarto panel quedaba solo en otra fila). min(100%, ...) evita que la
          rejilla desborde en un movil de ~400px, donde queda en una columna. */}
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(auto-fit, minmax(min(100%, 420px), 1fr))",
          gap: 16,
          alignItems: "start",
        }}
      >
        <MetricPanel
          title="Volumen de eventos"
          question="¿Qué eventos ocurren en el sistema y con qué frecuencia?"
          isEmpty={events_per_day.length === 0}
        >
          <BarList
            tone="brand"
            max={eventTotals[0]?.count ?? 0}
            items={eventTotals.map((row) => ({ label: row.event_type, value: row.count, display: String(row.count) }))}
          />
          <DetailTable
            headers={["Fecha", "Tipo de evento", "Eventos"]}
            rows={events_per_day.map((row) => [row.date, row.event_type, row.count])}
          />
        </MetricPanel>

        <MetricPanel
          title="Tasa de error por día"
          question="¿Qué proporción de los eventos de cada día son errores?"
          isEmpty={error_rate_by_day.length === 0}
        >
          <BarList
            tone="critical"
            max={1}
            items={error_rate_by_day.map((row) => ({
              label: row.date,
              value: row.error_rate,
              display: `${formatRate(row.error_rate)} (${row.error_events}/${row.total_events})`,
            }))}
          />
          <DetailTable
            headers={["Fecha", "Eventos", "Errores", "Tasa"]}
            rows={error_rate_by_day.map((row) => [row.date, row.total_events, row.error_events, formatRate(row.error_rate)])}
          />
        </MetricPanel>

        <MetricPanel
          title="Latencia percibida (Web Vitals)"
          question="¿Qué tan rápido responde el backoffice para quien lo usa, día a día?"
          isEmpty={web_vital_latency_by_day.length === 0}
        >
          <div style={{ overflowX: "auto" }}>
            <table className="table">
              <thead>
                <tr>
                  <th>Fecha</th>
                  <th>Métrica</th>
                  <th>Promedio</th>
                </tr>
              </thead>
              <tbody>
                {web_vital_latency_by_day.map((row) => (
                  <tr key={`${row.date}-${row.metric_name}`}>
                    <td className="mono">{row.date}</td>
                    <td className="mono">{row.metric_name}</td>
                    <td className="mono">{formatVitalValue(row.metric_name, row.avg_value)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </MetricPanel>

        <MetricPanel
          title="Fallos de login por día"
          question="¿Qué proporción de los intentos de inicio de sesión fallan cada día?"
          isEmpty={auth_failure_rate.length === 0}
        >
          <BarList
            tone="critical"
            max={1}
            items={auth_failure_rate.map((row) => ({
              label: row.date,
              value: row.failure_rate,
              display: `${formatRate(row.failure_rate)} (${row.failed_attempts}/${row.total_attempts})`,
            }))}
          />
          <DetailTable
            headers={["Fecha", "Intentos", "Fallidos", "Tasa"]}
            rows={auth_failure_rate.map((row) => [row.date, row.total_attempts, row.failed_attempts, formatRate(row.failure_rate)])}
          />
        </MetricPanel>
      </div>
    </>
  );
}

export default function TelemetryDashboardPage() {
  // Dos estados a proposito: `draft` es lo que la persona va escribiendo en
  // los campos, `applied` es el rango que realmente se pide a la API. Asi no
  // se lanza una peticion por cada cambio en un <input type="date">.
  const [draft, setDraft] = useState<DateRange>(() => getDefaultDateRange());
  const [applied, setApplied] = useState<DateRange>(draft);
  const [rangeError, setRangeError] = useState<string | null>(null);

  // `applied` solo cambia de referencia al pulsar "Aplicar", que es cuando
  // useAsyncData debe relanzar la lectura.
  const fetchReport = useCallback(() => getTelemetryReport(toReportQuery(applied)), [applied]);
  const { data: report, isLoading, error, reload } = useAsyncData<TelemetryReport>(
    fetchReport,
    "No se pudo cargar el reporte técnico. Verifica que la API esté activa e inténtalo de nuevo.",
  );

  const applyRange = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const validationError = validateDateRange(draft);
    setRangeError(validationError);
    if (!validationError) {
      setApplied({ ...draft });
    }
  };

  const resetRange = () => {
    const defaults = getDefaultDateRange();
    setDraft(defaults);
    setRangeError(null);
    setApplied(defaults);
  };

  return (
    <main className="shell" style={{ padding: "24px 0 48px" }}>
      <h1>Telemetría técnica</h1>
      <p style={{ color: "var(--muted)" }}>
        Radar operacional para el equipo de ingeniería: volumen de eventos, errores, latencia y fallos de
        autenticación. No es un reporte de negocio.
      </p>

      {/* noValidate: sin el, los min/max de los <input type="date"> activan la
          validacion nativa, que bloquea el submit con un globo del navegador
          (en su idioma) y validateDateRange nunca llega a mostrar su mensaje.
          min/max se mantienen como pista visual en el selector de calendario. */}
      <form
        noValidate
        onSubmit={applyRange}
        className="panel"
        style={{ display: "flex", gap: 12, alignItems: "flex-end", flexWrap: "wrap", marginBottom: 16 }}
      >
        <label>
          Desde (UTC)
          <input
            type="date"
            value={draft.start}
            max={draft.end || undefined}
            onChange={(event) => setDraft((prev) => ({ ...prev, start: event.target.value }))}
          />
        </label>
        <label>
          Hasta, incluido (UTC)
          <input
            type="date"
            value={draft.end}
            min={draft.start || undefined}
            onChange={(event) => setDraft((prev) => ({ ...prev, end: event.target.value }))}
          />
        </label>
        <button type="submit" className="primary">
          Aplicar
        </button>
        <button type="button" onClick={resetRange}>
          Últimos 7 días
        </button>
        {rangeError && (
          <span className="error-text" role="alert" style={{ flexBasis: "100%" }}>
            {rangeError}
          </span>
        )}
      </form>

      <AsyncSection
        isLoading={isLoading}
        error={error}
        onRetry={reload}
        loadingLabel="Calculando reporte técnico…"
        isEmpty={!report}
        emptyLabel="El reporte llegó vacío."
      >
        {report && <ReportBody report={report} />}
      </AsyncSection>
    </main>
  );
}
