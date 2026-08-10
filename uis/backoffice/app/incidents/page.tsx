"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import { ApiFieldError } from "../../services/http";
import { getIncidents, updateIncidentStatus } from "../../services/incidentsApi";
import {
  BRANCH_LABELS,
  BRANCH_OPTIONS,
  CATEGORY_LABELS,
  ORIGIN_LABELS,
  ORIGIN_OPTIONS,
  STATUS_LABELS,
  STATUS_OPTIONS,
  STATUS_TRANSITIONS,
  type Incident,
  type IncidentFilters,
  type IncidentStatus,
} from "../../types/incident";

const STATUS_BADGE_STYLES: Record<IncidentStatus, React.CSSProperties> = {
  open: { background: "#fee2e2", color: "#991b1b" },
  in_progress: { background: "#fef3c7", color: "#92400e" },
  resolved: { background: "#dcfce7", color: "#166534" },
  discarded: { background: "#e2e8f0", color: "#334155" },
};

export default function IncidentsPage() {
  const [filters, setFilters] = useState<IncidentFilters>({ status: "", origin: "", branch: "" });
  const [incidents, setIncidents] = useState<Incident[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [updatingId, setUpdatingId] = useState<number | null>(null);
  const [updateNotice, setUpdateNotice] = useState<string | null>(null);

  const loadIncidents = useCallback(async (activeFilters: IncidentFilters) => {
    setIsLoading(true);
    setLoadError(null);
    try {
      const results = await getIncidents(activeFilters);
      setIncidents(results);
    } catch {
      setLoadError("The incidents list could not be loaded. Check that the API is running and try again.");
    } finally {
      setIsLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadIncidents(filters);
  }, [filters, loadIncidents]);

  const setFilter = (key: keyof IncidentFilters, value: string) => {
    setFilters((prev) => ({ ...prev, [key]: value }));
  };

  const changeStatus = async (incident: Incident, nextStatus: IncidentStatus) => {
    const previousStatus = incident.status;
    setUpdateNotice(null);
    setUpdatingId(incident.id);
    setIncidents((prev) =>
      prev.map((item) => (item.id === incident.id ? { ...item, status: nextStatus } : item)),
    );

    try {
      const updated = await updateIncidentStatus(incident.id, nextStatus);
      setIncidents((prev) => prev.map((item) => (item.id === incident.id ? updated : item)));
    } catch (error) {
      setIncidents((prev) =>
        prev.map((item) => (item.id === incident.id ? { ...item, status: previousStatus } : item)),
      );
      if (error instanceof ApiFieldError) {
        setUpdateNotice(
          `The status of "${incident.title}" was not changed: ${error.fieldErrors[0]?.message ?? "invalid transition."}`,
        );
      } else {
        setUpdateNotice(`The status of "${incident.title}" could not be updated. Please try again.`);
      }
    } finally {
      setUpdatingId(null);
    }
  };

  const hasActiveFilters = Boolean(filters.status || filters.origin || filters.branch);

  return (
    <main className="shell" style={{ padding: "24px 0 48px" }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", flexWrap: "wrap", gap: 10 }}>
        <h1 style={{ marginBottom: 0 }}>Incidents</h1>
        <Link href="/incidents/new" className="nav-link" style={{ color: "var(--brand)" }}>
          + Report incident
        </Link>
      </div>

      <div className="panel" style={{ margin: "16px 0" }}>
        <div className="form-grid">
          <label>
            Status
            <select value={filters.status ?? ""} onChange={(event) => setFilter("status", event.target.value)}>
              <option value="">All statuses</option>
              {STATUS_OPTIONS.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </select>
          </label>
          <label>
            Origin
            <select value={filters.origin ?? ""} onChange={(event) => setFilter("origin", event.target.value)}>
              <option value="">All origins</option>
              {ORIGIN_OPTIONS.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </select>
          </label>
          <label>
            Branch
            <select value={filters.branch ?? ""} onChange={(event) => setFilter("branch", event.target.value)}>
              <option value="">All branches</option>
              {BRANCH_OPTIONS.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </select>
          </label>
        </div>
      </div>

      {updateNotice && (
        <p className="error-text" role="alert" style={{ marginTop: 0 }}>
          {updateNotice}
        </p>
      )}

      {isLoading && <p style={{ color: "var(--muted)" }}>Loading incidents…</p>}

      {!isLoading && loadError && (
        <div className="panel" style={{ display: "flex", gap: 12, alignItems: "center", flexWrap: "wrap" }}>
          <span className="error-text">{loadError}</span>
          <button type="button" onClick={() => void loadIncidents(filters)}>
            Retry
          </button>
        </div>
      )}

      {!isLoading && !loadError && incidents.length === 0 && (
        <div className="panel">
          <p style={{ margin: 0, color: "var(--muted)" }}>
            {hasActiveFilters
              ? "No incidents match the selected filters. Try clearing them to see the full list."
              : "No incidents have been reported yet. Use “Report incident” to register the first one."}
          </p>
        </div>
      )}

      {!isLoading && !loadError && incidents.length > 0 && (
        <div className="panel" style={{ overflowX: "auto" }}>
          <table className="table">
            <thead>
              <tr>
                <th>Title</th>
                <th>Category</th>
                <th>Origin</th>
                <th>Branch</th>
                <th>Created</th>
                <th>Status</th>
                <th>Change status</th>
              </tr>
            </thead>
            <tbody>
              {incidents.map((incident) => {
                const transitions = STATUS_TRANSITIONS[incident.status] ?? [];
                return (
                  <tr key={incident.id}>
                    <td>
                      <strong>{incident.title}</strong>
                      <div style={{ color: "var(--muted)", fontSize: 13, maxWidth: 380 }}>{incident.description}</div>
                    </td>
                    <td>{CATEGORY_LABELS[incident.category] ?? incident.category}</td>
                    <td>{ORIGIN_LABELS[incident.origin] ?? incident.origin}</td>
                    <td>{BRANCH_LABELS[incident.branch] ?? incident.branch}</td>
                    <td>{new Date(incident.created_at).toLocaleDateString("en-GB")}</td>
                    <td>
                      <span className="status-badge" style={STATUS_BADGE_STYLES[incident.status]}>
                        {STATUS_LABELS[incident.status] ?? incident.status}
                      </span>
                    </td>
                    <td>
                      {transitions.length === 0 ? (
                        <span style={{ color: "var(--muted)", fontSize: 13 }}>Final</span>
                      ) : (
                        <select
                          value=""
                          disabled={updatingId === incident.id}
                          onChange={(event) => {
                            const nextStatus = event.target.value as IncidentStatus;
                            if (nextStatus) void changeStatus(incident, nextStatus);
                          }}
                        >
                          <option value="">{updatingId === incident.id ? "Updating…" : "Move to…"}</option>
                          {transitions.map((value) => (
                            <option key={value} value={value}>
                              {STATUS_LABELS[value]}
                            </option>
                          ))}
                        </select>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </main>
  );
}
