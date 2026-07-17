"use client";

import { useEffect, useMemo, useState } from "react";

import { createSupplier, deleteSupplier, getSuppliers, updateSupplierRate, updateSupplierStatus } from "../services/suppliersApi";
import type { Supplier, SupplierCreateInput, SupplierStatus } from "../types/supplier";
import { ProviderForm } from "./ProviderForm";
import { ProviderStatusBadge } from "./ProviderStatusBadge";

const categories = [
  "",
  "medical_supplies",
  "laboratory_services",
  "pharmaceutical",
  "clinical_software",
  "it_infrastructure",
  "hr_and_payroll_software",
  "cleaning_and_facilities",
  "patient_communication",
  "billing_and_coding_software",
  "training_platforms",
];

export function ProviderDirectory() {
  const [suppliers, setSuppliers] = useState<Supplier[]>([]);
  const [loadingList, setLoadingList] = useState(true);
  const [listError, setListError] = useState<string | null>(null);
  const [filters, setFilters] = useState<{ country?: string; category?: string }>({});
  const [savingById, setSavingById] = useState<Record<number, boolean>>({});
  const [errorById, setErrorById] = useState<Record<number, string | null>>({});
  const [editingRateById, setEditingRateById] = useState<Record<number, string>>({});

  const fetchList = async () => {
    setLoadingList(true);
    setListError(null);
    try {
      const data = await getSuppliers(filters);
      setSuppliers(data);
    } catch (error) {
      const message = error instanceof Error ? error.message : "No se pudo cargar el listado.";
      setListError(message);
    } finally {
      setLoadingList(false);
    }
  };

  useEffect(() => {
    void fetchList();
  }, [filters.country, filters.category]);

  const countries = useMemo(() => ["", "USA", "UK"], []);

  const onCreateSupplier = async (payload: SupplierCreateInput) => {
    const created = await createSupplier(payload);
    const inCountry = !filters.country || created.country === filters.country;
    const inCategory = !filters.category || created.categories.includes(filters.category);
    if (inCountry && inCategory) {
      setSuppliers((prev) => [created, ...prev]);
    }
    return created;
  };

  const saveRate = async (supplier: Supplier) => {
    const rawValue = editingRateById[supplier.id] ?? String(supplier.monthly_rate);
    const monthlyRate = Number(rawValue);
    if (!Number.isFinite(monthlyRate) || monthlyRate <= 0) {
      setErrorById((prev) => ({ ...prev, [supplier.id]: "La tarifa debe ser mayor a 0." }));
      return;
    }

    setSavingById((prev) => ({ ...prev, [supplier.id]: true }));
    setErrorById((prev) => ({ ...prev, [supplier.id]: null }));

    try {
      const updated = await updateSupplierRate(supplier.id, monthlyRate);
      setSuppliers((prev) => prev.map((item) => (item.id === supplier.id ? updated : item)));
      setEditingRateById((prev) => ({ ...prev, [supplier.id]: String(updated.monthly_rate) }));
    } catch (error) {
      const message = error instanceof Error ? error.message : "No se pudo actualizar la tarifa.";
      setErrorById((prev) => ({ ...prev, [supplier.id]: message }));
    } finally {
      setSavingById((prev) => ({ ...prev, [supplier.id]: false }));
    }
  };

  const saveStatus = async (supplier: Supplier, status: SupplierStatus) => {
    setSavingById((prev) => ({ ...prev, [supplier.id]: true }));
    setErrorById((prev) => ({ ...prev, [supplier.id]: null }));

    try {
      const updated = await updateSupplierStatus(supplier.id, status);
      setSuppliers((prev) => prev.map((item) => (item.id === supplier.id ? updated : item)));
    } catch (error) {
      const message = error instanceof Error ? error.message : "No se pudo actualizar el estado.";
      setErrorById((prev) => ({ ...prev, [supplier.id]: message }));
    } finally {
      setSavingById((prev) => ({ ...prev, [supplier.id]: false }));
    }
  };

  const removeSupplier = async (supplier: Supplier) => {
    setSavingById((prev) => ({ ...prev, [supplier.id]: true }));
    setErrorById((prev) => ({ ...prev, [supplier.id]: null }));

    try {
      await deleteSupplier(supplier.id);
      setSuppliers((prev) => prev.filter((item) => item.id !== supplier.id));
    } catch (error) {
      const message = error instanceof Error ? error.message : "No se pudo eliminar el proveedor.";
      setErrorById((prev) => ({ ...prev, [supplier.id]: message }));
    } finally {
      setSavingById((prev) => ({ ...prev, [supplier.id]: false }));
    }
  };

  return (
    <main className="shell" style={{ padding: "24px 0 42px" }}>
      <section className="panel" style={{ marginBottom: 16 }}>
        <h1 style={{ marginTop: 0, marginBottom: 8 }}>Directorio de proveedores</h1>
        <p style={{ margin: 0, color: "var(--muted)" }}>Gestiona proveedores de HealthCore por pais, categoria, tarifa y estado.</p>
      </section>

      <ProviderForm onSubmit={onCreateSupplier} />

      <section className="panel" style={{ marginBottom: 16 }}>
        <h2 style={{ marginTop: 0 }}>Filtros</h2>
        <div className="form-grid">
          <label>
            Pais
            <select
              value={filters.country ?? ""}
              onChange={(event) =>
                setFilters((prev) => ({
                  ...prev,
                  country: event.target.value || undefined,
                }))
              }
            >
              {countries.map((value) => (
                <option key={value || "all"} value={value}>
                  {value || "Todos"}
                </option>
              ))}
            </select>
          </label>
          <label>
            Categoria
            <select
              value={filters.category ?? ""}
              onChange={(event) =>
                setFilters((prev) => ({
                  ...prev,
                  category: event.target.value || undefined,
                }))
              }
            >
              {categories.map((value) => (
                <option key={value || "all"} value={value}>
                  {value || "Todas"}
                </option>
              ))}
            </select>
          </label>
        </div>
      </section>

      <section className="panel">
        <h2 style={{ marginTop: 0 }}>Listado</h2>

        {loadingList && <p>Cargando proveedores...</p>}
        {!loadingList && listError && <p className="error-text">{listError}</p>}
        {!loadingList && !listError && suppliers.length === 0 && <p>No hay proveedores para los filtros seleccionados.</p>}

        {!loadingList && !listError && suppliers.length > 0 && (
          <div style={{ overflowX: "auto" }}>
            <table className="table">
              <thead>
                <tr>
                  <th>Nombre</th>
                  <th>Pais</th>
                  <th>Categorias</th>
                  <th>Tarifa mensual</th>
                  <th>Moneda</th>
                  <th>Status</th>
                  <th>Updated at</th>
                  <th>Acciones</th>
                </tr>
              </thead>
              <tbody>
                {suppliers.map((supplier) => {
                  const rowSaving = Boolean(savingById[supplier.id]);
                  return (
                    <tr key={supplier.id}>
                      <td>{supplier.name}</td>
                      <td>{supplier.country}</td>
                      <td>{supplier.categories.join(", ")}</td>
                      <td>
                        <div style={{ display: "flex", gap: 8 }}>
                          <input
                            type="number"
                            min={1}
                            step="0.01"
                            value={editingRateById[supplier.id] ?? String(supplier.monthly_rate)}
                            onChange={(event) =>
                              setEditingRateById((prev) => ({
                                ...prev,
                                [supplier.id]: event.target.value,
                              }))
                            }
                            disabled={rowSaving}
                            style={{ width: 110 }}
                          />
                          <button type="button" onClick={() => void saveRate(supplier)} disabled={rowSaving}>
                            Guardar
                          </button>
                        </div>
                      </td>
                      <td>{supplier.currency}</td>
                      <td>
                        <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
                          <ProviderStatusBadge status={supplier.status} />
                          <select
                            value={supplier.status}
                            onChange={(event) => void saveStatus(supplier, event.target.value as SupplierStatus)}
                            disabled={rowSaving}
                          >
                            <option value="active">active</option>
                            <option value="suspended">suspended</option>
                          </select>
                        </div>
                      </td>
                      <td>{new Date(supplier.updated_at).toLocaleString()}</td>
                      <td>
                        <button type="button" onClick={() => void removeSupplier(supplier)} disabled={rowSaving}>
                          Eliminar
                        </button>
                        {errorById[supplier.id] && <p className="error-text">{errorById[supplier.id]}</p>}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </main>
  );
}
