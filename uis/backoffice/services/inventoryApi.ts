import type { InboundOrderInput, InventoryOrder, MedicalSupply, OutboundOrderInput } from "../types/inventory";
import { requestJson } from "./http";

export function getProducts(): Promise<MedicalSupply[]> {
  return requestJson<MedicalSupply[]>("/inventory/products", undefined, { authRequired: true });
}

export function getProduct(id: number): Promise<MedicalSupply> {
  return requestJson<MedicalSupply>(`/inventory/products/${id}`, undefined, { authRequired: true });
}

export function createInboundOrder(payload: InboundOrderInput): Promise<{ id: number }> {
  return requestJson<{ id: number }>("/inventory/orders/inbound", {
    method: "POST",
    body: JSON.stringify(payload),
  }, { authRequired: true });
}

export function createOutboundOrder(payload: OutboundOrderInput): Promise<{ id: number }> {
  return requestJson<{ id: number }>("/inventory/orders/outbound", {
    method: "POST",
    body: JSON.stringify(payload),
  }, { authRequired: true });
}

export function getOrders(): Promise<InventoryOrder[]> {
  return requestJson<InventoryOrder[]>("/inventory/orders", undefined, { authRequired: true });
}
