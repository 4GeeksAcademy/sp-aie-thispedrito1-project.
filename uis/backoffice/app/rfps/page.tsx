"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useMemo, useRef, useState, type FormEvent } from "react";

import { AsyncSection } from "../../components/AsyncSection";
import { useAsyncData } from "../../hooks/useAsyncData";
import { usePolling } from "../../hooks/usePolling";
import { useRfpEvents, type RfpStreamState } from "../../hooks/useRfpEvents";
import { listRfpTickets, uploadRfp } from "../../services/rfpApi";
import {
  POLL_INTERVAL_MS,
  STATUS_CLASS,
  STATUS_LABELS,
  shouldPoll,
  validatePdfFile,
  type RfpTicketListItem,
} from "../../types/rfp";
import {
  arrivalFromListItem,
  findMissedTickets,
  mergeArrivals,
  type RfpArrival,
  type RfpTicketCreatedEvent,
} from "../../types/rfpEvents";

function formatDate(iso: string): string {
  return new Date(iso).toLocaleString("es-ES", { dateStyle: "short", timeStyle: "short" });
}

function LiveIndicator({ status, retryInMs }: RfpStreamState) {
  const label =
    status === "live"
      ? "En directo"
      : status === "reconnecting"
        ? `Sin conexión · reintento en ${Math.max(1, Math.round((retryInMs ?? 0) / 1000))} s`
        : "Conectando…";
  return (
    <span className={`rfp-live rfp-live-${status}`} role="status">
      {label}
    </span>
  );
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

  // Tiempo real (docs/sse-notifications.md). Una entrada por ticket_id: el
  // mismo ticket nunca se avisa dos veces, venga en directo o recuperado.
  const [arrivals, setArrivals] = useState<Map<string, RfpArrival>>(() => new Map());
  const [dismissed, setDismissed] = useState<Set<string>>(() => new Set());
  // Lo que la persona ya ha visto: la lista al cargar la pantalla + los
  // avisos. El polling de estados no lo amplía a propósito: un ticket que
  // trajo el polling con el stream caído sigue mereciendo aviso.
  const knownIds = useRef<Set<string> | null>(null);
  useEffect(() => {
    if (data && knownIds.current === null) knownIds.current = new Set(data.map((t) => t.ticket_id));
  }, [data]);

  const addArrivals = useCallback((items: RfpArrival[]) => {
    setArrivals((previous) => {
      const fresh = items.filter((item) => !previous.has(item.ticket.ticket_id));
      if (fresh.length === 0) return previous;
      const next = new Map(previous);
      fresh.forEach((item) => {
        next.set(item.ticket.ticket_id, item);
        knownIds.current?.add(item.ticket.ticket_id);
      });
      return next;
    });
  }, []);

  const handleTicketCreated = useCallback(
    (ticket: RfpTicketCreatedEvent) => addArrivals([{ ticket, source: "live" }]),
    [addArrivals],
  );

  // Estrategia de recuperación: refetch de la lista y SSE para lo posterior.
  // Cada lista nueva se compara con lo conocido: al (re)conectar (el
  // servidor ya está suscrito cuando llega onOpen, así que no queda hueco) y
  // en cada refresco del polling de estados. Lo segundo cubre una RFP
  // clasificada durante el corte que aún no tenía cliente al reconectar: su
  // evento ya pasó, y avisa en cuanto la lista la muestra como RFP válida.
  const syncList = useCallback(
    (fetched: RfpTicketListItem[]) => {
      setLive(fetched);
      const known = knownIds.current;
      if (known === null) return; // la carga inicial aún no terminó: ella es la referencia
      addArrivals(
        findMissedTickets(known, fetched).map((item) => ({ ticket: arrivalFromListItem(item), source: "recovered" as const })),
      );
    },
    [addArrivals],
  );

  const refresh = useCallback(async () => {
    try {
      syncList(await listRfpTickets());
    } catch {
      // Refresco silencioso: si falla, se mantiene la última lista buena.
    }
  }, [syncList]);

  const stream = useRfpEvents({ onOpen: refresh, onTicketCreated: handleTicketCreated });
  const rows = useMemo(() => mergeArrivals(tickets, arrivals), [tickets, arrivals]);
  const unread = useMemo(
    () => Array.from(arrivals.values()).filter((a) => !dismissed.has(a.ticket.ticket_id)).reverse(),
    [arrivals, dismissed],
  );

  // Polling solo para ver avanzar los estados; la llegada de tickets nuevos va por SSE.
  usePolling(refresh, rows.some((ticket) => shouldPoll(ticket.status)), POLL_INTERVAL_MS);

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
      <div style={{ display: "flex", gap: 12, alignItems: "baseline", flexWrap: "wrap" }}>
        <h1 style={{ marginBottom: 4 }}>RFPs</h1>
        <LiveIndicator {...stream} />
      </div>
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

      <section aria-live="polite" aria-label="RFPs nuevas" className="rfp-arrivals">
        {unread.length > 1 && (
          <div style={{ display: "flex", justifyContent: "flex-end" }}>
            <button type="button" onClick={() => setDismissed(new Set(arrivals.keys()))}>
              Ocultar todos los avisos
            </button>
          </div>
        )}
        {unread.map(({ ticket, source }) => (
          <article key={ticket.ticket_id} className={`rfp-arrival${source === "recovered" ? " rfp-arrival-recovered" : ""}`}>
            <div>
              <div className="rfp-arrival-title">
                Nueva RFP · {ticket.client_name ?? "Cliente aún sin identificar"}
                {ticket.client_country ? ` (${ticket.client_country})` : ""}
              </div>
              <div style={{ color: "var(--muted)", fontSize: 13 }}>
                <span className="mono">{ticket.rfp_id}</span> · requiere procesamiento ·{" "}
                {source === "recovered" ? "no llegó en directo (recuperada al sincronizar)" : formatDate(ticket.created_at)}
              </div>
            </div>
            <div style={{ display: "flex", gap: 8 }}>
              <Link href={`/rfps/${ticket.ticket_id}`} className="nav-link" style={{ color: "var(--brand)" }}>
                Ver ticket
              </Link>
              <button
                type="button"
                aria-label={`Ocultar aviso de ${ticket.rfp_id}`}
                onClick={() => setDismissed((previous) => new Set(previous).add(ticket.ticket_id))}
              >
                Ocultar
              </button>
            </div>
          </article>
        ))}
      </section>

      <AsyncSection
        isLoading={isLoading}
        error={error}
        onRetry={() => {
          setLive(null);
          reload();
        }}
        loadingLabel="Cargando RFPs…"
        isEmpty={rows.length === 0}
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
              {rows.map((ticket) => (
                <tr key={ticket.ticket_id} className={arrivals.has(ticket.ticket_id) ? "rfp-row-new" : undefined}>
                  <td className="mono">
                    <Link href={`/rfps/${ticket.ticket_id}`} className="nav-link" style={{ color: "var(--brand)" }}>
                      {ticket.rfp_id}
                    </Link>
                  </td>
                  <td>{ticket.client_name ?? "—"}</td>
                  <td>{ticket.client_country ?? "—"}</td>
                  <td>
                    {arrivals.has(ticket.ticket_id) && (
                      <span className="status-badge rfp-badge-new" style={{ marginRight: 6 }}>
                        Nueva
                      </span>
                    )}
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
