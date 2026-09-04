"use client";

import { useEffect, useState } from "react";

import { createInboundOrder, getProducts } from "../../../../services/inventoryApi";
import { track } from "../../../../services/telemetry";
import { CLINIC_ID_MAX, CLINIC_ID_MIN, type InboundOrderInput, type MedicalSupply } from "../../../../types/inventory";

const EMPTY_FORM: InboundOrderInput = {
  supply_id: 0,
  quantity: 1,
  vendor_name: "",
  clinic_id: CLINIC_ID_MIN,
};

export default function InboundOrderPage() {
  const [products, setProducts] = useState<MedicalSupply[]>([]);
  const [productsError, setProductsError] = useState<string | null>(null);
  const [form, setForm] = useState<InboundOrderInput>(EMPTY_FORM);
  const [formError, setFormError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);

  // Read the optional ?supply_id= preselect via window.location instead of
  // next/navigation's useSearchParams, which would force this page out of
  // static rendering and require a <Suspense> boundary just for one prefill value.
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

  const setField = <K extends keyof InboundOrderInput>(key: K, value: InboundOrderInput[K]) => {
    setForm((prev) => ({ ...prev, [key]: value }));
  };

  const submit = async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setFormError(null);
    setSuccess(null);

    if (!form.supply_id) {
      setFormError("Selecciona un producto.");
      return;
    }
    if (!form.vendor_name.trim()) {
      setFormError("El nombre del proveedor es obligatorio.");
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

    setIsSubmitting(true);
    try {
      const trimmedForm = { ...form, vendor_name: form.vendor_name.trim() };
      const delivery = await createInboundOrder(trimmedForm);
      const product = products.find((item) => item.id === form.supply_id);
      if (product) {
        track("inbound_order_created", {
          clinic_id: trimmedForm.clinic_id,
          country: product.country,
          product_id: product.id,
          product_category: product.category,
          quantity: trimmedForm.quantity,
          vendor_name: trimmedForm.vendor_name,
          delivery_id: delivery.id,
        });
      }
      setSuccess("Entrega registrada correctamente. El stock del producto se ha actualizado.");
      setForm((prev) => ({ ...EMPTY_FORM, supply_id: prev.supply_id }));
    } catch (submitError) {
      setFormError(submitError instanceof Error ? submitError.message : "No se pudo registrar la entrega.");
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <main className="shell" style={{ padding: "24px 0 48px" }}>
      <h1>Registrar una entrega</h1>
      <p style={{ color: "var(--muted)", maxWidth: 720 }}>
        Registra un envío de material recibido de un proveedor. Esto incrementa el stock disponible del
        producto.
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

          <label>
            Cantidad *
            <input
              type="number"
              min={1}
              value={form.quantity}
              onChange={(event) => setField("quantity", Number(event.target.value))}
            />
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

          <label style={{ gridColumn: "1 / -1" }}>
            Nombre del proveedor *
            <input
              value={form.vendor_name}
              onChange={(event) => setField("vendor_name", event.target.value)}
              placeholder="p. ej. MedLine Industries"
            />
          </label>
        </div>

        <div style={{ marginTop: 14, display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap" }}>
          <button type="submit" disabled={isSubmitting}>
            {isSubmitting ? "Registrando…" : "Registrar entrega"}
          </button>
          {formError && <span className="error-text" role="alert">{formError}</span>}
          {success && <span className="success-text" role="status">{success}</span>}
        </div>
      </form>
    </main>
  );
}
