/**
 * Utilidades de sesión (services/session.ts): validación de JWT y
 * almacenamiento del token. La lógica que decide si una sesión sigue viva.
 */

import {
  AUTH_TOKEN_KEY,
  clearAuthToken,
  getAuthToken,
  hasValidSession,
  isTokenValid,
  setAuthToken,
} from "../services/session";

// Construye un JWT con la forma correcta (header.payload.firma) pero firma
// falsa: isTokenValid solo decodifica el payload, no verifica la firma.
const base64url = (value: object): string =>
  Buffer.from(JSON.stringify(value))
    .toString("base64")
    .replace(/\+/g, "-")
    .replace(/\//g, "_")
    .replace(/=+$/, "");

const makeToken = (payload: object): string =>
  `${base64url({ alg: "HS256", typ: "JWT" })}.${base64url(payload)}.firma-falsa`;

const nowInSeconds = () => Math.floor(Date.now() / 1000);

afterEach(() => {
  window.localStorage.clear();
});

describe("isTokenValid", () => {
  it("acepta un token con expiración futura (camino feliz)", () => {
    const token = makeToken({ sub: "user-1", exp: nowInSeconds() + 3600 });

    expect(isTokenValid(token)).toBe(true);
  });

  it("rechaza un token expirado (modo de fallo)", () => {
    const token = makeToken({ sub: "user-1", exp: nowInSeconds() - 60 });

    expect(isTokenValid(token)).toBe(false);
  });

  it("rechaza una cadena que no es un JWT (modo de fallo)", () => {
    expect(isTokenValid("esto-no-es-un-jwt")).toBe(false);
  });

  it("rechaza null (modo de fallo)", () => {
    expect(isTokenValid(null)).toBe(false);
  });

  it("acepta un token sin claim exp (caso límite documentado)", () => {
    // Comportamiento actual: sin exp se considera válido; queda registrado aquí.
    const token = makeToken({ sub: "user-1" });

    expect(isTokenValid(token)).toBe(true);
  });
});

describe("almacenamiento del token", () => {
  it("guarda y recupera el token (camino feliz)", () => {
    setAuthToken("token-de-prueba");

    expect(getAuthToken()).toBe("token-de-prueba");
    expect(window.localStorage.getItem(AUTH_TOKEN_KEY)).toBe("token-de-prueba");
  });

  it("clearAuthToken elimina la sesión (modo de fallo)", () => {
    setAuthToken(makeToken({ exp: nowInSeconds() + 3600 }));
    expect(hasValidSession()).toBe(true);

    clearAuthToken();

    expect(getAuthToken()).toBeNull();
    expect(hasValidSession()).toBe(false);
  });

  it("hasValidSession es false con un token expirado guardado (caso límite)", () => {
    setAuthToken(makeToken({ exp: nowInSeconds() - 60 }));

    expect(hasValidSession()).toBe(false);
  });
});
