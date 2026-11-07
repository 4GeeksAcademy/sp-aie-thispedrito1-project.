"use client";

import { useCallback, useState } from "react";

import { AsyncSection } from "../../components/AsyncSection";
import { useAccessProfile } from "../../hooks/useAccessProfile";
import { useAsyncData } from "../../hooks/useAsyncData";
import { auditApi } from "../../services/auditApi";
import { can, departmentLabel } from "../../types/access";
import {
  ACTION_LABELS,
  ACTOR_TYPE_LABELS,
  EMPTY_FILTERS,
  OUTCOME_LABELS,
  RESOURCE_LABELS,
  actorKey,
  describeDetails,
  describeIntegrity,
  formatAuditTime,
  label,
  shortHash,
  toAuditQuery,
  validateFilters,
  type AuditActor,
  type AuditCatalog,
  type AuditEntry,
  type AuditFilters,
  type IntegrityResult,
} from "../../types/audit";

// Registro de Auditoría (BRIEFING de HealthCore §3). La API decide el alcance
// (Admin: todo; Supervisor: su departamento; Empleado: sin visor, solo "quién
// accedió a mis pacientes"). Aquí solo se elige qué paneles mostrar.

const PAGE_SIZE = 25;

const OUTCOME_COLORS: Record<string, string> = {
  success: "var(--ok)",
  denied: "var(--critical)",
  blocked: "var(--warning)",
};

export default function AuditLogPage() {
  const profile = useAccessProfile();
  const canAudit = can(profile, "audit:read");

  return (
    <main className="shell" style={{ padding: "24px 0 42px", display: "grid", gap: 16 }}>
      <section className="panel">
        <h1 style={{ marginTop: 0 }}>Registro de auditoría</h1>
        <p style={{ color: "var(--muted)", margin: 0 }}>
          Quién hizo qué, sobre qué y cuándo. Las entradas no se pueden modificar ni borrar, y cada una lleva la huella
          de la anterior. Nunca contiene datos clínicos: los pacientes aparecen solo con su referencia opaca.
        </p>
        {profile && (
          <p style={{ margin: "8px 0 0", fontSize: 14 }}>
            {can(profile, "departments:read_all")
              ? "Ves el registro completo (Admin)."
              : canAudit
                ? `Ves las entradas de ${departmentLabel(profile.department)}.`
                : "Tu rol no tiene acceso al visor completo; abajo ves quién accedió a tus pacientes asignados."}
          </p>
        )}
      </section>

      {canAudit && <AuditViewer isAdmin={can(profile, "departments:read_all")} />}
      {profile && <MyPatientsPanel />}
    </main>
  );
}

// --- Visor -----------------------------------------------------------------------

function AuditViewer({ isAdmin }: { isAdmin: boolean }) {
  const fetchMeta = useCallback(() => Promise.all([auditApi.catalog(), auditApi.actors()]), []);
  const meta = useAsyncData<[AuditCatalog, AuditActor[]]>(fetchMeta, "No se pudieron cargar los filtros del registro.");

  const [draft, setDraft] = useState<AuditFilters>(EMPTY_FILTERS);
  const [applied, setApplied] = useState<AuditFilters>(EMPTY_FILTERS);
  const [formError, setFormError] = useState<string | null>(null);
  // Pila de cursores: [null] = primera página; cada "Más antiguas" apila uno.
  const [cursors, setCursors] = useState<(number | null)[]>([null]);
  const cursor = cursors[cursors.length - 1];

  const fetchPage = useCallback(
    () => auditApi.list(toAuditQuery(applied, { limit: PAGE_SIZE, before: cursor })),
    [applied, cursor],
  );
  const { data, isLoading, error, reload } = useAsyncData(fetchPage, "No se pudo cargar el registro de auditoría.");

  const apply = (event: React.FormEvent) => {
    event.preventDefault();
    const problem = validateFilters(draft);
    setFormError(problem);
    if (problem) return;
    setApplied(draft);
    setCursors([null]);
  };

  const clear = () => {
    setDraft(EMPTY_FILTERS);
    setApplied(EMPTY_FILTERS);
    setFormError(null);
    setCursors([null]);
  };

  const [catalog, actors] = meta.data ?? [null, [] as AuditActor[]];
  const set = (field: keyof AuditFilters) => (event: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) =>
    setDraft({ ...draft, [field]: event.target.value });

  return (
    <>
      {isAdmin && <IntegrityPanel />}
      <AnomaliesPanel />

      <section className="panel">
        <h2 style={{ marginTop: 0 }}>Filtros</h2>
        {/* noValidate: la validación es la de validateFilters, con mensajes en español. */}
        <form onSubmit={apply} noValidate style={{ display: "grid", gap: 12, gridTemplateColumns: "repeat(auto-fit, minmax(200px, 1fr))" }}>
          <label>
            Actor
            <select value={draft.actorKey} onChange={set("actorKey")}>
              <option value="">Todos</option>
              {actors.map((actor) => (
                <option key={actorKey(actor)} value={actorKey(actor)}>
                  {actor.label} · {ACTOR_TYPE_LABELS[actor.actor_type]} ({actor.entries})
                </option>
              ))}
            </select>
          </label>
          <label>
            Acción
            <select value={draft.action} onChange={set("action")}>
              <option value="">Todas</option>
              {Object.keys(catalog?.actions ?? {}).map((code) => (
                <option key={code} value={code}>
                  {label(ACTION_LABELS, code)}
                </option>
              ))}
            </select>
          </label>
          <label>
            Tipo de recurso
            <select value={draft.resourceType} onChange={set("resourceType")}>
              <option value="">Todos</option>
              {(catalog?.resource_types ?? []).map((code) => (
                <option key={code} value={code}>
                  {label(RESOURCE_LABELS, code)}
                </option>
              ))}
            </select>
          </label>
          <label>
            Referencia de paciente
            <input value={draft.patientRef} onChange={set("patientRef")} placeholder="PT-XXXXXXXX" maxLength={11} />
          </label>
          <label>
            Id del recurso
            <input value={draft.resourceId} onChange={set("resourceId")} maxLength={64} />
          </label>
          <label>
            Desde
            <input type="date" value={draft.dateFrom} onChange={set("dateFrom")} />
          </label>
          <label>
            Hasta (incluido)
            <input type="date" value={draft.dateTo} onChange={set("dateTo")} />
          </label>
          <div style={{ display: "flex", gap: 8, alignItems: "end" }}>
            <button type="submit" className="primary">
              Aplicar
            </button>
            <button type="button" onClick={clear}>
              Limpiar
            </button>
          </div>
        </form>
        {formError && (
          <p role="alert" className="error-text">
            {formError}
          </p>
        )}
        {meta.error && <p className="error-text">{meta.error}</p>}
      </section>

      <AsyncSection
        isLoading={isLoading}
        error={error}
        onRetry={reload}
        loadingLabel="Cargando entradas…"
        isEmpty={(data?.items.length ?? 0) === 0}
        emptyLabel="No hay entradas con estos filtros."
      >
        <section className="panel" style={{ display: "grid", gap: 12 }}>
          <p style={{ margin: 0, color: "var(--muted)" }}>
            {data?.total} entradas · página {cursors.length}
          </p>
          <div style={{ overflowX: "auto" }}>
            <table className="table">
              <thead>
                <tr>
                  <th>#</th>
                  <th>Fecha</th>
                  <th>Actor</th>
                  <th>Acción</th>
                  <th>Recurso</th>
                  <th>Departamento</th>
                  <th>Motivo</th>
                  <th>Resultado</th>
                  <th>Origen</th>
                </tr>
              </thead>
              <tbody>
                {data?.items.map((entry) => <EntryRow key={entry.entry_id} entry={entry} purposes={catalog?.purposes ?? {}} />)}
              </tbody>
            </table>
          </div>
          <div style={{ display: "flex", gap: 8 }}>
            <button type="button" disabled={cursors.length === 1} onClick={() => setCursors(cursors.slice(0, -1))}>
              ← Más recientes
            </button>
            <button
              type="button"
              disabled={!data?.next_cursor}
              onClick={() => data?.next_cursor && setCursors([...cursors, data.next_cursor])}
            >
              Más antiguas →
            </button>
          </div>
        </section>
      </AsyncSection>
    </>
  );
}

function EntryRow({ entry, purposes }: { entry: AuditEntry; purposes: Record<string, string> }) {
  const details = describeDetails(entry.details);
  return (
    <tr>
      <td className="mono">{entry.sequence}</td>
      <td>{formatAuditTime(entry.occurred_at)}</td>
      <td>
        <strong>{entry.actor_label}</strong>
        <br />
        <small style={{ color: entry.actor_type === "process" ? "var(--brand)" : "var(--muted)" }}>
          {ACTOR_TYPE_LABELS[entry.actor_type]}
          {entry.on_behalf_of_label ? ` · a petición de ${entry.on_behalf_of_label}` : ""}
        </small>
      </td>
      <td>
        {label(ACTION_LABELS, entry.action)}
        {details && (
          <>
            <br />
            <small style={{ color: "var(--muted)" }}>{details}</small>
          </>
        )}
      </td>
      <td>
        {label(RESOURCE_LABELS, entry.resource_type)}
        {entry.patient_ref && (
          <>
            <br />
            <span className="mono">{entry.patient_ref}</span>
          </>
        )}
      </td>
      <td>{entry.department ? departmentLabel(entry.department) : "—"}</td>
      <td>{entry.context ? (purposes[entry.context] ?? entry.context) : "—"}</td>
      <td style={{ color: OUTCOME_COLORS[entry.outcome] }}>{OUTCOME_LABELS[entry.outcome]}</td>
      <td>
        <span className="mono">{entry.origin}</span>
        <br />
        <small className="mono" title={entry.entry_hash} style={{ color: "var(--muted)" }}>
          huella {shortHash(entry.entry_hash)}
        </small>
      </td>
    </tr>
  );
}

// --- Integridad (solo Admin) -------------------------------------------------------

function IntegrityPanel() {
  const [result, setResult] = useState<IntegrityResult | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const verify = async () => {
    setBusy(true);
    setError(null);
    try {
      setResult(await auditApi.integrity());
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "No se pudo verificar la cadena.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="panel" style={{ display: "grid", gap: 8 }}>
      <h2 style={{ margin: 0 }}>Integridad de la cadena</h2>
      <p style={{ margin: 0, color: "var(--muted)" }}>
        Recalcula la huella de cada entrada y comprueba que apunta a la anterior. Si alguien editara o borrara una entrada
        directamente en la base de datos, la verificación diría exactamente dónde.
      </p>
      <div>
        <button type="button" className="primary" onClick={() => void verify()} disabled={busy}>
          {busy ? "Verificando…" : "Verificar integridad"}
        </button>
      </div>
      {error && (
        <p role="alert" className="error-text">
          {error}{" "}
          <button type="button" onClick={() => void verify()}>
            Reintentar
          </button>
        </p>
      )}
      {result && (
        <div role="status" style={{ display: "grid", gap: 4 }}>
          <strong style={{ color: result.ok ? "var(--ok)" : "var(--critical)" }}>{describeIntegrity(result)}</strong>
          <small className="mono" style={{ color: "var(--muted)" }}>
            Última entrada verificada: #{result.head_sequence ?? "—"} · huella de cabeza {result.head_hash} ·{" "}
            {formatAuditTime(result.verified_at)}
          </small>
          <small style={{ color: "var(--muted)" }}>
            Guarda la huella de cabeza fuera del sistema: es lo que permite detectar también un borrado de las últimas
            entradas.
          </small>
        </div>
      )}
    </section>
  );
}

// --- Accesos inusuales -------------------------------------------------------------

function AnomaliesPanel() {
  const fetchAnomalies = useCallback(() => auditApi.anomalies(), []);
  const { data, isLoading, error, reload } = useAsyncData(
    fetchAnomalies,
    "No se pudo calcular la detección de accesos inusuales.",
  );

  return (
    <section className="panel" style={{ display: "grid", gap: 8 }}>
      <h2 style={{ margin: 0 }}>Accesos inusuales a fichas de pacientes</h2>
      <AsyncSection
        isLoading={isLoading}
        error={error}
        onRetry={reload}
        loadingLabel="Calculando…"
        isEmpty={(data?.items.length ?? 0) === 0}
        emptyLabel="Nadie ha consultado fichas muy por encima de su promedio en las últimas 24 horas."
      >
        <p style={{ margin: 0, color: "var(--muted)" }}>
          Últimas {data?.window_hours} h comparadas con la media diaria de los {data?.baseline_days} días anteriores.
        </p>
        <ul style={{ margin: 0 }}>
          {data?.items.map((item) => (
            <li key={item.actor_id} style={{ color: "var(--warning)" }}>
              <strong>{item.actor_label}</strong>: {item.recent_count} fichas en {data.window_hours} h (su media:{" "}
              {item.baseline_average.toLocaleString("es-ES")} al día)
            </li>
          ))}
        </ul>
      </AsyncSection>
    </section>
  );
}

// --- Empleado: quién accedió a mis pacientes ----------------------------------------

function MyPatientsPanel() {
  const fetchMine = useCallback(() => auditApi.myPatients(), []);
  const { data, isLoading, error, reload } = useAsyncData(fetchMine, "No se pudieron cargar los accesos a tus pacientes.");

  return (
    <section className="panel" style={{ display: "grid", gap: 8 }}>
      <h2 style={{ margin: 0 }}>Quién accedió a mis pacientes asignados</h2>
      <AsyncSection
        isLoading={isLoading}
        error={error}
        onRetry={reload}
        loadingLabel="Cargando…"
        isEmpty={(data?.items.length ?? 0) === 0}
        emptyLabel="No tienes pacientes asignados o nadie ha accedido a ellos."
      >
        <ul style={{ margin: 0, display: "grid", gap: 4 }}>
          {data?.items.map((item) => (
            <li key={item.sequence}>
              <span className="mono">{item.patient_ref}</span> · {label(ACTION_LABELS, item.action)} por{" "}
              <strong>{item.actor_label}</strong> ({formatAuditTime(item.occurred_at)})
              {item.outcome !== "success" && (
                <span style={{ color: OUTCOME_COLORS[item.outcome] }}> · {OUTCOME_LABELS[item.outcome]}</span>
              )}
            </li>
          ))}
        </ul>
      </AsyncSection>
    </section>
  );
}
