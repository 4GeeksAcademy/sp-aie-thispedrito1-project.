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
  open: { background: "rgba(255, 93, 93, 0.14)", color: "var(--critical)" },
  in_progress: { background: "rgba(255, 194, 75, 0.14)", color: "var(--warning)" },
  resolved: { background: "rgba(74, 222, 128, 0.14)", color: "var(--ok)" },
  discarded: { background: "rgba(138, 150, 145, 0.14)", color: "var(--muted)" },
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
      setLoadError("No se pudo cargar la lista de incidencias. Verifica que la API esté activa e inténtalo de nuevo.");
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
          `No se cambió el estado de "${incident.title}": ${error.fieldErrors[0]?.message ?? "transición no válida."}`,
        );
      } else {
        setUpdateNotice(`No se pudo actualizar el estado de "${incident.title}". Inténtalo de nuevo.`);
      }
    } finally {
      setUpdatingId(null);
    }
  };

  const hasActiveFilters = Boolean(filters.status || filters.origin || filters.branch);

  return (
    <main className="shell" style={{ padding: "24px 0 48px" }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", flexWrap: "wrap", gap: 10 }}>
        <h1 style={{ marginBottom: 0 }}>Incidencias</h1>
        <Link href="/incidents/new" className="nav-link" style={{ color: "var(--brand)" }}>
          + Reportar incidencia
        </Link>
      </div>

      <div className="panel" style={{ margin: "16px 0" }}>
        <div className="form-grid">
          <label>
            Estado
            <select value={filters.status ?? ""} onChange={(event) => setFilter("status", event.target.value)}>
              <option value="">Todos los estados</option>
              {STATUS_OPTIONS.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </select>
          </label>
          <label>
            Origen
            <select value={filters.origin ?? ""} onChange={(event) => setFilter("origin", event.target.value)}>
              <option value="">Todos los orígenes</option>
              {ORIGIN_OPTIONS.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </select>
          </label>
          <label>
            Sede
            <select value={filters.branch ?? ""} onChange={(event) => setFilter("branch", event.target.value)}>
              <option value="">Todas las sedes</option>
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

      {isLoading && <p style={{ color: "var(--muted)" }}>Cargando incidencias…</p>}

      {!isLoading && loadError && (
        <div className="panel" style={{ display: "flex", gap: 12, alignItems: "center", flexWrap: "wrap" }}>
          <span className="error-text">{loadError}</span>
          <button type="button" onClick={() => void loadIncidents(filters)}>
            Reintentar
          </button>
        </div>
      )}

      {!isLoading && !loadError && incidents.length === 0 && (
        <div className="panel">
          <p style={{ margin: 0, color: "var(--muted)" }}>
            {hasActiveFilters
              ? "Ninguna incidencia coincide con los filtros seleccionados. Prueba a quitarlos para ver la lista completa."
              : "Todavía no se ha reportado ninguna incidencia. Usa “Reportar incidencia” para registrar la primera."}
          </p>
        </div>
      )}

      {!isLoading && !loadError && incidents.length > 0 && (
        <div className="panel" style={{ overflowX: "auto" }}>
          <table className="table">
            <thead>
              <tr>
                <th>Título</th>
                <th>Categoría</th>
                <th>Origen</th>
                <th>Sede</th>
                <th>Creada</th>
                <th>Estado</th>
                <th>Cambiar estado</th>
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
                    <td>{new Date(incident.created_at).toLocaleDateString("es-ES")}</td>
                    <td>
                      <span className="status-badge" style={STATUS_BADGE_STYLES[incident.status]}>
                        {STATUS_LABELS[incident.status] ?? incident.status}
                      </span>
                    </td>
                    <td>
                      {transitions.length === 0 ? (
                        <span style={{ color: "var(--muted)", fontSize: 13 }}>Definitivo</span>
                      ) : (
                        <select
                          value=""
                          disabled={updatingId === incident.id}
                          onChange={(event) => {
                            const nextStatus = event.target.value as IncidentStatus;
                            if (nextStatus) void changeStatus(incident, nextStatus);
                          }}
                        >
                          <option value="">{updatingId === incident.id ? "Actualizando…" : "Mover a…"}</option>
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
