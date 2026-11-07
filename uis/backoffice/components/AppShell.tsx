"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";

import { useAccessProfile } from "../hooks/useAccessProfile";
import { NAV_SECTIONS, isActivePath, visibleSections } from "../types/navigation";
import { SessionMenu } from "./SessionMenu";
import { ThemeToggle } from "./ThemeToggle";

const PUBLIC_ROUTES = new Set(["/login", "/register", "/forgot-password", "/reset-password", "/auth/callback/microsoft"]);

export function AppShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const [menuOpen, setMenuOpen] = useState(false);
  const isPublic = PUBLIC_ROUTES.has(pathname);
  const profile = useAccessProfile(!isPublic);

  // Al navegar desde el menú móvil, el panel se cierra solo.
  useEffect(() => {
    setMenuOpen(false);
  }, [pathname]);

  // Login, registro y recuperación de contraseña van sin menú.
  if (isPublic) {
    return <>{children}</>;
  }

  return (
    <div className="app-layout" data-menu-open={menuOpen ? "true" : "false"}>
      <aside className="app-sidebar" id="app-sidebar">
        <Link href="/" className="app-brand">
          <span className="app-brand-mark" aria-hidden="true" />
          <span>
            <strong>HealthCore</strong>
            <small>Unidad interna · Operaciones</small>
          </span>
        </Link>

        <nav aria-label="Navegación principal" className="app-nav">
          {visibleSections(NAV_SECTIONS, profile).map((section) => (
            <div key={section.title} className="app-nav-section">
              <p className="app-nav-title">{section.title}</p>
              <ul>
                {section.items.map((item) => {
                  const active = isActivePath(pathname, item.href);
                  return (
                    <li key={item.href}>
                      <Link
                        href={item.href}
                        className="app-nav-link"
                        aria-current={active ? "page" : undefined}
                      >
                        {item.label}
                      </Link>
                    </li>
                  );
                })}
              </ul>
            </div>
          ))}
        </nav>
      </aside>

      <div className="app-backdrop" onClick={() => setMenuOpen(false)} aria-hidden="true" />

      <div className="app-main">
        <header className="app-topbar">
          <button
            type="button"
            className="app-menu-button"
            aria-expanded={menuOpen}
            aria-controls="app-sidebar"
            onClick={() => setMenuOpen((open) => !open)}
          >
            {menuOpen ? "Cerrar" : "Menú"}
          </button>
          <span className="app-topbar-title">HealthCore Panel</span>
          <div className="app-topbar-actions">
            <ThemeToggle />
            <SessionMenu />
          </div>
        </header>
        <div className="app-content">{children}</div>
      </div>
    </div>
  );
}
