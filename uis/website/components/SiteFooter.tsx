export function SiteFooter() {
  return (
    <footer style={{ borderTop: "1px solid var(--line)", marginTop: 10 }}>
      <div className="container" style={{ padding: "24px 0", color: "var(--muted)", fontSize: 14, display: "flex", justifyContent: "space-between", gap: 12, flexWrap: "wrap" }}>
        <p style={{ margin: 0 }}>
          HealthCore Digital · Transformacion digital sanitaria con estandares clinicos y regulatorios.
        </p>
        <p style={{ margin: 0 }}>Operacion regional: EE.UU. y Reino Unido.</p>
      </div>
    </footer>
  );
}
