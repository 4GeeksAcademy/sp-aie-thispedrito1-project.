"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";

import { clearAuthToken, hasValidSession } from "../services/session";

const PUBLIC_ROUTES = new Set(["/login", "/register"]);

export function SessionMenu() {
  const pathname = usePathname();
  const router = useRouter();

  const onLogout = () => {
    clearAuthToken();
    router.replace("/login");
  };

  if (PUBLIC_ROUTES.has(pathname) || !hasValidSession()) {
    return (
      <>
        <Link href="/login" className="nav-link">
          Iniciar sesión
        </Link>
        <Link href="/register" className="nav-link">
          Registro
        </Link>
      </>
    );
  }

  return (
    <>
      <Link href="/account/profile" className="topbar-link">
        Mi cuenta
      </Link>
      <button type="button" onClick={onLogout} className="topbar-logout">
        Cerrar sesión
      </button>
    </>
  );
}
