"use client";

import type { InventoryOrder } from "../types/inventory";

type OrderDetailPanelProps = {
  order: InventoryOrder;
};

// Raw identifiers and the full ISO timestamp — needed for audit/traceability
// (HealthCore's HIPAA/UK GDPR obligations), not for everyday browsing. That's
// why this stays out of the main table and only mounts on demand.
export function OrderDetailPanel({ order }: OrderDetailPanelProps) {
  return (
    <div
      className="panel"
      style={{ margin: "8px 0", fontSize: 13, display: "grid", gap: 6, gridTemplateColumns: "repeat(auto-fit, minmax(200px, 1fr))" }}
    >
      <div>
        <span style={{ display: "block", color: "var(--muted)" }}>ID de orden</span>
        <span className="mono">{order.id}</span>
      </div>
      <div>
        <span style={{ display: "block", color: "var(--muted)" }}>ID de producto</span>
        <span className="mono">{order.supply_id}</span>
      </div>
      <div>
        <span style={{ display: "block", color: "var(--muted)" }}>Marca de tiempo completa (ISO)</span>
        <span className="mono">{order.created_at}</span>
      </div>
      <div>
        <span style={{ display: "block", color: "var(--muted)" }}>Tipo (valor interno)</span>
        <span className="mono">{order.order_type}</span>
      </div>
    </div>
  );
}
