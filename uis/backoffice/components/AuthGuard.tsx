"use client";

import { usePathname, useRouter } from "next/navigation";
import { useEffect, useMemo, useState } from "react";

import { clearAuthToken, hasValidSession } from "../services/session";

const PUBLIC_ROUTES = new Set(["/login", "/register", "/forgot-password", "/reset-password"]);
// Rutas que funcionan con y sin sesión: el callback de Microsoft sirve tanto
// al login (sin sesión) como a la vinculación desde el perfil (con sesión).
// Ni redirigen al login ni sacan de ellas a quien ya entró.
export const OPEN_ROUTES = new Set(["/auth/callback/microsoft"]);

function isSafeRedirectPath(candidate: string | null): candidate is string {
  return Boolean(candidate && candidate.startsWith("/") && !candidate.startsWith("//"));
}

export function AuthGuard({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();
  const [checkingSession, setCheckingSession] = useState(true);

  const isPublicRoute = useMemo(() => PUBLIC_ROUTES.has(pathname), [pathname]);

  useEffect(() => {
    if (OPEN_ROUTES.has(pathname)) {
      setCheckingSession(false);
      return;
    }
    if (isPublicRoute) {
      if (hasValidSession()) {
        router.replace("/");
        return;
      }
      setCheckingSession(false);
      return;
    }

    if (!hasValidSession()) {
      clearAuthToken();
      const next = isSafeRedirectPath(pathname) ? `?next=${encodeURIComponent(pathname)}` : "";
      router.replace(`/login${next}`);
      return;
    }

    setCheckingSession(false);
  }, [isPublicRoute, pathname, router]);

  if (isPublicRoute) {
    return <>{children}</>;
  }

  if (checkingSession) {
    return (
      <main className="shell" style={{ padding: "24px 0 42px" }}>
        <section className="panel">
          <p style={{ margin: 0 }}>Verificando sesion...</p>
        </section>
      </main>
    );
  }

  return <>{children}</>;
}
