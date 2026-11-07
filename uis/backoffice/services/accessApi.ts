// Llamadas de Roles y Permisos: administración (solo Admin) e informes
// internos (alcance departamental). Todo por requestJson, así que un 403
// llega como ApiRequestError con el mensaje ya en español.

import type {
  AdminUserCreatePayload,
  Department,
  InternalReport,
  InternalReportPayload,
  ManagedUser,
  Role,
  UserAssignmentPayload,
} from "../types/access";
import { requestJson } from "./http";

const AUTH = { authRequired: true } as const;

function send<T>(path: string, method: string, body?: unknown): Promise<T> {
  return requestJson<T>(path, { method, body: body === undefined ? undefined : JSON.stringify(body) }, AUTH);
}

export const accessApi = {
  listRoles: () => requestJson<Role[]>("/roles", undefined, AUTH),
  createRole: (payload: { code: string; name: string; rank: number }) => send<Role>("/roles", "POST", payload),
  renameRole: (id: string, name: string) => send<Role>(`/roles/${encodeURIComponent(id)}`, "PUT", { name }),

  listDepartments: () => requestJson<Department[]>("/departments", undefined, AUTH),
  createDepartment: (payload: { code: string; name: string }) => send<Department>("/departments", "POST", payload),
  renameDepartment: (id: string, name: string) =>
    send<Department>(`/departments/${encodeURIComponent(id)}`, "PUT", { name }),

  listUsers: () => requestJson<ManagedUser[]>("/users", undefined, AUTH),
  createUser: (payload: AdminUserCreatePayload) => send<{ id: string }>("/users", "POST", payload),
  assign: (userId: string, payload: UserAssignmentPayload) =>
    send<ManagedUser>(`/users/${encodeURIComponent(userId)}/assignment`, "PATCH", payload),
};

export const internalReportsApi = {
  list: () => requestJson<InternalReport[]>("/internal-reports", undefined, AUTH),
  create: (payload: InternalReportPayload) => send<InternalReport>("/internal-reports", "POST", payload),
  update: (id: string, payload: Partial<InternalReportPayload>) =>
    send<InternalReport>(`/internal-reports/${encodeURIComponent(id)}`, "PUT", payload),
  approve: (id: string) => send<InternalReport>(`/internal-reports/${encodeURIComponent(id)}/approve`, "POST"),
  remove: (id: string) => send<void>(`/internal-reports/${encodeURIComponent(id)}`, "DELETE"),
};
