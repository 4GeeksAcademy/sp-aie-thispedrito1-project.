import { stats } from "../data/content";

export function HeroSection() {
  return (
    <section className="container" style={{ display: "grid", gap: 30, padding: "48px 0" }}>
      <div style={{ display: "grid", gap: 22, gridTemplateColumns: "repeat(auto-fit,minmax(280px,1fr))", alignItems: "center" }}>
        <div style={{ maxWidth: 760 }}>
        <p
          style={{
            display: "inline-block",
            border: "1px solid rgba(34, 211, 238, 0.45)",
            padding: "6px 12px",
            borderRadius: 999,
            color: "#67e8f9",
            fontSize: 12,
            letterSpacing: "0.14em",
            textTransform: "uppercase",
            fontWeight: 700,
          }}
        >
          Red internacional de salud ambulatoria
        </p>
        <h1 style={{ fontSize: "clamp(2rem, 4.5vw, 3.4rem)", margin: "16px 0 10px", lineHeight: 1.1 }}>
          La confianza clinica de siempre, ahora con velocidad digital.
        </h1>
        <p style={{ color: "var(--muted)", lineHeight: 1.7, maxWidth: 680 }}>
          HealthCore integra 12 clinicas entre EE.UU. y Reino Unido con una sola experiencia digital para reservas,
          gestion clinica y cumplimiento normativo. Menos friccion operativa, mas continuidad asistencial para cada
          paciente.
        </p>
        <div style={{ display: "flex", gap: 12, marginTop: 18, flexWrap: "wrap" }}>
          <a href="/application" style={{ background: "var(--accent)", color: "#064e3b", borderRadius: 10, padding: "12px 16px", fontWeight: 800 }}>
            Comenzar registro
          </a>
          <a href="#servicios" style={{ border: "1px solid var(--line)", borderRadius: 10, padding: "12px 16px", fontWeight: 700 }}>
            Ver servicios
          </a>
        </div>
        </div>
        <article
          style={{
            border: "1px solid var(--line)",
            borderRadius: 18,
            padding: 16,
            background: "rgba(15,23,42,.72)",
          }}
        >
          <img
            src="https://images.unsplash.com/photo-1576091160399-112ba8d25d1d?auto=format&fit=crop&w=1280&q=80"
            alt="Profesional sanitario usando una plataforma clinica digital en consulta ambulatoria"
            style={{ width: "100%", height: 260, objectFit: "cover", borderRadius: 12 }}
            loading="lazy"
          />
          <h2 style={{ marginBottom: 6 }}>Operacion clinica conectada y trazable</h2>
          <p style={{ margin: 0, color: "var(--muted)", lineHeight: 1.65 }}>
            Unifica agenda, pre-registro y verificacion de datos para disminuir no-shows, acelerar flujos de atencion y
            mantener estandares de seguridad para HIPAA y UK GDPR.
          </p>
        </article>
      </div>
      <ul style={{ display: "grid", gap: 12, gridTemplateColumns: "repeat(auto-fit,minmax(180px,1fr))", padding: 0, margin: 0, listStyle: "none" }}>
        {stats.map((item) => (
          <li key={item.label} style={{ border: "1px solid var(--line)", borderRadius: 16, padding: 16, background: "rgba(15,23,42,.65)" }}>
            <strong style={{ display: "block", fontSize: 28, marginBottom: 2 }}>{item.value}</strong>
            <span style={{ color: "var(--muted)" }}>{item.label}</span>
          </li>
        ))}
      </ul>
    </section>
  );
}
