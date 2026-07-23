import type { Supplier, SupplierCreateInput, SupplierFilters, SupplierStatus } from "../types/supplier";
import { requestJson } from "./http";

export function getSuppliers(filters: SupplierFilters): Promise<Supplier[]> {
  const params = new URLSearchParams();
  if (filters.country) params.set("country", filters.country);
  if (filters.category) params.set("category", filters.category);

  const query = params.toString();
  return requestJson<Supplier[]>(`/suppliers${query ? `?${query}` : ""}`, undefined, { authRequired: true });
}

export function createSupplier(payload: SupplierCreateInput): Promise<Supplier> {
  return requestJson<Supplier>("/suppliers", {
    method: "POST",
    body: JSON.stringify(payload),
  }, { authRequired: true });
}

export function updateSupplierRate(id: number, monthlyRate: number): Promise<Supplier> {
  return requestJson<Supplier>(`/suppliers/${id}/rate`, {
    method: "PATCH",
    body: JSON.stringify({ monthly_rate: monthlyRate }),
  }, { authRequired: true });
}

export function updateSupplierStatus(id: number, status: SupplierStatus): Promise<Supplier> {
  return requestJson<Supplier>(`/suppliers/${id}/status`, {
    method: "PATCH",
    body: JSON.stringify({ status }),
  }, { authRequired: true });
}

export function deleteSupplier(id: number): Promise<void> {
  return requestJson<void>(`/suppliers/${id}`, {
    method: "DELETE",
  }, { authRequired: true });
}
