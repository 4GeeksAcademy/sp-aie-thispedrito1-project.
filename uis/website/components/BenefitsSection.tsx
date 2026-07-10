import { benefits } from "../data/content";

export function BenefitsSection() {
  return (
    <section id="beneficios" className="container" style={{ padding: "26px 0 8px" }}>
      <h2 style={{ fontSize: "clamp(1.6rem,3vw,2.2rem)", marginBottom: 14 }}>Ventajas competitivas orientadas a resultados clinicos</h2>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit,minmax(230px,1fr))", gap: 14 }}>
        {benefits.map((benefit) => (
          <article key={benefit.title} style={{ border: "1px solid var(--line)", borderRadius: 16, padding: 20, background: "rgba(15,23,42,.6)" }}>
            <h3 style={{ marginTop: 0, color: "#67e8f9" }}>{benefit.title}</h3>
            <p style={{ color: "var(--muted)", lineHeight: 1.7 }}>{benefit.description}</p>
          </article>
        ))}
      </div>
    </section>
  );
}
