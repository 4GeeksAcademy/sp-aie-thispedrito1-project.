"use client";

import type { ReactNode } from "react";

interface AsyncSectionProps {
  isLoading: boolean;
  /** Mensaje de error ya legible, o `null`. */
  error: string | null;
  /** Que hacer al pulsar "Reintentar". Normalmente el `reload` de useAsyncData. */
  onRetry: () => void;
  /** Texto del estado de carga, p. ej. "Cargando productos…". */
  loadingLabel: string;
  /** `true` cuando la carga fue bien pero no hay nada que mostrar. */
  isEmpty?: boolean;
  /** Que mostrar en ese caso. Acepta nodos porque alguna pantalla cambia el
   *  texto segun haya filtros activos o no. */
  emptyLabel?: ReactNode;
  /** El contenido real, que solo se renderiza si hay datos que mostrar. */
  children: ReactNode;
}

/**
 * Renderiza los tres estados de una carga asincrona: cargando, error con boton
 * de reintento, y vacio. Solo pinta `children` cuando hay algo que enseñar.
 *
 * El bloque JSX que hay debajo estaba repetido en /incidents,
 * /incidents/summary, /inventory/products y /inventory/orders, con las mismas
 * clases y los mismos estilos inline en cada copia. Aparte de quitar la
 * duplicacion, centralizarlo garantiza lo que la auditoria de gestion de
 * errores del proyecto ya exigia: que ningun estado de error se quede sin su
 * CTA de reintento, porque el boton ya no es opcional, viene con el componente.
 */
export function AsyncSection({
  isLoading,
  error,
  onRetry,
  loadingLabel,
  isEmpty = false,
  emptyLabel,
  children,
}: AsyncSectionProps) {
  if (isLoading) {
    return <p style={{ color: "var(--muted)" }}>{loadingLabel}</p>;
  }

  if (error) {
    return (
      <div className="panel" style={{ display: "flex", gap: 12, alignItems: "center", flexWrap: "wrap" }}>
        <span className="error-text">{error}</span>
        <button type="button" onClick={onRetry}>
          Reintentar
        </button>
      </div>
    );
  }

  if (isEmpty) {
    return (
      <div className="panel">
        <p style={{ margin: 0, color: "var(--muted)" }}>{emptyLabel}</p>
      </div>
    );
  }

  return <>{children}</>;
}
