import { getBackofficeSummary } from "../lib/businessMetrics";
import type { CSSProperties } from "react";

export default function BackofficeHome() {
  const summary = getBackofficeSummary();

  return (
    <main className="shell" style={{ padding: "24px 0 42px" }}>
      <section
        style={{
          border: "1px solid var(--line)",
          borderRadius: 16,
          background: "var(--panel)",
          padding: 20,
          marginBottom: 18,
        }}
      >
        <h1 style={{ marginTop: 0 }}>Vista de entrada del backoffice</h1>
      </section>

      <section style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit,minmax(210px,1fr))", gap: 12, marginBottom: 18 }}>
        <MetricCard title="Tasa global de denegacion" value={`${summary.denialRate}%`} />
        <MetricCard title="No-show semanal" value={`$${summary.lostRevenueWeek}`} />
        <MetricCard title="Validacion de claim" value={summary.claimValidation.valid ? "OK" : "Error"} />
      </section>

      <section
        style={{
          border: "1px solid var(--line)",
          borderRadius: 16,
          background: "var(--panel)",
          padding: 18,
          marginBottom: 18,
        }}
      >
        <h2 style={{ marginTop: 0 }}>Denegacion por pagador</h2>
        <ul style={{ margin: 0, paddingLeft: 18 }}>
          {summary.deniedByPayer.map((row) => (
            <li key={row.payer}>
              {row.payer}: {row.rate}%
            </li>
          ))}
        </ul>
      </section>

      <section
        style={{
          border: "1px solid var(--line)",
          borderRadius: 16,
          background: "var(--panel)",
          padding: 18,
          marginBottom: 18,
        }}
      >
        <h2 style={{ marginTop: 0 }}>CME snapshot (primeros 5)</h2>
        <div style={{ overflowX: "auto" }}>
          <table style={{ width: "100%", borderCollapse: "collapse" }}>
            <thead>
              <tr>
                <th style={th}>Nombre</th>
                <th style={th}>Estado</th>
                <th style={th}>Progreso %</th>
                <th style={th}>Dias licencia</th>
              </tr>
            </thead>
            <tbody>
              {summary.cmeSnapshot.map((item) => (
                <tr key={item.fullName}>
                  <td style={td}>{item.fullName}</td>
                  <td style={td}>{item.complianceStatus}</td>
                  <td style={td}>{item.percentComplete}</td>
                  <td style={td}>{item.licenceDaysRemaining}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      {!summary.claimValidation.valid && (
        <section
          style={{
            border: "1px solid #fecaca",
            borderRadius: 16,
            background: "#fff1f2",
            color: "#881337",
            padding: 16,
          }}
        >
          <strong>Errores de validacion detectados</strong>
          <ul>
            {summary.claimValidation.errors.map((error) => (
              <li key={error}>{error}</li>
            ))}
          </ul>
        </section>
      )}
    </main>
  );
}

function MetricCard({ title, value }: { title: string; value: string }) {
  return (
    <article style={{ border: "1px solid var(--line)", borderRadius: 14, background: "var(--panel)", padding: 16 }}>
      <p style={{ margin: "0 0 6px", color: "var(--muted)", fontSize: 14 }}>{title}</p>
      <strong style={{ fontSize: 24, color: "var(--brand)" }}>{value}</strong>
    </article>
  );
}

const th: CSSProperties = {
  textAlign: "left",
  borderBottom: "1px solid var(--line)",
  padding: "10px 8px",
};

const td: CSSProperties = {
  borderBottom: "1px solid var(--line)",
  padding: "10px 8px",
};
