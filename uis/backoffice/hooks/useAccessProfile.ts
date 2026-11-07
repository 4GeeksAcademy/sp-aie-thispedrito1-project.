"use client";

import { useEffect, useState } from "react";

import { authApi } from "../services/authApi";
import { getAuthToken } from "../services/session";
import type { AccessProfile } from "../types/access";

// Una sola petición a /auth/me por sesión, compartida por el menú y por cada
// pantalla. La caché va por token: si otra persona inicia sesión en el mismo
// navegador, nunca hereda el rol de la anterior.
let cached: { token: string; promise: Promise<AccessProfile> } | null = null;

export function loadAccessProfile(): Promise<AccessProfile> {
  const token = getAuthToken() ?? "";
  if (!cached || cached.token !== token) {
    const promise = authApi.getMe().then(({ role, role_rank, department, department_id, capabilities }) => ({
      role,
      role_rank,
      department,
      department_id,
      capabilities,
    }));
    // Un fallo no se queda en caché: el siguiente montaje lo reintenta.
    promise.catch(() => {
      if (cached?.promise === promise) cached = null;
    });
    cached = { token, promise };
  }
  return cached.promise;
}

/** Solo para tests y para el cierre de sesión. */
export function resetAccessProfileCache(): void {
  cached = null;
}

/** Perfil de acceso de quien inició sesión. `null` mientras carga o si falló:
 * en ese caso la interfaz no muestra ninguna acción restringida. */
export function useAccessProfile(enabled = true): AccessProfile | null {
  const [profile, setProfile] = useState<AccessProfile | null>(null);

  useEffect(() => {
    if (!enabled) return;
    let active = true;
    loadAccessProfile()
      .then((value) => {
        if (active) setProfile(value);
      })
      .catch(() => {
        if (active) setProfile(null);
      });
    return () => {
      active = false;
    };
  }, [enabled]);

  return profile;
}
