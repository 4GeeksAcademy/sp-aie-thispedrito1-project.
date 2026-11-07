import {
  FEDERATED_MESSAGES,
  callbackUrl,
  federatedErrorMessage,
  parsePendingFlow,
  readCallbackParams,
  serializePendingFlow,
  stateMatches,
} from "../types/federated";

describe("autenticación federada: utilidades del callback", () => {
  it("construye la URL de callback de la lista blanca", () => {
    expect(callbackUrl("http://localhost:3001")).toBe("http://localhost:3001/auth/callback/microsoft");
    expect(callbackUrl("http://localhost:3001/")).toBe("http://localhost:3001/auth/callback/microsoft");
  });

  it("lee code y state de la vuelta de Microsoft", () => {
    expect(readCallbackParams("?code=abc&state=xyz")).toEqual({ kind: "code", code: "abc", state: "xyz" });
  });

  it("distingue la cancelación del usuario de otros errores del proveedor", () => {
    expect(readCallbackParams("?error=access_denied&state=xyz")).toEqual({ kind: "error", code: "access_denied" });
    expect(readCallbackParams("?error=server_error")).toEqual({ kind: "error", code: "provider_error" });
  });

  it("marca como inválida una vuelta sin code o sin state", () => {
    expect(readCallbackParams("?code=abc")).toEqual({ kind: "invalid" });
    expect(readCallbackParams("")).toEqual({ kind: "invalid" });
  });

  it("solo acepta el state que guardó esta pestaña (anti login-CSRF)", () => {
    const pending = { state: "s-123", purpose: "login" as const, provider: "microsoft" };
    expect(stateMatches(pending, "s-123")).toBe(true);
    expect(stateMatches(pending, "s-999")).toBe(false);
    expect(stateMatches(null, "s-123")).toBe(false);
    expect(stateMatches({ ...pending, state: "" }, "")).toBe(false);
  });

  it("guarda y recupera el flujo pendiente, y rechaza datos manipulados", () => {
    const flow = { state: "s-1", purpose: "link" as const, provider: "microsoft" };
    expect(parsePendingFlow(serializePendingFlow(flow))).toEqual(flow);
    expect(parsePendingFlow(null)).toBeNull();
    expect(parsePendingFlow("no-json")).toBeNull();
    expect(parsePendingFlow(JSON.stringify({ ...flow, purpose: "admin" }))).toBeNull();
    expect(parsePendingFlow(JSON.stringify({ ...flow, state: "" }))).toBeNull();
  });

  it("traduce cada código de la API y cae a un mensaje genérico", () => {
    expect(federatedErrorMessage("not_linked")).toBe(FEDERATED_MESSAGES.not_linked);
    expect(federatedErrorMessage("last_access_method")).toMatch(/único método de acceso/);
    expect(federatedErrorMessage("desconocido")).toBe(FEDERATED_MESSAGES.provider_error);
  });

  it("tiene texto en español para todos los motivos que devuelve la API", () => {
    const apiReasons = [
      "not_linked", "account_inactive", "invalid_id_token", "invalid_state", "redirect_uri_not_allowed",
      "redirect_uri_mismatch", "provider_error", "login_required", "link_session_mismatch", "not_provisioned",
      "not_staff", "identity_in_use", "provider_already_linked", "not_linked_to_you", "last_access_method",
      "unsupported_provider", "provider_disabled",
    ];
    for (const reason of apiReasons) {
      expect(FEDERATED_MESSAGES[reason]).toBeTruthy();
    }
  });
});
