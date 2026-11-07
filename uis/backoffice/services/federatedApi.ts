import {
  PENDING_FLOW_KEY,
  parsePendingFlow,
  serializePendingFlow,
  type FederatedAccountStatus,
  type FederatedCallbackResponse,
  type FederatedProvidersResponse,
  type FederatedPurpose,
  type FederatedStartResponse,
  type FederatedUnlinkResponse,
  type PendingFlow,
} from "../types/federated";
import { requestJson } from "./http";
import { getAuthToken } from "./session";

export const federatedApi = {
  providers(): Promise<FederatedProvidersResponse> {
    return requestJson<FederatedProvidersResponse>("/auth/federated/providers");
  },

  // Login: público. Vinculación: exige sesión (la API responde 401 sin ella).
  start(provider: string, purpose: FederatedPurpose, redirectUri: string): Promise<FederatedStartResponse> {
    return requestJson<FederatedStartResponse>(
      `/auth/federated/${provider}/${purpose}/start`,
      { method: "POST", body: JSON.stringify({ redirect_uri: redirectUri }) },
      { authRequired: purpose === "link" },
    );
  },

  // El token se manda si hay sesión (vinculación) pero sin authRequired: un
  // 401 aquí es un motivo que mostrar ("login_required"), no una sesión
  // caducada que redirija al login borrando el contexto.
  complete(provider: string, body: { code: string; state: string; redirect_uri: string }, withSession: boolean): Promise<FederatedCallbackResponse> {
    const headers: Record<string, string> = {};
    const token = withSession ? getAuthToken() : null;
    if (token) headers.Authorization = `Bearer ${token}`;
    return requestJson<FederatedCallbackResponse>(`/auth/federated/${provider}/callback`, {
      method: "POST",
      headers,
      body: JSON.stringify(body),
    });
  },

  status(): Promise<FederatedAccountStatus> {
    return requestJson<FederatedAccountStatus>("/auth/federated/links", undefined, { authRequired: true });
  },

  unlink(provider: string): Promise<FederatedUnlinkResponse> {
    return requestJson<FederatedUnlinkResponse>(`/auth/federated/${provider}`, { method: "DELETE" }, { authRequired: true });
  },
};

/** Guarda el flujo en curso SOLO en esta pestaña (sessionStorage). */
export function rememberPendingFlow(flow: PendingFlow): void {
  try {
    window.sessionStorage.setItem(PENDING_FLOW_KEY, serializePendingFlow(flow));
  } catch {
    // Almacenamiento bloqueado: el callback no encontrará el state y
    // rechazará el flujo, que es el comportamiento seguro.
  }
}

/** Lee y BORRA el flujo pendiente: un state solo sirve una vez. */
export function takePendingFlow(): PendingFlow | null {
  try {
    const raw = window.sessionStorage.getItem(PENDING_FLOW_KEY);
    window.sessionStorage.removeItem(PENDING_FLOW_KEY);
    return parsePendingFlow(raw);
  } catch {
    return null;
  }
}

/** Empieza un flujo: pide la URL a la API, guarda el state y sale a Microsoft. */
export async function beginFederatedFlow(provider: string, purpose: FederatedPurpose, redirectUri: string): Promise<void> {
  const started = await federatedApi.start(provider, purpose, redirectUri);
  rememberPendingFlow({ state: started.state, purpose, provider });
  window.location.assign(started.authorization_url);
}
