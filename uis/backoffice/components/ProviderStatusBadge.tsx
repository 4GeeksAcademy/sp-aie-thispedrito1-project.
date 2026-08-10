import { SUPPLIER_STATUS_LABELS, type SupplierStatus } from "../types/supplier";

export function ProviderStatusBadge({ status }: { status: SupplierStatus }) {
  return (
    <span className={`status-badge ${status === "active" ? "status-active" : "status-suspended"}`}>
      {SUPPLIER_STATUS_LABELS[status]}
    </span>
  );
}
