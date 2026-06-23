export function CtaSection() {
  return (
    <section className="container" style={{ padding: "8px 0 24px" }}>
      <div
        style={{
          border: "1px solid var(--line)",
          borderRadius: 24,
          padding: "26px clamp(18px,3vw,38px)",
          background: "linear-gradient(130deg, rgba(15,23,42,.95), rgba(8,47,73,.55))",
        }}
      >
        <h2 style={{ margin: 0, fontSize: "clamp(1.5rem,3vw,2.2rem)" }}>
          Inicia tu registro y conecta tu atencion con una operacion digital confiable
        </h2>
        <p style={{ color: "var(--muted)", lineHeight: 1.7 }}>
          El formulario de registro aplica validaciones para proteger calidad de datos, reducir rechazos de
          facturacion y facilitar integracion con registros clinicos electronicos.
        </p>
        <a
          href="/application"
          style={{
            display: "inline-block",
            background: "var(--brand)",
            color: "#082f49",
            borderRadius: 10,
            padding: "12px 18px",
            fontWeight: 800,
          }}
        >
          Ir al formulario seguro
        </a>
      </div>
    </section>
  );
}
