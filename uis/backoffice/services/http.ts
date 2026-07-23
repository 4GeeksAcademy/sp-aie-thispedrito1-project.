import { clearAuthToken, getAuthToken } from "./session";

const BASE_URL =
  (globalThis as { process?: { env?: { NEXT_PUBLIC_API_URL?: string } } }).process?.env?.NEXT_PUBLIC_API_URL ||
  "/api-proxy";

type RequestOptions = {
  authRequired?: boolean;
};

const isObjectRecord = (value: unknown): value is Record<string, unknown> => (
  typeof value === "object" && value !== null
);

function redirectToLogin() {
  if (typeof window === "undefined") return;
  clearAuthToken();
  if (!window.location.pathname.startsWith("/login")) {
    window.location.assign("/login");
  }
}

function extractErrorMessage(status: number, payload: unknown): string {
  if (isObjectRecord(payload) && payload.detail !== undefined) {
    if (typeof payload.detail === "string") {
      return payload.detail;
    }
    if (status === 422) {
      return `API_422_DETAIL: ${JSON.stringify(payload.detail)}`;
    }
  }
  return `Error HTTP: ${status}`;
}

export async function requestJson<T>(path: string, init?: RequestInit, options: RequestOptions = {}): Promise<T> {
  const headers = new Headers(init?.headers || {});
  if (!headers.has("Content-Type") && init?.body) {
    headers.set("Content-Type", "application/json");
  }

  if (options.authRequired) {
    const token = getAuthToken();
    if (!token) {
      redirectToLogin();
      throw new Error("No authenticated session token was found.");
    }
    headers.set("Authorization", `Bearer ${token}`);
  }

  const response = await fetch(`${BASE_URL}${path}`, {
    cache: "no-store",
    ...init,
    headers,
  });

  if (!response.ok) {
    const errorPayload = await response.json().catch(() => null);
    if (response.status === 401 && options.authRequired) {
      redirectToLogin();
      throw new Error("Sesion expirada o invalida. Inicia sesion nuevamente.");
    }
    throw new Error(extractErrorMessage(response.status, errorPayload));
  }

  if (response.status === 204) {
    return undefined as T;
  }

  return (await response.json()) as T;
}
