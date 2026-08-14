"use client";

import { useEffect, useMemo, useState } from "react";

import { ApiFieldError } from "../../../../services/http";
import { createOutboundOrder, getProducts } from "../../../../services/inventoryApi";
import { track } from "../../../../services/telemetry";
import {
  CLINIC_ID_MAX,
  CLINIC_ID_MIN,
  CONSUMPTION_TYPE_OPTIONS,
  type MedicalSupply,
  type OutboundOrderInput,
} from "../../../../types/inventory";

const EMPTY_FORM: OutboundOrderInput = {
  supply_id: 0,
  quantity: 1,
  consumption_type: "",
  clinic_id: CLINIC_ID_MIN,
};

export default function OutboundOrderPage() {
  const [products, setProducts] = useState<MedicalSupply[]>([]);
  const [productsError, setProductsError] = useState<string | null>(null);
  const [form, setForm] = useState<OutboundOrderInput>(EMPTY_FORM);
  const [quantityError, setQuantityError] = useState<string | null>(null);
  const [formError, setFormError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);

  useEffect(() => {
    getProducts()
      .then((results) => {
        setProducts(results);
        const params = new URLSearchParams(window.location.search);
        const preselected = Number(params.get("supply_id"));
        if (preselected && results.some((product) => product.id === preselected)) {
          setForm((prev) => ({ ...prev, supply_id: preselected }));
        } else if (results.length > 0) {
          setForm((prev) => ({ ...prev, supply_id: results[0].id }));
        }
      })
      .catch(() => setProductsError("No se pudo cargar la lista de productos. Verifica que la API esté activa."));
  }, []);

  const selectedProduct = useMemo(
    () => products.find((product) => product.id === form.supply_id) ?? null,
    [products, form.supply_id],
  );

  const setField = <K extends keyof OutboundOrderInput>(key: K, value: OutboundOrderInput[K]) => {
    setForm((prev) => ({ ...prev, [key]: value }));
    setQuantityError(null);
  };

  const setQuantity = (quantity: number) => {
    setField("quantity", quantity);
    if (selectedProduct && quantity > selectedProduct.current_stock) {
      setQuantityError(
        `Solo hay ${selectedProduct.current_stock} ${selectedProduct.unit}(s) disponibles — esto supera el stock actual.`,
      );
    }
  };

  const submit = async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setFormError(null);
    setSuccess(null);

    if (!form.supply_id) {
      setFormError("Selecciona un producto.");
      return;
    }
    if (!form.consumption_type) {
      setFormError("Selecciona un tipo de consumo.");
      return;
    }
    if (form.quantity <= 0) {
      setFormError("La cantidad debe ser mayor que cero.");
      return;
    }
    if (form.clinic_id < CLINIC_ID_MIN || form.clinic_id > CLINIC_ID_MAX) {
      setFormError(`El id de clínica debe estar entre ${CLINIC_ID_MIN} y ${CLINIC_ID_MAX}.`);
      return;
    }
    if (selectedProduct && form.quantity > selectedProduct.current_stock) {
      setQuantityError(
        `Solo hay ${selectedProduct.current_stock} ${selectedProduct.unit}(s) disponibles — esto supera el stock actual.`,
      );
      return;
    }

    setIsSubmitting(true);
    try {
      const consumption = await createOutboundOrder(form);
      if (selectedProduct) {
        track("outbound_order_created", {
          clinic_id: form.clinic_id,
          country: selectedProduct.country,
          product_id: selectedProduct.id,
          product_category: selectedProduct.category,
          quantity: form.quantity,
          department: null,
          consumption_type: form.consumption_type,
          consumption_id: consumption.id,
        });
      }
      setSuccess("Consumo registrado correctamente. El stock del producto se ha actualizado.");
      setForm((prev) => ({ ...EMPTY_FORM, supply_id: prev.supply_id }));
      const refreshed = await getProducts();
      setProducts(refreshed);
    } catch (submitError) {
      if (submitError instanceof Error) {
        // El mensaje 400 de la API (stock insuficiente) llega en inglés tal
        // cual lo define el CONTEXT del backend — se muestra sin traducir,
        // junto al campo de cantidad que el usuario debe corregir.
        if (submitError.message.toLowerCase().includes("stock")) {
          setQuantityError(submitError.message);
        } else if (submitError instanceof ApiFieldError) {
          setFormError(submitError.fieldErrors.map((e) => e.message).join(" "));
        } else {
          setFormError(submitError.message);
        }
      } else {
        setFormError("No se pudo registrar el consumo.");
      }
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <main className="shell" style={{ padding: "24px 0 48px" }}>
      <h1>Registrar un consumo</h1>
      <p style={{ color: "var(--muted)", maxWidth: 720 }}>
        Registra material usado en la atención al paciente o desechado por caducidad. Esto reduce el
        stock disponible del producto.
      </p>

      {productsError && <p className="error-text">{productsError}</p>}

      <form onSubmit={submit} className="panel" style={{ maxWidth: 560 }}>
        <div className="form-grid">
          <label style={{ gridColumn: "1 / -1" }}>
            Producto *
            <select
              value={form.supply_id || ""}
              onChange={(event) => setField("supply_id", Number(event.target.value))}
            >
              <option value="">Selecciona un producto…</option>
              {products.map((product) => (
                <option key={product.id} value={product.id}>
                  {product.name} ({product.sku})
                </option>
              ))}
            </select>
          </label>

          {selectedProduct && (
            <p style={{ gridColumn: "1 / -1", margin: 0, fontSize: 13, color: "var(--muted)" }}>
              Stock actual disponible: <strong>{selectedProduct.current_stock} {selectedProduct.unit}(s)</strong>
            </p>
          )}

          <label>
            Cantidad *
            <input
              type="number"
              min={1}
              value={form.quantity}
              onChange={(event) => setQuantity(Number(event.target.value))}
            />
            {quantityError && <span className="error-text" role="alert">{quantityError}</span>}
          </label>

          <label>
            Tipo de consumo *
            <select
              value={form.consumption_type}
              onChange={(event) =>
                setField("consumption_type", event.target.value as OutboundOrderInput["consumption_type"])
              }
            >
              <option value="">Selecciona un tipo…</option>
              {CONSUMPTION_TYPE_OPTIONS.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </select>
          </label>

          <label>
            Id de clínica *
            <input
              type="number"
              min={CLINIC_ID_MIN}
              max={CLINIC_ID_MAX}
              value={form.clinic_id}
              onChange={(event) => setField("clinic_id", Number(event.target.value))}
            />
          </label>
        </div>

        <div style={{ marginTop: 14, display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap" }}>
          <button type="submit" disabled={isSubmitting || Boolean(quantityError)}>
            {isSubmitting ? "Registrando…" : "Registrar consumo"}
          </button>
          {formError && <span className="error-text" role="alert">{formError}</span>}
          {success && <span className="success-text" role="status">{success}</span>}
        </div>
      </form>
    </main>
  );
}
