export type SupplyCategory = "ppe" | "wound_care" | "diagnostics" | "medications" | "consumables";

export type SupplyCountry = "US" | "UK";

export type ConsumptionType = "clinical_use" | "expiry_waste";

export type OrderType = "inbound" | "outbound";

export type MedicalSupply = {
  id: number;
  name: string;
  sku: string;
  category: SupplyCategory;
  unit: string;
  country: SupplyCountry;
  current_stock: number;
  expiry_date: string | null;
};

export type InboundOrderInput = {
  supply_id: number;
  quantity: number;
  vendor_name: string;
  clinic_id: number;
};

export type OutboundOrderInput = {
  supply_id: number;
  quantity: number;
  consumption_type: ConsumptionType | "";
  clinic_id: number;
};

export type InventoryOrder = {
  order_type: OrderType;
  id: number;
  supply_id: number;
  supply_name: string;
  supply_sku: string;
  quantity: number;
  clinic_id: number;
  created_at: string;
  user_uuid: string;
  vendor_name: string | null;
  consumption_type: ConsumptionType | null;
};

type Option<T extends string> = { value: T; label: string };

export const CATEGORY_OPTIONS: Option<SupplyCategory>[] = [
  { value: "ppe", label: "EPI" },
  { value: "wound_care", label: "Cuidado de heridas" },
  { value: "diagnostics", label: "Diagnóstico" },
  { value: "medications", label: "Medicamentos" },
  { value: "consumables", label: "Consumibles" },
];

export const CONSUMPTION_TYPE_OPTIONS: Option<ConsumptionType>[] = [
  { value: "clinical_use", label: "Uso clínico" },
  { value: "expiry_waste", label: "Caducado / desecho" },
];

function toLabelMap<T extends string>(options: Option<T>[]): Record<string, string> {
  return Object.fromEntries(options.map((option) => [option.value, option.label]));
}

export const CATEGORY_LABELS = toLabelMap(CATEGORY_OPTIONS);
export const CONSUMPTION_TYPE_LABELS = toLabelMap(CONSUMPTION_TYPE_OPTIONS);

// CONTEXT-healthcore.es.md: 12 clinics (9 US, 3 UK), integer ids, no FK,
// and no clinic name catalogue is provided anywhere in the project — so
// clinic_id is entered as a plain number in this range rather than a named select.
export const CLINIC_ID_MIN = 1;
export const CLINIC_ID_MAX = 12;

export type StockLevel = "low" | "medium" | "healthy";

// Thresholds are a judgment call for this exercise (not specified by CONTEXT):
// <=20 units risks running out mid-shift before the next delivery; >100 is
// comfortably stocked. Adjust here if operations wants different cutoffs.
export const LOW_STOCK_THRESHOLD = 20;
export const HEALTHY_STOCK_THRESHOLD = 100;

export function getStockLevel(currentStock: number): StockLevel {
  if (currentStock <= LOW_STOCK_THRESHOLD) return "low";
  if (currentStock <= HEALTHY_STOCK_THRESHOLD) return "medium";
  return "healthy";
}

// Pipeline de desempeño de negocio (data/pipelines/PIPELINE_DESIGN.md): el
// coste se reporta en la moneda del país, sin convertir, y nunca se suman
// USD y GBP entre sí.
export type SupplyCurrency = "USD" | "GBP";

export const CURRENCY_BY_COUNTRY: Record<SupplyCountry, SupplyCurrency> = {
  US: "USD",
  UK: "GBP",
};

export type UnitCostParse = { ok: true; value: number | null } | { ok: false; error: string };

/**
 * Lee el "coste unitario" del formulario de entregas. Vacío = coste
 * desconocido (`null`), NUNCA cero: el pipeline trata un evento sin
 * `unit_cost` como gasto no registrado, y un cero inventado lo haría pasar
 * por una entrega gratuita. Se redondea a 2 decimales, como la columna
 * `total_supply_cost` que alimenta.
 */
export function parseUnitCost(raw: string): UnitCostParse {
  const trimmed = raw.trim().replace(",", ".");
  if (trimmed === "") return { ok: true, value: null };
  const value = Number(trimmed);
  if (!Number.isFinite(value) || value < 0) {
    return { ok: false, error: "El coste unitario debe ser un número mayor o igual que cero." };
  }
  return { ok: true, value: Math.round(value * 100) / 100 };
}

export const STOCK_LEVEL_LABELS: Record<StockLevel, string> = {
  low: "Stock bajo",
  medium: "Stock moderado",
  healthy: "Stock saludable",
};
