/**
 * Hito 5 (backoffice) — umbrales de nivel de stock consumidos por
 * app/inventory/products/page.tsx.
 */

import {
  CURRENCY_BY_COUNTRY,
  getStockLevel,
  parseUnitCost,
  HEALTHY_STOCK_THRESHOLD,
  LOW_STOCK_THRESHOLD,
} from "../types/inventory";

describe("getStockLevel", () => {
  it("clasifica stock en cero como bajo (camino feliz)", () => {
    expect(getStockLevel(0)).toBe("low");
  });

  it("clasifica stock justo en el umbral bajo como bajo (límite)", () => {
    expect(getStockLevel(LOW_STOCK_THRESHOLD)).toBe("low");
  });

  it("clasifica stock justo por encima del umbral bajo como moderado (límite)", () => {
    expect(getStockLevel(LOW_STOCK_THRESHOLD + 1)).toBe("medium");
  });

  it("clasifica stock justo en el umbral saludable como moderado (límite)", () => {
    expect(getStockLevel(HEALTHY_STOCK_THRESHOLD)).toBe("medium");
  });

  it("clasifica stock justo por encima del umbral saludable como saludable (límite)", () => {
    expect(getStockLevel(HEALTHY_STOCK_THRESHOLD + 1)).toBe("healthy");
  });

  it("clasifica stock negativo como bajo (caso de fallo/dato inesperado)", () => {
    expect(getStockLevel(-5)).toBe("low");
  });
});

describe("parseUnitCost (coste de inbound_order_created para el pipeline de negocio)", () => {
  it("acepta un decimal con punto y lo redondea a 2 decimales (camino feliz)", () => {
    expect(parseUnitCost("0.426")).toEqual({ ok: true, value: 0.43 });
  });

  it("acepta coma decimal, como se escribe en español", () => {
    expect(parseUnitCost("12,5")).toEqual({ ok: true, value: 12.5 });
  });

  it("devuelve null (coste desconocido), no 0, si se deja vacío (límite)", () => {
    expect(parseUnitCost("   ")).toEqual({ ok: true, value: null });
  });

  it("acepta cero explícito como coste real (límite)", () => {
    expect(parseUnitCost("0")).toEqual({ ok: true, value: 0 });
  });

  it("rechaza costes negativos (fallo)", () => {
    expect(parseUnitCost("-3")).toMatchObject({ ok: false });
  });

  it("rechaza texto no numérico (fallo)", () => {
    expect(parseUnitCost("gratis")).toMatchObject({ ok: false });
  });
});

describe("CURRENCY_BY_COUNTRY", () => {
  it("asigna USD a US y GBP a UK, sin conversión", () => {
    expect(CURRENCY_BY_COUNTRY).toEqual({ US: "USD", UK: "GBP" });
  });
});
