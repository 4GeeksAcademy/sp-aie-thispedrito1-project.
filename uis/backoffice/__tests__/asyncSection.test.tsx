/**
 * Tests del componente compartido extraido en el hito de auditoria de
 * rendimiento. Cubre sus cuatro estados: cargando, error, vacio y con datos.
 *
 * Mismo enfoque que useAsyncData.test.tsx: `act` de React 19 + createRoot,
 * sin añadir @testing-library al proyecto.
 */
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";

import { AsyncSection } from "../components/AsyncSection";

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean | undefined;
}
globalThis.IS_REACT_ACT_ENVIRONMENT = true;

function render(ui: React.ReactElement) {
  const container = document.createElement("div");
  document.body.appendChild(container);
  let root: Root;
  act(() => {
    root = createRoot(container);
    root.render(ui);
  });
  return {
    container,
    cleanup() {
      act(() => root.unmount());
      container.remove();
    },
  };
}

const base = {
  onRetry: () => {},
  loadingLabel: "Cargando productos…",
  children: <table data-testid="tabla" />,
};

describe("AsyncSection", () => {
  it("cargando: muestra la etiqueta y NO el contenido", () => {
    const { container, cleanup } = render(
      <AsyncSection {...base} isLoading error={null} />,
    );
    expect(container.textContent).toContain("Cargando productos…");
    expect(container.querySelector("table")).toBeNull();
    cleanup();
  });

  it("error: muestra el mensaje y SIEMPRE un boton de reintento", () => {
    const { container, cleanup } = render(
      <AsyncSection {...base} isLoading={false} error="No se pudo cargar la lista." />,
    );
    expect(container.textContent).toContain("No se pudo cargar la lista.");
    const boton = container.querySelector("button");
    expect(boton).not.toBeNull();
    expect(boton?.textContent).toContain("Reintentar");
    expect(container.querySelector("table")).toBeNull();
    cleanup();
  });

  it("error: el boton de reintento invoca onRetry", () => {
    const onRetry = jest.fn();
    const { container, cleanup } = render(
      <AsyncSection {...base} onRetry={onRetry} isLoading={false} error="fallo" />,
    );
    act(() => {
      container.querySelector("button")?.dispatchEvent(
        new MouseEvent("click", { bubbles: true }),
      );
    });
    expect(onRetry).toHaveBeenCalledTimes(1);
    cleanup();
  });

  it("vacio: muestra el mensaje de lista vacia en vez del contenido", () => {
    const { container, cleanup } = render(
      <AsyncSection {...base} isLoading={false} error={null} isEmpty emptyLabel="Nada que mostrar." />,
    );
    expect(container.textContent).toContain("Nada que mostrar.");
    expect(container.querySelector("table")).toBeNull();
    cleanup();
  });

  it("con datos: renderiza el contenido y ningun mensaje de estado", () => {
    const { container, cleanup } = render(
      <AsyncSection {...base} isLoading={false} error={null} isEmpty={false} />,
    );
    expect(container.querySelector("table")).not.toBeNull();
    expect(container.textContent).not.toContain("Cargando");
    cleanup();
  });
});
