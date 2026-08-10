import type {
  Incident,
  IncidentCreateInput,
  IncidentFilters,
  IncidentStatus,
  IncidentSummary,
} from "../types/incident";
import { requestJson } from "./http";

export function getIncidents(filters: IncidentFilters): Promise<Incident[]> {
  const params = new URLSearchParams();
  if (filters.status) params.set("status", filters.status);
  if (filters.origin) params.set("origin", filters.origin);
  if (filters.branch) params.set("branch", filters.branch);
  if (filters.category) params.set("category", filters.category);

  const query = params.toString();
  return requestJson<Incident[]>(`/api/incidents${query ? `?${query}` : ""}`, undefined, { authRequired: true });
}

export function createIncident(payload: IncidentCreateInput): Promise<Incident> {
  return requestJson<Incident>("/api/incidents", {
    method: "POST",
    body: JSON.stringify(payload),
  }, { authRequired: true });
}

export function updateIncidentStatus(id: number, status: IncidentStatus): Promise<Incident> {
  return requestJson<Incident>(`/api/incidents/${id}/status`, {
    method: "PATCH",
    body: JSON.stringify({ status }),
  }, { authRequired: true });
}

export function getIncidentSummary(): Promise<IncidentSummary> {
  return requestJson<IncidentSummary>("/api/incidents/summary", undefined, { authRequired: true });
}
