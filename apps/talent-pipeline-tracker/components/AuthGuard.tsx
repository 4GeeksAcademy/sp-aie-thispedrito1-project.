"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useMemo, useState } from "react";

import { clearAuthToken, getAuthToken, hasValidSession } from "@/services/session";

const PUBLIC_ROUTES = new Set(["/login", "/register"]);

function isSafeRedirectPath(candidate: string | null): candidate is string {
  return Boolean(candidate && candidate.startsWith("/") && !candidate.startsWith("//"));
}

export default function AuthGuard({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();
  const [checkingSession, setCheckingSession] = useState(true);

  const isPublicRoute = useMemo(() => PUBLIC_ROUTES.has(pathname), [pathname]);

  useEffect(() => {
    if (isPublicRoute) {
      if (hasValidSession()) {
        router.replace("/");
        return;
      }
      setCheckingSession(false);
      return;
    }

    const token = getAuthToken();
    if (!hasValidSession()) {
      clearAuthToken();
      const next = isSafeRedirectPath(pathname) ? `?next=${encodeURIComponent(pathname)}` : "";
      router.replace(`/login${next}`);
      return;
    }

    if (!token) {
      router.replace("/login");
      return;
    }

    setCheckingSession(false);
  }, [isPublicRoute, pathname, router]);

  const handleLogout = () => {
    clearAuthToken();
    router.replace("/login");
  };

  if (isPublicRoute) {
    return <>{children}</>;
  }

  if (checkingSession) {
    return (
      <main className="min-h-screen bg-slate-50 flex items-center justify-center p-6">
        <div className="bg-white border border-slate-200 rounded-xl p-6 text-center shadow-sm">
          <p className="text-slate-700 font-medium">Verificando sesion...</p>
        </div>
      </main>
    );
  }

  return (
    <>
      <div className="sticky top-0 z-50 border-b border-slate-200 bg-white/95 backdrop-blur-sm">
        <div className="max-w-7xl mx-auto px-6 py-3 flex items-center justify-end gap-3 text-sm">
          <Link href="/account/profile" className="text-slate-700 hover:text-blue-700 font-semibold">
            Mi cuenta
          </Link>
          <button
            type="button"
            onClick={handleLogout}
            className="px-3 py-1.5 rounded-md border border-slate-300 bg-white hover:bg-slate-50 text-slate-800 font-semibold"
          >
            Cerrar sesion
          </button>
        </div>
      </div>
      {children}
    </>
  );
}
