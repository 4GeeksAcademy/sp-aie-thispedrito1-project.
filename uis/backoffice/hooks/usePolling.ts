"use client";

import { useEffect } from "react";

/**
 * Ejecuta `onTick` cada `intervalMs` mientras `enabled` sea true (p. ej. un
 * ticket de RFP que sigue `analizando`). Se detiene solo al desactivarse o al
 * desmontar la pantalla, así que no quedan peticiones huérfanas.
 *
 * No muestra estado de carga: es un refresco silencioso por encima de la
 * carga inicial de `useAsyncData` (que sí lo muestra y haría parpadear la
 * pantalla cada 3 s). `onTick` debe ser estable (`useCallback`).
 */
export function usePolling(onTick: () => void, enabled: boolean, intervalMs: number): void {
  useEffect(() => {
    if (!enabled) return;
    const id = window.setInterval(onTick, intervalMs);
    return () => window.clearInterval(id);
  }, [enabled, intervalMs, onTick]);
}
