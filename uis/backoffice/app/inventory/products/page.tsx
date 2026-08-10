"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import { getProducts } from "../../../services/inventoryApi";
import {
  CATEGORY_LABELS,
  getStockLevel,
  STOCK_LEVEL_LABELS,
  type MedicalSupply,
  type StockLevel,
} from "../../../types/inventory";

// LED dot + bold number, not a pill: this list is scanned for what's low,
// not read as a status label — see memory-bank/techContext.md (redesign notes).
const STOCK_LEVEL_LED_CLASS: Record<StockLevel, string> = {
  low: "led-critical",
  medium: "led-warning",
  healthy: "led-ok",
};

const STOCK_LEVEL_COLOR: Record<StockLevel, string> = {
  low: "var(--critical)",
  medium: "var(--warning)",
  healthy: "var(--ok)",
};

export default function InventoryProductsPage() {
  const [products, setProducts] = useState<MedicalSupply[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);

  const loadProducts = useCallback(async () => {
    setIsLoading(true);
    setLoadError(null);
    try {
      const results = await getProducts();
      setProducts(results);
    } catch {
      setLoadError("No se pudo cargar la lista de productos. Verifica que la API esté activa e inténtalo de nuevo.");
    } finally {
      setIsLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadProducts();
  }, [loadProducts]);

  return (
    <main className="shell" style={{ padding: "24px 0 48px" }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", flexWrap: "wrap", gap: 10 }}>
        <h1 style={{ marginBottom: 0 }}>Material sanitario</h1>
        <Link href="/inventory/orders" className="nav-link" style={{ color: "var(--brand)" }}>
          Ver historial de órdenes →
        </Link>
      </div>

      {isLoading && <p style={{ color: "var(--muted)" }}>Cargando productos…</p>}

      {!isLoading && loadError && (
        <div className="panel" style={{ display: "flex", gap: 12, alignItems: "center", flexWrap: "wrap" }}>
          <span className="error-text">{loadError}</span>
          <button type="button" onClick={() => void loadProducts()}>
            Reintentar
          </button>
        </div>
      )}

      {!isLoading && !loadError && products.length === 0 && (
        <div className="panel">
          <p style={{ margin: 0, color: "var(--muted)" }}>Todavía no se ha registrado ningún material sanitario.</p>
        </div>
      )}

      {!isLoading && !loadError && products.length > 0 && (
        <div className="panel" style={{ overflowX: "auto", marginTop: 16 }}>
          <table className="table">
            <thead>
              <tr>
                <th>Nombre</th>
                <th>SKU</th>
                <th>Categoría</th>
                <th>Unidad</th>
                <th>País</th>
                <th>Stock actual</th>
                <th>Acciones</th>
              </tr>
            </thead>
            <tbody>
              {products.map((product) => {
                const level = getStockLevel(product.current_stock);
                return (
                  <tr key={product.id}>
                    <td>
                      <strong>{product.name}</strong>
                    </td>
                    <td className="mono" style={{ fontSize: 12, color: "var(--muted)" }}>{product.sku}</td>
                    <td>{CATEGORY_LABELS[product.category] ?? product.category}</td>
                    <td>{product.unit}</td>
                    <td>{product.country}</td>
                    <td>
                      <span className={`led ${STOCK_LEVEL_LED_CLASS[level]}`} />
                      <span className="mono" style={{ color: STOCK_LEVEL_COLOR[level], fontWeight: 700 }}>
                        {product.current_stock}
                      </span>
                      <span
                        style={{
                          display: "block",
                          marginLeft: 16,
                          fontSize: 10,
                          letterSpacing: "0.04em",
                          textTransform: "uppercase",
                          color: "var(--muted)",
                        }}
                      >
                        {STOCK_LEVEL_LABELS[level]}
                      </span>
                    </td>
                    <td style={{ display: "flex", gap: 10 }}>
                      <Link
                        href={`/inventory/orders/inbound?supply_id=${product.id}`}
                        className="nav-link"
                        style={{ color: "var(--brand)" }}
                      >
                        + Entrega
                      </Link>
                      <Link
                        href={`/inventory/orders/outbound?supply_id=${product.id}`}
                        className="nav-link"
                        style={{ color: "var(--brand)" }}
                      >
                        − Consumo
                      </Link>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </main>
  );
}
