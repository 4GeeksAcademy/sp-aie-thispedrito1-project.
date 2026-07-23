// /services/api.ts
import { clearAuthToken, getAuthToken } from "./session";
import { Candidate, CandidateFormData, CandidateNote } from "../types/tracker";
import {
  AuthMeResponse,
  LoginPayload,
  ProfileUpdatePayload,
  RegisterPayload,
  TokenResponse,
} from "../types/auth";

const TRACKER_BASE_URL =
  (globalThis as { process?: { env?: { NEXT_PUBLIC_API_URL?: string } } }).process?.env?.NEXT_PUBLIC_API_URL ||
  "https://playground.4geeks.com/tracker/api/v1";

const AUTH_BASE_URL =
  (globalThis as { process?: { env?: { NEXT_PUBLIC_AUTH_API_URL?: string } } }).process?.env?.NEXT_PUBLIC_AUTH_API_URL ||
  "/api-proxy";

const isObjectRecord = (value: unknown): value is Record<string, unknown> => (
  typeof value === "object" && value !== null
);

const redirectToLogin = () => {
  if (typeof window === "undefined") return;
  clearAuthToken();
  if (!window.location.pathname.startsWith("/login")) {
    window.location.assign("/login");
  }
};

const extractErrorMessage = (status: number, payload: unknown): string => {
  if (isObjectRecord(payload) && payload.detail !== undefined) {
    if (typeof payload.detail === "string") {
      return payload.detail;
    }
    if (status === 422) {
      return `API_422_DETAIL: ${JSON.stringify(payload.detail)}`;
    }
  }
  return `Error HTTP: ${status}`;
};

const normalizeNote = (raw: unknown): CandidateNote => {
  if (!isObjectRecord(raw)) {
    return {
      id: "",
      candidate_id: "",
      record_id: "",
      content: "",
      created_at: "",
    };
  }

  return {
    id: typeof raw.id === "string" || typeof raw.id === "number" ? raw.id : "",
    candidate_id: typeof raw.candidate_id === "string"
      ? raw.candidate_id
      : typeof raw.record_id === "string"
        ? raw.record_id
        : "",
    record_id: typeof raw.record_id === "string"
      ? raw.record_id
      : typeof raw.candidate_id === "string"
        ? raw.candidate_id
        : "",
    content: typeof raw.content === "string" ? raw.content : "",
    created_at: typeof raw.created_at === "string" ? raw.created_at : "",
  };
};

const parseNotesPayload = (payload: unknown): CandidateNote[] => {
  if (Array.isArray(payload)) {
    return payload.map(normalizeNote);
  }

  if (isObjectRecord(payload)) {
    const nested = Array.isArray(payload.data)
      ? payload.data
      : Array.isArray(payload.records)
        ? payload.records
        : [];
    return nested.map(normalizeNote);
  }

  return [];
};

type RequestOptions = {
  authRequired?: boolean;
};

async function requestJson<T>(
  baseUrl: string,
  path: string,
  init?: RequestInit,
  options: RequestOptions = {},
): Promise<T> {
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

  const response = await fetch(`${baseUrl}${path}`, {
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

export const talentApi = {
  login: async (payload: LoginPayload): Promise<TokenResponse> => {
    return requestJson<TokenResponse>(AUTH_BASE_URL, "/auth/login", {
      method: "POST",
      body: JSON.stringify(payload),
    });
  },

  register: async (payload: RegisterPayload): Promise<void> => {
    await requestJson(AUTH_BASE_URL, "/users", {
      method: "POST",
      body: JSON.stringify(payload),
    });
  },

  getMe: async (): Promise<AuthMeResponse> => {
    return requestJson<AuthMeResponse>(AUTH_BASE_URL, "/auth/me", undefined, { authRequired: true });
  },

  updateMyProfile: async (payload: ProfileUpdatePayload): Promise<AuthMeResponse["profile"]> => {
    return requestJson<AuthMeResponse["profile"]>(AUTH_BASE_URL, "/profiles/me", {
      method: "PUT",
      body: JSON.stringify(payload),
    }, { authRequired: true });
  },

  getRecords: async (): Promise<Candidate[]> => {
    return requestJson<Candidate[]>(TRACKER_BASE_URL, "/records", undefined, { authRequired: true });
  },

  getRecordById: async (id: string): Promise<Candidate> => {
    return requestJson<Candidate>(TRACKER_BASE_URL, `/records/${id}`, undefined, { authRequired: true });
  },

  createRecord: async (data: CandidateFormData): Promise<Candidate> => {
    return requestJson<Candidate>(TRACKER_BASE_URL, "/records", {
      method: "POST",
      body: JSON.stringify(data),
    }, { authRequired: true });
  },

  updateRecord: async (id: string, data: CandidateFormData): Promise<Candidate> => {
    return requestJson<Candidate>(TRACKER_BASE_URL, `/records/${id}`, {
      method: "PUT",
      body: JSON.stringify(data),
    }, { authRequired: true });
  },

  patchRecordStatus: async (id: string, cleanPayload: Partial<Pick<Candidate, "status" | "stage">>): Promise<Candidate> => {
    return requestJson<Candidate>(TRACKER_BASE_URL, `/records/${id}`, {
      method: "PATCH",
      body: JSON.stringify(cleanPayload),
    }, { authRequired: true });
  },

  getNotes: async (candidateId: string): Promise<CandidateNote[]> => {
    const response = await fetch(`${TRACKER_BASE_URL}/records/${candidateId}/notes`, {
      cache: "no-store",
      headers: {
        Authorization: `Bearer ${getAuthToken() || ""}`,
      },
    });

    if (response.status === 401) {
      redirectToLogin();
      throw new Error("Sesion expirada o invalida. Inicia sesion nuevamente.");
    }

    if (response.status === 404) {
      return [];
    }

    if (!response.ok) {
      const errorPayload = await response.json().catch(() => null);
      throw new Error(extractErrorMessage(response.status, errorPayload));
    }

    const payload = (await response.json()) as unknown;
    return parseNotesPayload(payload);
  },

  addNote: async (candidateId: string, content: string): Promise<CandidateNote> => {
    const payload = await requestJson<unknown>(TRACKER_BASE_URL, `/records/${candidateId}/notes`, {
      method: "POST",
      body: JSON.stringify({ content }),
    }, { authRequired: true });

    if (isObjectRecord(payload) && isObjectRecord(payload.data)) {
      return normalizeNote(payload.data);
    }
    return normalizeNote(payload);
  },

  deleteNote: async (candidateId: string, noteId: string | number): Promise<void> => {
    await requestJson<void>(TRACKER_BASE_URL, `/records/${candidateId}/notes/${noteId}`, {
      method: "DELETE",
    }, { authRequired: true });
  },
};