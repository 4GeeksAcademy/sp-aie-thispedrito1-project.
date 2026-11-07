// Menú del backoffice: datos puros, sin JSX, para poder testearlos y
// cambiar el orden o los grupos sin tocar el componente.

export type NavItem = {
  href: string;
  label: string;
};

export type NavSection = {
  title: string;
  items: NavItem[];
};

export const NAV_SECTIONS: NavSection[] = [
  {
    title: "General",
    items: [{ href: "/", label: "Inicio" }],
  },
  {
    title: "Operaciones",
    items: [
      { href: "/suppliers", label: "Proveedores" },
      { href: "/incidents", label: "Incidencias" },
      { href: "/incidents/new", label: "Reportar incidencia" },
      { href: "/incidents/summary", label: "Resumen de incidencias" },
    ],
  },
  {
    title: "Inventario",
    items: [
      { href: "/inventory/products", label: "Productos y stock" },
      { href: "/inventory/orders", label: "Historial de órdenes" },
    ],
  },
  {
    title: "Informes",
    items: [
      { href: "/reporting", label: "Informe mensual" },
      { href: "/telemetry", label: "Telemetría" },
    ],
  },
  {
    title: "Inteligencia artificial",
    items: [
      { href: "/knowledge", label: "Asistente de políticas" },
      { href: "/compliance-chat", label: "Asistente de compliance" },
      { href: "/rfps", label: "RFPs" },
    ],
  },
];

export const ALL_NAV_HREFS: string[] = NAV_SECTIONS.flatMap((section) =>
  section.items.map((item) => item.href),
);

/**
 * ¿Debe marcarse `href` como la página actual cuando el usuario está en `pathname`?
 *
 * `allHrefs` son todos los enlaces del menú, por si hace falta compararlos
 * entre sí (p. ej. "/incidents" frente a "/incidents/new").
 */
export function isActivePath(pathname: string, href: string, allHrefs: string[] = ALL_NAV_HREFS): boolean {
  // Delegado por el usuario: "el prefijo más largo gana".
  const matches = (candidate: string) =>
    candidate === "/"
      ? pathname === "/"
      : pathname === candidate || pathname.startsWith(`${candidate}/`);

  if (!matches(href)) {
    return false;
  }
  // Solo gana si ningún otro enlace del menú encaja de forma más específica.
  return !allHrefs.some((other) => other.length > href.length && matches(other));
}
