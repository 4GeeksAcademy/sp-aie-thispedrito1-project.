export function SiteHeader() {
  return (
    <header style={{ borderBottom: "1px solid var(--line)" }}>
      <div className="container" style={{ display: "flex", justifyContent: "space-between", alignItems: "center", padding: "20px 0" }}>
        <a href="/" style={{ display: "inline-flex", alignItems: "center", gap: 12 }}>
          <span
            style={{
              display: "inline-flex",
              height: 40,
              width: 40,
              borderRadius: 12,
              alignItems: "center",
              justifyContent: "center",
              background: "var(--brand)",
              color: "#082f49",
              fontWeight: 800,
            }}
          >
            HC
          </span>
          <span style={{ fontWeight: 800 }}>HealthCore Digital</span>
        </a>
        <nav style={{ display: "flex", gap: 12, flexWrap: "wrap" }}>
          <a href="#beneficios">Beneficios</a>
          <a href="#servicios">Servicios</a>
          <a href="#contacto">Contacto</a>
          <a
            href="/application"
            style={{
              background: "var(--brand)",
              color: "#082f49",
              padding: "10px 14px",
              borderRadius: 10,
              fontWeight: 700,
            }}
          >
            Solicitar acceso
          </a>
        </nav>
      </div>
    </header>
  );
}
