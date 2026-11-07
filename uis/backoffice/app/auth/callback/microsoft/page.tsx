"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";

import { federatedApi, takePendingFlow } from "../../../../services/federatedApi";
import { setAuthToken } from "../../../../services/session";
import {
  MICROSOFT_PROVIDER,
  callbackUrl,
  federatedErrorMessage,
  readCallbackParams,
  stateMatches,
  type FederatedPurpose,
} from "../../../../types/federated";

export default function MicrosoftCallbackPage() {
  const router = useRouter();
  const handled = useRef(false);
  const [error, setError] = useState<string | null>(null);
  const [purpose, setPurpose] = useState<FederatedPurpose | null>(null);

  useEffect(() => {
    // El code de Microsoft es de un solo uso: en modo estricto React ejecuta
    // los efectos dos veces en desarrollo y el segundo canje fallaría.
    if (handled.current) return;
    handled.current = true;

    const params = readCallbackParams(window.location.search);
    const pending = takePendingFlow();
    setPurpose(pending?.purpose ?? null);

    if (params.kind === "error") {
      setError(federatedErrorMessage(params.code));
      return;
    }
    if (params.kind === "invalid" || !stateMatches(pending, params.state) || pending === null) {
      // No se llama a la API: este navegador no empezó este flujo.
      setError(federatedErrorMessage("invalid_state"));
      return;
    }

    const finish = async () => {
      try {
        const result = await federatedApi.complete(
          pending.provider || MICROSOFT_PROVIDER,
          { code: params.code, state: params.state, redirect_uri: callbackUrl(window.location.origin) },
          pending.purpose === "link",
        );
        if (result.purpose === "login" && result.access_token) {
          setAuthToken(result.access_token);
          router.replace("/");
          return;
        }
        router.replace(`/account/profile?federated=linked&provider=${encodeURIComponent(result.link?.provider ?? MICROSOFT_PROVIDER)}`);
      } catch (requestError: unknown) {
        setError(requestError instanceof Error ? requestError.message : federatedErrorMessage("provider_error"));
      }
    };
    void finish();
  }, [router]);

  const backHref = purpose === "link" ? "/account/profile" : "/login";
  const backLabel = purpose === "link" ? "Volver a mi perfil" : "Volver al inicio de sesión";

  return (
    <main className="shell" style={{ padding: "24px 0 42px" }}>
      <section className="panel" style={{ maxWidth: 520, margin: "0 auto", display: "grid", gap: 12 }}>
        <h1 style={{ margin: 0 }}>Microsoft</h1>
        {error ? (
          <>
            <p className="error-text" role="alert">{error}</p>
            <p style={{ margin: 0 }}>
              <Link href={backHref} style={{ color: "var(--brand)", fontWeight: 700 }}>
                {backLabel}
              </Link>
            </p>
          </>
        ) : (
          <p style={{ margin: 0 }}>Comprobando tu cuenta de Microsoft…</p>
        )}
      </section>
    </main>
  );
}
