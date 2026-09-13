export type BarItem = {
  /** Clave estable de la fila (si falta, se usa `label`). */
  id?: string;
  label: string;
  value: number;
  /** Texto a la derecha de la barra (el número ya formateado). */
  display: string;
};

type BarListProps = {
  items: BarItem[];
  /** Valor que ocupa el 100% del ancho. Para tasas es 1; para conteos, el mayor. */
  max: number;
  tone: "brand" | "critical" | "warning";
  /** Etiquetas en monoespaciada: sí para identificadores técnicos, no para nombres de negocio. */
  monoLabels?: boolean;
};

const TONE_COLOR: Record<BarListProps["tone"], string> = {
  brand: "var(--brand)",
  critical: "var(--critical)",
  warning: "var(--warning)",
};

/**
 * Barras horizontales solo con CSS: sin librería de gráficos (decisión del
 * proyecto, para no tocar package-lock.json) y con los tokens del tema, así
 * que funcionan igual en modo oscuro y claro. Son decorativas (aria-hidden):
 * los valores exactos viven en la tabla que acompaña a cada panel, que es lo
 * que lee un lector de pantalla.
 *
 * Compartido por /telemetry (reporte técnico) y /reporting (dashboard de
 * negocio).
 */
export function BarList({ items, max, tone, monoLabels = true }: BarListProps) {
  return (
    <div aria-hidden="true" style={{ display: "grid", gap: 8, marginBottom: 14 }}>
      {items.map((item) => {
        const width = max > 0 ? Math.min(100, (item.value / max) * 100) : 0;
        return (
          <div
            key={item.id ?? item.label}
            style={{ display: "grid", gridTemplateColumns: "minmax(90px, 30%) 1fr auto", gap: 10, alignItems: "center" }}
          >
            {/* title: las etiquetas largas se cortan con ellipsis en esta
                columna; al pasar el ratón se ve el texto entero. */}
            <span
              className={monoLabels ? "mono" : undefined}
              title={item.label}
              style={{ fontSize: 12, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}
            >
              {item.label}
            </span>
            <span style={{ background: "var(--line)", borderRadius: 2, height: 10, overflow: "hidden" }}>
              <span style={{ display: "block", width: `${width}%`, height: "100%", background: TONE_COLOR[tone] }} />
            </span>
            <span className="mono" style={{ fontSize: 12, color: "var(--muted)" }}>
              {item.display}
            </span>
          </div>
        );
      })}
    </div>
  );
}
