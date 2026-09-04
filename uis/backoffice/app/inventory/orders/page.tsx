"use client";

import dynamic from "next/dynamic";
import { Fragment, useCallback, useEffect, useState } from "react";

import { getOrders } from "../../../services/inventoryApi";
import type { InventoryOrder, OrderType } from "../../../types/inventory";
import { CONSUMPTION_TYPE_LABELS } from "../../../types/inventory";

// Most people scanning this history just want type/product/quantity — raw
// IDs and full timestamps are only useful for a specific audit lookup, so
// the panel's chunk is deferred until someone actually asks for it.
const OrderDetailPanel = dynamic(
  () => import("../../../components/OrderDetailPanel").then((mod) => mod.OrderDetailPanel),
  { loading: () => <p style={{ margin: "8px 0", color: "var(--muted)" }}>Cargando detalle…</p> },
);

const ORDER_TYPE_BADGE_STYLES: Record<OrderType, React.CSSProperties> = {
  inbound: { background: "rgba(74, 222, 128, 0.14)", color: "var(--ok)" },
  outbound: { background: "rgba(255, 194, 75, 0.14)", color: "var(--warning)" },
};

const ORDER_TYPE_LABELS: Record<OrderType, string> = {
  inbound: "Entrada",
  outbound: "Salida",
};

export default function InventoryOrdersPage() {
  const [orders, setOrders] = useState<InventoryOrder[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [expandedKey, setExpandedKey] = useState<string | null>(null);

  const loadOrders = useCallback(async () => {
    setIsLoading(true);
    setLoadError(null);
    try {
      const results = await getOrders();
      setOrders(results);
    } catch {
      setLoadError("No se pudo cargar el historial de órdenes. Verifica que la API esté activa e inténtalo de nuevo.");
    } finally {
      setIsLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadOrders();
  }, [loadOrders]);

  return (
    <main className="shell" style={{ padding: "24px 0 48px" }}>
      <h1>Historial de órdenes de inventario</h1>
      <p style={{ color: "var(--muted)", maxWidth: 720 }}>
        Vista de solo lectura de todas las entregas y consumos registrados en todos los productos.
      </p>

      {isLoading && <p style={{ color: "var(--muted)" }}>Cargando órdenes…</p>}

      {!isLoading && loadError && (
        <div className="panel" style={{ display: "flex", gap: 12, alignItems: "center", flexWrap: "wrap" }}>
          <span className="error-text">{loadError}</span>
          <button type="button" onClick={() => void loadOrders()}>
            Reintentar
          </button>
        </div>
      )}

      {!isLoading && !loadError && orders.length === 0 && (
        <div className="panel">
          <p style={{ margin: 0, color: "var(--muted)" }}>Todavía no se ha registrado ninguna orden.</p>
        </div>
      )}

      {!isLoading && !loadError && orders.length > 0 && (
        <div className="panel" style={{ overflowX: "auto", marginTop: 16 }}>
          <table className="table">
            <thead>
              <tr>
                <th>Tipo</th>
                <th>Producto</th>
                <th>Cantidad</th>
                <th>Detalle</th>
                <th>Clínica</th>
                <th>Creada</th>
                <th>Creada por</th>
                <th>Auditoría</th>
              </tr>
            </thead>
            <tbody>
              {orders.map((order) => {
                const key = `${order.order_type}-${order.id}`;
                const isExpanded = expandedKey === key;
                return (
                  <Fragment key={key}>
                    <tr>
                      <td>
                        <span className="status-badge" style={ORDER_TYPE_BADGE_STYLES[order.order_type]}>
                          {ORDER_TYPE_LABELS[order.order_type]}
                        </span>
                      </td>
                      <td>
                        <strong>{order.supply_name}</strong>
                        <div className="mono" style={{ color: "var(--muted)", fontSize: 11 }}>{order.supply_sku}</div>
                      </td>
                      <td className="mono">{order.quantity}</td>
                      <td>
                        {order.order_type === "inbound"
                          ? order.vendor_name
                          : CONSUMPTION_TYPE_LABELS[order.consumption_type ?? ""] ?? order.consumption_type}
                      </td>
                      <td className="mono">{order.clinic_id}</td>
                      <td className="mono" style={{ fontSize: 12 }}>{new Date(order.created_at).toLocaleString("es-ES")}</td>
                      <td className="mono" style={{ fontSize: 11, color: "var(--muted)" }}>{order.user_uuid}</td>
                      <td>
                        <button type="button" onClick={() => setExpandedKey(isExpanded ? null : key)}>
                          {isExpanded ? "Ocultar" : "Ver detalle"}
                        </button>
                      </td>
                    </tr>
                    {isExpanded && (
                      <tr key={`${key}-detail`}>
                        <td colSpan={8} style={{ padding: 0, border: "none" }}>
                          <OrderDetailPanel order={order} />
                        </td>
                      </tr>
                    )}
                  </Fragment>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </main>
  );
}
