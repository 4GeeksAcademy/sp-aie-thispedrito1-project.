/**
 * FE-019 — Lógica de negocio compartida (src/utils) consumida por el
 * backoffice a través de lib/businessMetrics.ts.
 */

import type { Claim } from "../../../src/types/models";
import { calculateDenialRate } from "../../../src/utils/transformations";
import { validateClaim } from "../../../src/utils/validations";

const baseClaim: Claim = {
  claimId: "HC-100001",
  patientId: "PAT-000001",
  locationId: "US-TX-01",
  serviceType: "primary_care",
  payerName: "Aetna",
  payerId: "AET-01",
  submissionDate: "2025-01-10",
  claimAmount: 150,
  status: "submitted",
  resubmitted: false,
};

const KNOWN_LOCATIONS = ["US-TX-01", "UK-LON-01"];

describe("validateClaim", () => {
  it("acepta una factura correcta (camino feliz)", () => {
    const result = validateClaim(baseClaim, KNOWN_LOCATIONS);

    expect(result.valid).toBe(true);
    expect(result.errors).toHaveLength(0);
  });

  it("acumula un error por cada regla incumplida (modo de fallo)", () => {
    const badClaim: Claim = {
      ...baseClaim,
      claimId: "SIN-FORMATO",       // no empieza por HC-
      claimAmount: 0,               // monto no positivo
      locationId: "XX-XX-99",       // sede desconocida
      status: "denied",             // denegada sin motivo
      denialReason: undefined,
    };

    const result = validateClaim(badClaim, KNOWN_LOCATIONS);

    expect(result.valid).toBe(false);
    // Cuatro reglas rotas => cuatro errores, no solo el primero.
    expect(result.errors).toHaveLength(4);
  });

  it("una denegada CON motivo no genera ese error (caso límite)", () => {
    const deniedWithReason: Claim = {
      ...baseClaim,
      status: "denied",
      denialReason: "coding_error",
    };

    const result = validateClaim(deniedWithReason, KNOWN_LOCATIONS);

    expect(result.valid).toBe(true);
  });
});

describe("calculateDenialRate", () => {
  it("calcula el porcentaje de denegadas (camino feliz)", () => {
    const claims: Claim[] = [
      baseClaim,
      { ...baseClaim, claimId: "HC-100002" },
      { ...baseClaim, claimId: "HC-100003" },
      { ...baseClaim, claimId: "HC-100004", status: "denied", denialReason: "coding_error" },
    ];

    expect(calculateDenialRate(claims)).toBe(25);
  });

  it("lanza un error con una lista vacía (modo de fallo)", () => {
    // Comportamiento definido en el Hito 2: mejor fallar explícitamente que
    // devolver un 0% engañoso cuando no hay datos.
    expect(() => calculateDenialRate([])).toThrow("No claims provided");
  });
});
