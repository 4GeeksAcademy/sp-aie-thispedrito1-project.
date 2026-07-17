import type { Supplier, SupplierCreateInput, SupplierFilters, SupplierStatus } from "../types/supplier";

const BASE_URL = process.env.NEXT_PUBLIC_API_URL || "/api-proxy";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${BASE_URL}${path}`, {
    cache: "no-store",
    headers: {
      "Content-Type": "application/json",
      ...(init?.headers || {}),
    },
    ...init,
  });

  if (!response.ok) {
    let message = "Request failed";
    try {
      const data = await response.json();
      message = data.detail || message;
    } catch {
      // Keep fallback message.
    }
    throw new Error(message);
  }

  if (response.status === 204) {
    return undefined as T;
  }

  return (await response.json()) as T;
}

export function getSuppliers(filters: SupplierFilters): Promise<Supplier[]> {
  const params = new URLSearchParams();
  if (filters.country) params.set("country", filters.country);
  if (filters.category) params.set("category", filters.category);

  const query = params.toString();
  return request<Supplier[]>(`/suppliers${query ? `?${query}` : ""}`);
}

export function createSupplier(payload: SupplierCreateInput): Promise<Supplier> {
  return request<Supplier>("/suppliers", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function updateSupplierRate(id: number, monthlyRate: number): Promise<Supplier> {
  return request<Supplier>(`/suppliers/${id}/rate`, {
    method: "PATCH",
    body: JSON.stringify({ monthly_rate: monthlyRate }),
  });
}

export function updateSupplierStatus(id: number, status: SupplierStatus): Promise<Supplier> {
  return request<Supplier>(`/suppliers/${id}/status`, {
    method: "PATCH",
    body: JSON.stringify({ status }),
  });
}

export function deleteSupplier(id: number): Promise<void> {
  return request<void>(`/suppliers/${id}`, {
    method: "DELETE",
  });
}
