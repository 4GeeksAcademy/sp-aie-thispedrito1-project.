// Autenticación federada con Microsoft (BRIEFING de Autenticación Federada).
// Contratos de /auth/federated y utilidades puras del flujo en el navegador.

export type FederatedPurpose = "login" | "link";

export type FederatedProvider = {
  provider: string;
  enabled: boolean;
  mode: string | null;
};

export type FederatedProvidersResponse = { providers: FederatedProvider[] };

export type FederatedStartResponse = {
  authorization_url: string;
  state: string;
  expires_in: number;
};

export type FederatedLink = {
  provider: string;
  provider_user_id: string;
  email_at_link_time: string | null;
  linked_at: string;
};

export type FederatedCallbackResponse = {
  purpose: FederatedPurpose;
  message: string;
  access_token: string | null;
  token_type: string | null;
  link: FederatedLink | null;
};

export type FederatedAccountStatus = {
  links: FederatedLink[];
  has_password: boolean;
  unlink_warning: string | null;
};

export type FederatedUnlinkResponse = {
  message: string;
  provider: string;
  account: FederatedAccountStatus;
};

export const MICROSOFT_PROVIDER = "microsoft";
export const MICROSOFT_CALLBACK_PATH = "/auth/callback/microsoft";
export const PENDING_FLOW_KEY = "healthcore.federated.pending";

export const PROVIDER_LABELS: Record<string, string> = { microsoft: "Microsoft" };

// La API responde { detail: { code, message } }; aquí el texto que ve el
// usuario. http.ts lo usa para cualquier estado HTTP.
export const FEDERATED_MESSAGES: Record<string, string> = {
  not_linked:
    "Esta cuenta de Microsoft no está vinculada a ningún usuario de HealthCore. Entra con tu contraseña y vincúlala desde tu perfil.",
  account_inactive: "Tu cuenta de HealthCore está desactivada. Contacta con IT.",
  invalid_id_token: "Microsoft no devolvió una identidad válida del tenant de HealthCore. Usa tu cuenta corporativa.",
  invalid_state: "La solicitud de inicio de sesión no es válida, ha caducado o ya se usó. Vuelve a intentarlo.",
  redirect_uri_not_allowed: "La dirección de retorno no está autorizada.",
  redirect_uri_mismatch: "La dirección de retorno no coincide con la solicitud original.",
  provider_error: "Microsoft no pudo completar el inicio de sesión. Inténtalo de nuevo.",
  login_required: "Para vincular Microsoft necesitas haber iniciado sesión con tu contraseña.",
  link_session_mismatch: "Esta vinculación la empezó otra sesión. Vuelve a empezarla desde tu perfil.",
  not_provisioned: "IT todavía no ha habilitado el inicio de sesión con Microsoft para tu cuenta.",
  not_staff: "El inicio de sesión con Microsoft es solo para el personal de HealthCore.",
  identity_in_use: "Esa cuenta de Microsoft ya está vinculada a otro usuario de HealthCore.",
  provider_already_linked: "Tu cuenta ya tiene otra cuenta de Microsoft vinculada. Desvincúlala primero.",
  not_linked_to_you: "Tu cuenta no tiene ninguna cuenta de Microsoft vinculada.",
  last_access_method:
    "Microsoft es tu único método de acceso. Crea una contraseña (con «¿Olvidaste tu contraseña?») antes de desvincularlo.",
  unsupported_provider: "Proveedor de identidad no admitido.",
  provider_disabled: "El inicio de sesión con Microsoft no está configurado.",
  access_denied: "Cancelaste el inicio de sesión en Microsoft.",
};

export type PendingFlow = {
  state: string;
  purpose: FederatedPurpose;
  provider: string;
};

export function callbackUrl(origin: string): string {
  return `${origin.replace(/\/$/, "")}${MICROSOFT_CALLBACK_PATH}`;
}

export function serializePendingFlow(flow: PendingFlow): string {
  return JSON.stringify(flow);
}

export function parsePendingFlow(raw: string | null): PendingFlow | null {
  if (!raw) return null;
  try {
    const value: unknown = JSON.parse(raw);
    if (typeof value !== "object" || value === null) return null;
    const { state, purpose, provider } = value as Record<string, unknown>;
    if (typeof state !== "string" || !state) return null;
    if (purpose !== "login" && purpose !== "link") return null;
    if (typeof provider !== "string") return null;
    return { state, purpose, provider };
  } catch {
    return null;
  }
}

export type CallbackParams =
  | { kind: "code"; code: string; state: string }
  | { kind: "error"; code: string }
  | { kind: "invalid" };

/** Lee lo que Microsoft añade a la URL de vuelta (?code&state o ?error). */
export function readCallbackParams(search: string): CallbackParams {
  const params = new URLSearchParams(search);
  const error = params.get("error");
  if (error) return { kind: "error", code: error === "access_denied" ? "access_denied" : "provider_error" };
  const code = params.get("code");
  const state = params.get("state");
  if (!code || !state) return { kind: "invalid" };
  return { kind: "code", code, state };
}

/**
 * El callback solo continúa si el `state` que vuelve de Microsoft es el que
 * ESTA pestaña guardó al empezar. Sin esta comprobación, un atacante podría
 * hacer que la víctima abra su URL de vuelta y entrar con la cuenta del
 * atacante (login CSRF). Comparación exacta; nada vacío coincide.
 */
export function stateMatches(pending: PendingFlow | null, received: string): boolean {
  return pending !== null && pending.state.length > 0 && pending.state === received;
}

export function federatedErrorMessage(code: string): string {
  return FEDERATED_MESSAGES[code] ?? FEDERATED_MESSAGES.provider_error;
}

export function providerLabel(provider: string): string {
  return PROVIDER_LABELS[provider] ?? provider;
}
