"use client";

import { useCallback } from "react";

import { AsyncSection } from "../../../components/AsyncSection";
import { useAsyncData } from "../../../hooks/useAsyncData";
import { getIncidentSummary } from "../../../services/incidentsApi";
import {
  BRANCH_LABELS,
  CATEGORY_LABELS,
  ORIGIN_LABELS,
  STATUS_LABELS,
  type IncidentSummary,
} from "../../../types/incident";

type MetricGroupProps = {
  title: string;
  counts: Record<string, number>;
  labels: Record<string, string>;
};

function MetricGroup({ title, counts, labels }: MetricGroupProps) {
  const entries = Object.entries(counts).sort(([, a], [, b]) => b - a);

  return (
    <section className="panel">
      <h2 style={{ marginTop: 0, fontSize: 17 }}>{title}</h2>
      <table className="table">
        <tbody>
          {entries.map(([value, count]) => (
            <tr key={value}>
              <td>{labels[value] ?? value}</td>
              <td style={{ textAlign: "right", fontWeight: 700 }}>{count}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}

export default function IncidentSummaryPage() {
  const fetchSummary = useCallback(() => getIncidentSummary(), []);
  const { data: summary, isLoading, error, reload } = useAsyncData<IncidentSummary>(
    fetchSummary,
    "No se pudieron cargar las métricas del resumen. Verifica que la API esté activa e inténtalo de nuevo.",
  );

  return (
    <main className="shell" style={{ padding: "24px 0 48px" }}>
      <h1>Resumen de incidencias</h1>
      <p style={{ color: "var(--muted)" }}>
        Métricas agregadas de toda la red para visibilidad ejecutiva: totales por estado, categoría,
        origen y sede.
      </p>

      <AsyncSection
        isLoading={isLoading}
        error={error}
        onRetry={reload}
        loadingLabel="Cargando métricas…"
        isEmpty={!summary}
        emptyLabel="Todavía no hay incidencias registradas para resumir."
      >
        {summary && (
          <>
          <div className="panel" style={{ marginBottom: 16, display: "flex", alignItems: "baseline", gap: 10 }}>
            <span style={{ fontSize: 34, fontWeight: 800 }}>{summary.total}</span>
            <span style={{ color: "var(--muted)" }}>incidencias registradas en toda la red</span>
          </div>
          <div
            style={{
              display: "grid",
              gridTemplateColumns: "repeat(auto-fit, minmax(240px, 1fr))",
              gap: 16,
              alignItems: "start",
            }}
          >
            <MetricGroup title="Por estado" counts={summary.by_status} labels={STATUS_LABELS} />
            <MetricGroup title="Por categoría" counts={summary.by_category} labels={CATEGORY_LABELS} />
            <MetricGroup title="Por origen" counts={summary.by_origin} labels={ORIGIN_LABELS} />
            <MetricGroup title="Por sede" counts={summary.by_branch} labels={BRANCH_LABELS} />
            </div>
          </>
        )}
      </AsyncSection>
    </main>
  );
}
