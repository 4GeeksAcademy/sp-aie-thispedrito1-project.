import Image from "next/image";

import { services } from "../data/content";

export function ServicesSection() {
  return (
    <section id="servicios" className="container" style={{ padding: "24px 0" }}>
      <h2 style={{ fontSize: "clamp(1.6rem,3vw,2.2rem)", marginBottom: 14 }}>Servicios clinicos y digitales que fortalecen tu operacion</h2>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit,minmax(260px,1fr))", gap: 14 }}>
        {services.map((service) => (
          <article key={service.title} style={{ border: "1px solid var(--line)", borderRadius: 16, overflow: "hidden", background: "rgba(15,23,42,.6)" }}>
            {/*
              Estas si deben seguir siendo lazy: estan por debajo del fold y
              no compiten por el LCP. Lo que se gana aqui es el reescalado:
              antes se descargaba el original de 900 px de ancho para una
              tarjeta que en movil mide ~345 px. next/image sirve WebP en el
              tamano que toca segun `sizes`, y width/height reservan el hueco
              para que la tarjeta no salte al cargar.
            */}
            <Image
              src={service.image}
              alt={service.alt}
              width={900}
              height={600}
              sizes="(max-width: 620px) 100vw, 320px"
              loading="lazy"
              style={{ width: "100%", height: 180, objectFit: "cover" }}
            />
            <div style={{ padding: 18 }}>
              <h3 style={{ marginTop: 0, color: "#67e8f9" }}>{service.title}</h3>
              <p style={{ color: "var(--muted)", lineHeight: 1.65 }}>{service.description}</p>
            </div>
          </article>
        ))}
      </div>
    </section>
  );
}
