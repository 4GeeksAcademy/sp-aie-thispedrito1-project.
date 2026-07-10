import { contactInfo } from "../data/content";

export function ContactSection() {
  return (
    <section id="contacto" className="container" style={{ paddingBottom: 40 }}>
      <div style={{ border: "1px solid var(--line)", borderRadius: 22, padding: "24px", background: "rgba(15,23,42,.6)", display: "grid", gap: 18, gridTemplateColumns: "repeat(auto-fit,minmax(260px,1fr))" }}>
        <article>
          <h2 style={{ marginTop: 0 }}>Contacto y siguientes pasos</h2>
          <p style={{ color: "var(--muted)", lineHeight: 1.7 }}>
            Nuestro equipo de HealthCore Digital acompana la implementacion clinica y operativa para cada sede.
            Solicita una evaluacion y te compartimos un plan de despliegue por region.
          </p>
        </article>
        <article>
          <ul style={{ listStyle: "none", padding: 0, margin: 0, display: "grid", gap: 10 }}>
            {contactInfo.map((item) => (
              <li key={item.label}>
                <strong style={{ color: "#67e8f9" }}>{item.label}: </strong>
                <span>{item.value}</span>
              </li>
            ))}
          </ul>
          <a href="/application" style={{ marginTop: 14, display: "inline-block", background: "var(--accent)", color: "#064e3b", borderRadius: 10, padding: "10px 14px", fontWeight: 800 }}>
            Solicitar demo clinica
          </a>
        </article>
      </div>
    </section>
  );
}
