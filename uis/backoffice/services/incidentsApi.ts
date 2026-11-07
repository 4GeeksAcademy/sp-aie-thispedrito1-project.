import type { Incident, IncidentCreateInput, IncidentFilters, IncidentListItem, IncidentStatus, IncidentSummary } from "../types/incident";
import { statusChangeBody } from "../types/incident";
import { requestJson } from "./http";

export function getIncidents(filters: IncidentFilters): Promise<IncidentListItem[]> {
  const params = new URLSearchParams();
  if (filters.status) params.set("status", filters.status);
  if (filters.origin) params.set("origin", filters.origin);
  if (filters.branch) params.set("branch", filters.branch);
  if (filters.category) params.set("category", filters.category);

  const query = params.toString();
  return requestJson<IncidentListItem[]>(`/api/incidents${query ? `?${query}` : ""}`, undefined, { authRequired: true });
}

export function createIncident(payload: IncidentCreateInput): Promise<Incident> {
  return requestJson<Incident>("/api/incidents", {
    method: "POST",
    body: JSON.stringify(payload),
  }, { authRequired: true });
}

/** `confirmed` solo debe ser true tras la confirmación explícita de la persona:
 * cerrar una incidencia (resolved/discarded) es irreversible y la API lo exige. */
export function updateIncidentStatus(id: number, status: IncidentStatus, confirmed = false): Promise<Incident> {
  return requestJson<Incident>(`/api/incidents/${id}/status`, {
    method: "PATCH",
    body: JSON.stringify(statusChangeBody(status, confirmed)),
  }, { authRequired: true });
}

export function getIncidentSummary(): Promise<IncidentSummary> {
  return requestJson<IncidentSummary>("/api/incidents/summary", undefined, { authRequired: true });
}
