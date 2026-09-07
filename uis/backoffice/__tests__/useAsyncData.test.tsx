/**
 * Tests del Custom Hook extraido en el hito de auditoria de rendimiento.
 *
 * No hay @testing-library/react en el proyecto y no se añade una dependencia
 * solo para esto: React 19 exporta `act` desde el propio paquete `react`, y
 * con `createRoot` de react-dom/client se puede montar un componente sonda en
 * jsdom sin nada mas.
 */
import { act, useEffect } from "react";
import { createRoot, type Root } from "react-dom/client";

import { useAsyncData } from "../hooks/useAsyncData";

// React 19 exige esta bandera para que `act` no avise por consola.
declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean | undefined;
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true;

type Snapshot<T> = { data: T | null; isLoading: boolean; error: string | null; reload: () => void };

/** Monta el hook y devuelve una referencia viva a su ultimo estado. */
function mountHook<T>(fetcher: () => Promise<T>, message = "fallo") {
  const seen: Snapshot<T>[] = [];
  let container: HTMLDivElement;
  let root: Root;

  function Probe() {
    const state = useAsyncData<T>(fetcher, message);
    useEffect(() => {
      seen.push(state);
    });
    seen[seen.length] = state;
    return null;
  }

  act(() => {
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
    root.render(<Probe />);
  });

  return {
    get last() {
      return seen[seen.length - 1];
    },
    async flush() {
      await act(async () => {
        await Promise.resolve();
      });
    },
    unmount() {
      act(() => root.unmount());
      container.remove();
    },
  };
}

describe("useAsyncData", () => {
  it("caso feliz: empieza cargando y termina exponiendo los datos", async () => {
    const fetcher = jest.fn().mockResolvedValue(["a", "b"]);
    const h = mountHook(fetcher);

    expect(h.last.isLoading).toBe(true);
    expect(h.last.data).toBeNull();

    await h.flush();

    expect(h.last.isLoading).toBe(false);
    expect(h.last.data).toEqual(["a", "b"]);
    expect(h.last.error).toBeNull();
    expect(fetcher).toHaveBeenCalledTimes(1);
    h.unmount();
  });

  it("caso de fallo: expone el mensaje legible y nunca el error crudo", async () => {
    const fetcher = jest.fn().mockRejectedValue(new Error("500 Internal Server Error"));
    const h = mountHook(fetcher, "No se pudo cargar la lista.");

    await h.flush();

    expect(h.last.error).toBe("No se pudo cargar la lista.");
    expect(h.last.data).toBeNull();
    expect(h.last.isLoading).toBe(false);
    h.unmount();
  });

  it("reload vuelve a pedir los datos y limpia el error anterior", async () => {
    const fetcher = jest
      .fn()
      .mockRejectedValueOnce(new Error("caida temporal"))
      .mockResolvedValueOnce(["recuperado"]);
    const h = mountHook(fetcher);

    await h.flush();
    expect(h.last.error).not.toBeNull();

    await act(async () => {
      h.last.reload();
      await Promise.resolve();
    });
    await h.flush();

    expect(h.last.error).toBeNull();
    expect(h.last.data).toEqual(["recuperado"]);
    expect(fetcher).toHaveBeenCalledTimes(2);
    h.unmount();
  });

  it("caso limite: una respuesta lenta y obsoleta no pisa a la mas reciente", async () => {
    // Este es el bug que tenian las cuatro copias sueltas del patron: en
    // /incidents, cambiar de filtro deja dos peticiones en vuelo, y si la
    // primera responde despues que la segunda, la lista mostraba resultados
    // que no correspondian al filtro seleccionado.
    let resolveLenta!: (v: string[]) => void;
    const lenta = new Promise<string[]>((res) => {
      resolveLenta = res;
    });

    const fetcher = jest
      .fn<Promise<string[]>, []>()
      .mockReturnValueOnce(lenta)
      .mockResolvedValueOnce(["resultado nuevo"]);

    const h = mountHook<string[]>(fetcher);

    // Segunda peticion antes de que la primera haya respondido.
    await act(async () => {
      h.last.reload();
      await Promise.resolve();
    });
    await h.flush();

    expect(h.last.data).toEqual(["resultado nuevo"]);

    // Ahora responde la primera, ya obsoleta: debe descartarse.
    await act(async () => {
      resolveLenta(["resultado viejo"]);
      await Promise.resolve();
    });
    await h.flush();

    expect(h.last.data).toEqual(["resultado nuevo"]);
    h.unmount();
  });
});
