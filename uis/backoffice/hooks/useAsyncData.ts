"use client";

import { useCallback, useEffect, useRef, useState } from "react";

export interface AsyncDataState<T> {
  /** Datos ya cargados, o `null` mientras no haya una carga con exito. */
  data: T | null;
  isLoading: boolean;
  /** Mensaje legible para el usuario, o `null` si no hubo fallo. */
  error: string | null;
  /** Vuelve a ejecutar el fetcher (para el boton "Reintentar"). */
  reload: () => void;
}

/**
 * Encapsula el ciclo cargando -> exito | error de una lectura contra la API.
 *
 * Este triplete de estados (`isLoading` / `loadError` / `data`) mas su
 * `useCallback` + `useEffect` estaba copiado casi caracter por caracter en
 * cuatro pantallas: /incidents, /incidents/summary, /inventory/products y
 * /inventory/orders. Ver AUDIT.md para el analisis de la duplicacion.
 *
 * Al centralizarlo se corrige ademas una condicion de carrera que las copias
 * sueltas tenian: en /incidents el fetcher se vuelve a lanzar cada vez que
 * cambian los filtros, y si la respuesta de una peticion antigua llegaba
 * despues que la de una nueva, la antigua sobreescribia la lista y la pantalla
 * mostraba resultados que no correspondian a los filtros seleccionados. El
 * contador `requestIdRef` descarta cualquier respuesta que ya no sea la
 * ultima pedida.
 *
 * @param fetcher  Funcion que hace la lectura. DEBE ser estable (envuelta en
 *                 `useCallback` por quien llama), porque es una dependencia
 *                 del efecto: una funcion nueva en cada render dispararia una
 *                 peticion en bucle.
 * @param errorMessage Mensaje a mostrar si el fetcher falla. Se genera aqui y
 *                 no se propaga el error crudo: la convencion del proyecto es
 *                 que el usuario nunca ve codigos de estado ni stack traces.
 */
export function useAsyncData<T>(fetcher: () => Promise<T>, errorMessage: string): AsyncDataState<T> {
  const [data, setData] = useState<T | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Identifica la peticion en curso. Solo la ultima puede escribir estado.
  const requestIdRef = useRef(0);
  // Evita escribir estado despues de que el componente se haya desmontado.
  const isMountedRef = useRef(true);

  useEffect(() => {
    isMountedRef.current = true;
    return () => {
      isMountedRef.current = false;
    };
  }, []);

  const run = useCallback(async () => {
    const requestId = requestIdRef.current + 1;
    requestIdRef.current = requestId;

    setIsLoading(true);
    setError(null);

    try {
      const result = await fetcher();
      if (!isMountedRef.current || requestIdRef.current !== requestId) return;
      setData(result);
    } catch {
      if (!isMountedRef.current || requestIdRef.current !== requestId) return;
      setError(errorMessage);
    } finally {
      if (isMountedRef.current && requestIdRef.current === requestId) {
        setIsLoading(false);
      }
    }
  }, [fetcher, errorMessage]);

  useEffect(() => {
    void run();
  }, [run]);

  const reload = useCallback(() => {
    void run();
  }, [run]);

  return { data, isLoading, error, reload };
}
