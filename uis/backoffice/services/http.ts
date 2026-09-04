import { clearAuthToken, getAuthToken } from "./session";
import { track } from "./telemetry";

const BASE_URL =
  (globalThis as { process?: { env?: { NEXT_PUBLIC_API_URL?: string } } }).process?.env?.NEXT_PUBLIC_API_URL ||
  "/api-proxy";

type RequestOptions = {
  authRequired?: boolean;
};

const isObjectRecord = (value: unknown): value is Record<string, unknown> => (
  typeof value === "object" && value !== null
);

export type FieldError = { field: string; message: string };

export class ApiFieldError extends Error {
  readonly fieldErrors: FieldError[];

  constructor(fieldErrors: FieldError[]) {
    super(`Algunos datos no son válidos — ${fieldErrors.map((e) => `${e.field}: ${e.message}`).join("; ")}`);
    this.name = "ApiFieldError";
    this.fieldErrors = fieldErrors;
  }
}

function extractFieldErrors(payload: unknown): FieldError[] | null {
  if (!isObjectRecord(payload)) return null;

  // Own incidents API format: { detail: { errors: [{ field, message }] } }
  if (isObjectRecord(payload.detail) && Array.isArray(payload.detail.errors)) {
    const fieldErrors = payload.detail.errors.filter(
      (item): item is FieldError =>
        isObjectRecord(item) && typeof item.field === "string" && typeof item.message === "string",
    );
    return fieldErrors.length > 0 ? fieldErrors : null;
  }

  // FastAPI/Pydantic 422 format: { detail: [{ loc: [...], msg }] }
  if (Array.isArray(payload.detail)) {
    const fieldErrors = payload.detail
      .map((item): FieldError | null => {
        if (!isObjectRecord(item) || typeof item.msg !== "string" || !Array.isArray(item.loc)) return null;
        const field = String(item.loc[item.loc.length - 1] ?? "");
        return field ? { field, message: item.msg } : null;
      })
      .filter((item): item is FieldError => item !== null);
    return fieldErrors.length > 0 ? fieldErrors : null;
  }

  return null;
}

function redirectToLogin() {
  if (typeof window === "undefined") return;
  clearAuthToken();
  if (!window.location.pathname.startsWith("/login")) {
    window.location.assign("/login");
  }
}

function extractErrorMessage(status: number, payload: unknown): string {
  if (status >= 500) {
    return "El servidor tuvo un problema inesperado. Inténtalo de nuevo en unos minutos.";
  }
  if (isObjectRecord(payload) && typeof payload.detail === "string") {
    return payload.detail;
  }
  if (status === 422) {
    return "Algunos datos enviados no son válidos. Revisa el formulario e inténtalo de nuevo.";
  }
  return "No se pudo completar la operación. Inténtalo de nuevo.";
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

  let response: Response;
  try {
    response = await fetch(`${BASE_URL}${path}`, {
      cache: "no-store",
      ...init,
      headers,
    });
  } catch {
    throw new Error("No se pudo conectar con el servidor. Revisa tu conexión e inténtalo de nuevo.");
  }

  if (!response.ok) {
    const errorPayload = await response.json().catch(() => null);
    if (response.status === 401 && options.authRequired) {
      // Unlike the "no token at all" branch above, reaching here means the
      // token existed and was sent — the server rejected it as expired/invalid
      // mid-session, which is exactly what session_expired describes.
      track("session_expired", { route_template: window.location.pathname });
      redirectToLogin();
      throw new Error("Sesion expirada o invalida. Inicia sesion nuevamente.");
    }
    const fieldErrors = extractFieldErrors(errorPayload);
    if (fieldErrors) {
      throw new ApiFieldError(fieldErrors);
    }
    throw new Error(extractErrorMessage(response.status, errorPayload));
  }

  if (response.status === 204) {
    return undefined as T;
  }

  try {
    return (await response.json()) as T;
  } catch {
    throw new Error("La respuesta del servidor no se pudo interpretar. Inténtalo de nuevo.");
  }
}
