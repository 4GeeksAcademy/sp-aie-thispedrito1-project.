import { ALL_NAV_HREFS, isActivePath } from "../types/navigation";

function activeLinks(pathname: string): string[] {
  return ALL_NAV_HREFS.filter((href) => isActivePath(pathname, href));
}

describe("isActivePath", () => {
  it("marca Inicio solo en la portada exacta", () => {
    expect(activeLinks("/")).toEqual(["/"]);
    expect(isActivePath("/suppliers", "/")).toBe(false);
  });

  it("el enlace más específico gana a su prefijo", () => {
    expect(activeLinks("/incidents/new")).toEqual(["/incidents/new"]);
    expect(activeLinks("/incidents/summary")).toEqual(["/incidents/summary"]);
    expect(activeLinks("/incidents")).toEqual(["/incidents"]);
  });

  it("las subrutas fuera del menú iluminan a su sección", () => {
    expect(activeLinks("/rfps/abc-123")).toEqual(["/rfps"]);
    expect(activeLinks("/inventory/orders/inbound")).toEqual(["/inventory/orders"]);
  });

  it("no confunde un prefijo de texto con un segmento de ruta", () => {
    expect(isActivePath("/rfpsXYZ", "/rfps")).toBe(false);
  });

  it("rutas sin enlace en el menú no iluminan nada", () => {
    expect(activeLinks("/account/profile")).toEqual([]);
  });
});
