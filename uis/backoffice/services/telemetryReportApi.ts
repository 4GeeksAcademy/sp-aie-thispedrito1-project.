import type { ReportQuery, TelemetryReport } from "../types/telemetryReport";
import { requestJson } from "./http";

/**
 * Lectura del reporte tecnico. Va por requestJson (y no por
 * services/telemetry.ts) porque aqui SI queremos que un fallo se propague:
 * la pantalla necesita saberlo para mostrar el estado de error con
 * "Reintentar". TelemetryService, en cambio, es fire-and-forget para envios.
 *
 * El backend cachea 60s por combinacion exacta de start_date/end_date, asi
 * que pedir el mismo rango dos veces seguidas no recalcula el pipeline.
 */
export function getTelemetryReport(query: ReportQuery): Promise<TelemetryReport> {
  const params = new URLSearchParams({
    start_date: query.start_date,
    end_date: query.end_date,
  });
  return requestJson<TelemetryReport>(`/telemetry/report?${params.toString()}`, undefined, {
    authRequired: true,
  });
}
