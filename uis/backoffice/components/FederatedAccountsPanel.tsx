"use client";

import Link from "next/link";
import { useCallback, useState } from "react";

import { useAsyncData } from "../hooks/useAsyncData";
import { beginFederatedFlow, federatedApi } from "../services/federatedApi";
import {
  MICROSOFT_PROVIDER,
  callbackUrl,
  federatedErrorMessage,
  providerLabel,
  type FederatedAccountStatus,
} from "../types/federated";
import { AsyncSection } from "./AsyncSection";

function formatDate(iso: string): string {
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? iso : date.toLocaleString("es-ES", { dateStyle: "medium", timeStyle: "short" });
}

/**
 * Inicio de sesión con Microsoft en el perfil (BRIEFING de Autenticación
 * Federada §4): vincular y desvincular viven AQUÍ, con sesión iniciada.
 * `justLinkedProvider` llega del callback para confirmar la vinculación.
 */
export function FederatedAccountsPanel({ justLinkedProvider }: { justLinkedProvider: string | null }) {
  const fetcher = useCallback(() => federatedApi.status(), []);
  const { data, isLoading, error, reload } = useAsyncData<FederatedAccountStatus>(
    fetcher,
    "No se pudo cargar el estado del inicio de sesión con Microsoft.",
  );
  const [override, setOverride] = useState<FederatedAccountStatus | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(
    justLinkedProvider ? `Vinculación completada: tu cuenta de ${providerLabel(justLinkedProvider)} ya puede usarse para iniciar sesión.` : null,
  );
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);

  const status = override ?? data;
  const microsoftLink = status?.links.find((link) => link.provider === MICROSOFT_PROVIDER) ?? null;

  const handleLink = async () => {
    setBusy(true);
    setActionError(null);
    try {
      await beginFederatedFlow(MICROSOFT_PROVIDER, "link", callbackUrl(window.location.origin));
    } catch (requestError: unknown) {
      setActionError(requestError instanceof Error ? requestError.message : federatedErrorMessage("provider_error"));
      setBusy(false);
    }
  };

  const handleUnlink = async () => {
    setBusy(true);
    setActionError(null);
    setNotice(null);
    try {
      const result = await federatedApi.unlink(MICROSOFT_PROVIDER);
      setOverride(result.account);
      setNotice(`Cuenta de ${providerLabel(result.provider)} desvinculada. Ya no podrás iniciar sesión con ella.`);
    } catch (requestError: unknown) {
      setActionError(requestError instanceof Error ? requestError.message : federatedErrorMessage("provider_error"));
    } finally {
      setConfirming(false);
      setBusy(false);
    }
  };

  return (
    <section style={{ display: "grid", gap: 10, paddingTop: 16, borderTop: "1px solid var(--line)" }}>
      <div>
        <h2 style={{ margin: 0, fontSize: "1.1rem" }}>Inicio de sesión con Microsoft</h2>
        <p style={{ color: "var(--muted)", margin: "4px 0 0" }}>
          Vincula tu cuenta corporativa de Microsoft para entrar sin contraseña. Solo se puede vincular desde aquí.
        </p>
      </div>

      {notice && <p className="success-text" role="status" style={{ margin: 0 }}>{notice}</p>}
      {actionError && <p className="error-text" role="alert" style={{ margin: 0 }}>{actionError}</p>}

      <AsyncSection isLoading={isLoading && !override} error={override ? null : error} onRetry={reload} loadingLabel="Cargando vinculaciones…">
        {status && microsoftLink ? (
          <div style={{ display: "grid", gap: 8 }}>
            <p style={{ margin: 0 }}>
              <strong>Microsoft vinculado</strong>
              {microsoftLink.email_at_link_time ? ` · ${microsoftLink.email_at_link_time}` : ""}
              <br />
              <small style={{ color: "var(--muted)" }}>Desde {formatDate(microsoftLink.linked_at)}</small>
            </p>

            {status.unlink_warning ? (
              <>
                <p className="error-text" role="alert" style={{ margin: 0 }}>
                  {federatedErrorMessage(status.unlink_warning)}{" "}
                  <Link href="/forgot-password" style={{ color: "var(--brand)", fontWeight: 700 }}>
                    Crear contraseña
                  </Link>
                </p>
                <button type="button" disabled>
                  Desvincular Microsoft
                </button>
              </>
            ) : confirming ? (
              <div style={{ display: "grid", gap: 8 }}>
                <p style={{ margin: 0 }}>
                  ¿Desvincular Microsoft? Seguirás entrando con tu contraseña, pero no con Microsoft.
                </p>
                <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
                  <button type="button" onClick={handleUnlink} disabled={busy}>
                    {busy ? "Desvinculando..." : "Sí, desvincular"}
                  </button>
                  <button type="button" onClick={() => setConfirming(false)} disabled={busy}>
                    Cancelar
                  </button>
                </div>
              </div>
            ) : (
              <button type="button" onClick={() => setConfirming(true)}>
                Desvincular Microsoft
              </button>
            )}
          </div>
        ) : (
          <button type="button" onClick={handleLink} disabled={busy}>
            {busy ? "Redirigiendo a Microsoft..." : "Vincular cuenta de Microsoft"}
          </button>
        )}
      </AsyncSection>
    </section>
  );
}
