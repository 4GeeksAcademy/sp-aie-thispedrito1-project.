/**
 * Hito 5 (backoffice) — umbrales de nivel de stock consumidos por
 * app/inventory/products/page.tsx.
 */

import {
  getStockLevel,
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
