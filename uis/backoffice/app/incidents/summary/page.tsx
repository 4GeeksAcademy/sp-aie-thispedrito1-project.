"use client";

import { useCallback, useEffect, useState } from "react";

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
  const [summary, setSummary] = useState<IncidentSummary | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);

  const loadSummary = useCallback(async () => {
    setIsLoading(true);
    setLoadError(null);
    try {
      setSummary(await getIncidentSummary());
    } catch {
      setLoadError("The summary metrics could not be loaded. Check that the API is running and try again.");
    } finally {
      setIsLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadSummary();
  }, [loadSummary]);

  return (
    <main className="shell" style={{ padding: "24px 0 48px" }}>
      <h1>Incidents summary</h1>
      <p style={{ color: "var(--muted)" }}>
        Aggregated network metrics for executive visibility: totals by status, category, origin, and branch.
      </p>

      {isLoading && <p style={{ color: "var(--muted)" }}>Loading metrics…</p>}

      {!isLoading && loadError && (
        <div className="panel" style={{ display: "flex", gap: 12, alignItems: "center", flexWrap: "wrap" }}>
          <span className="error-text">{loadError}</span>
          <button type="button" onClick={() => void loadSummary()}>
            Retry
          </button>
        </div>
      )}

      {!isLoading && !loadError && summary && (
        <>
          <div className="panel" style={{ marginBottom: 16, display: "flex", alignItems: "baseline", gap: 10 }}>
            <span style={{ fontSize: 34, fontWeight: 800 }}>{summary.total}</span>
            <span style={{ color: "var(--muted)" }}>incidents registered across the network</span>
          </div>
          <div
            style={{
              display: "grid",
              gridTemplateColumns: "repeat(auto-fit, minmax(240px, 1fr))",
              gap: 16,
              alignItems: "start",
            }}
          >
            <MetricGroup title="By status" counts={summary.by_status} labels={STATUS_LABELS} />
            <MetricGroup title="By category" counts={summary.by_category} labels={CATEGORY_LABELS} />
            <MetricGroup title="By origin" counts={summary.by_origin} labels={ORIGIN_LABELS} />
            <MetricGroup title="By branch" counts={summary.by_branch} labels={BRANCH_LABELS} />
          </div>
        </>
      )}
    </main>
  );
}
