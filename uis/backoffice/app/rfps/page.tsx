"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useState, type FormEvent } from "react";

import { AsyncSection } from "../../components/AsyncSection";
import { useAsyncData } from "../../hooks/useAsyncData";
import { usePolling } from "../../hooks/usePolling";
import { listRfpTickets, uploadRfp } from "../../services/rfpApi";
import {
  POLL_INTERVAL_MS,
  STATUS_CLASS,
  STATUS_LABELS,
  shouldPoll,
  validatePdfFile,
  type RfpTicketListItem,
} from "../../types/rfp";

function formatDate(iso: string): string {
  return new Date(iso).toLocaleString("es-ES", { dateStyle: "short", timeStyle: "short" });
}

export default function RfpTicketsPage() {
  const router = useRouter();
  const [file, setFile] = useState<File | null>(null);
  const [formError, setFormError] = useState<string | null>(null);
  const [isUploading, setIsUploading] = useState(false);

  const fetchTickets = useCallback(() => listRfpTickets(), []);
  const { data, isLoading, error, reload } = useAsyncData(fetchTickets, "No se pudieron cargar las RFPs.");
  const [live, setLive] = useState<RfpTicketListItem[] | null>(null);
  const tickets = live ?? data ?? [];

  const refresh = useCallback(async () => {
    try {
      setLive(await listRfpTickets());
    } catch {
      // Refresco silencioso: si falla, se mantiene la última lista buena.
    }
  }, []);
  usePolling(refresh, tickets.some((ticket) => shouldPoll(ticket.status)), POLL_INTERVAL_MS);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const validation = validatePdfFile(file);
    if (validation || !file) {
      setFormError(validation);
      return;
    }
    setFormError(null);
    setIsUploading(true);
    try {
      const ticket = await uploadRfp(file);
      router.push(`/rfps/${ticket.ticket_id}`);
    } catch (uploadError) {
      setFormError(uploadError instanceof Error ? uploadError.message : "No se pudo subir la RFP.");
    } finally {
      setIsUploading(false);
    }
  }

  return (
    <main className="shell" style={{ padding: "24px 0 48px" }}>
      <h1 style={{ marginBottom: 4 }}>RFPs</h1>
      <p style={{ color: "var(--muted)", marginTop: 0 }}>
        Sube la RFP del cliente en PDF. Se crea un ticket y el análisis por departamento (Revenue Cycle, Clinical
        Operations y Compliance) corre en segundo plano.
      </p>

      <form className="panel" onSubmit={handleSubmit} noValidate style={{ margin: "16px 0" }}>
        <label htmlFor="rfp-file" style={{ display: "block", fontWeight: 600, marginBottom: 8 }}>
          RFP en PDF (máximo 10 MB)
        </label>
        <div style={{ display: "flex", gap: 12, flexWrap: "wrap", alignItems: "center" }}>
          <input
            id="rfp-file"
            type="file"
            accept="application/pdf,.pdf"
            onChange={(event) => {
              setFile(event.target.files?.[0] ?? null);
              setFormError(null);
            }}
          />
          <button type="submit" disabled={isUploading}>
            {isUploading ? "Subiendo…" : "Crear ticket"}
          </button>
        </div>
        <p style={{ color: "var(--muted)", fontSize: 13, marginBottom: 0 }}>
          No subas documentos con datos de pacientes. Si la RFP los contiene, el flujo se detiene y la marca para
          revisión de Compliance.
        </p>
        {formError && (
          <p className="error-text" role="alert" style={{ marginBottom: 0 }}>
            {formError}
          </p>
        )}
      </form>

      <AsyncSection
        isLoading={isLoading}
        error={error}
        onRetry={() => {
          setLive(null);
          reload();
        }}
        loadingLabel="Cargando RFPs…"
        isEmpty={tickets.length === 0}
        emptyLabel="Todavía no se ha subido ninguna RFP."
      >
        <div className="panel" style={{ overflowX: "auto" }}>
          <table className="table">
            <thead>
              <tr>
                <th>RFP</th>
                <th>Cliente</th>
                <th>País</th>
                <th>Estado</th>
                <th>Creada</th>
              </tr>
            </thead>
            <tbody>
              {tickets.map((ticket) => (
                <tr key={ticket.ticket_id}>
                  <td className="mono">
                    <Link href={`/rfps/${ticket.ticket_id}`} className="nav-link" style={{ color: "var(--brand)" }}>
                      {ticket.rfp_id}
                    </Link>
                  </td>
                  <td>{ticket.client_name ?? "—"}</td>
                  <td>{ticket.client_country ?? "—"}</td>
                  <td>
                    <span className={`status-badge ${STATUS_CLASS[ticket.status]}`}>{STATUS_LABELS[ticket.status]}</span>
                    {ticket.requires_compliance_review && (
                      <span className="status-badge rfp-status-error" style={{ marginLeft: 6 }}>
                        Revisión Compliance
                      </span>
                    )}
                  </td>
                  <td className="mono">{formatDate(ticket.created_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </AsyncSection>
    </main>
  );
}
