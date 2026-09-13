import type { MonthlyClinicSupplyPerformance, PipelineRunStatus } from "../types/businessReport";
import { ApiRequestError, requestJson } from "./http";

/**
 * Lecturas del pipeline de desempeño de negocio (services/reporting).
 *
 * Un 404 aquí no es un fallo: significa "ese mes no tiene informe calculado"
 * o "el pipeline nunca ha corrido", así que se devuelve `null` para que la
 * pantalla muestre un estado vacío explicado, no un error con "Reintentar".
 * Cualquier otro error se propaga igual que en el resto del backoffice.
 */
async function nullOn404<T>(request: Promise<T>): Promise<T | null> {
  try {
    return await request;
  } catch (error) {
    if (error instanceof ApiRequestError && error.status === 404) {
      return null;
    }
    throw error;
  }
}

/** Sin `monthStart`, la API devuelve el mes calculado más reciente. */
export function getMonthlyClinicSupplyPerformance(monthStart?: string): Promise<MonthlyClinicSupplyPerformance | null> {
  const query = monthStart ? `?${new URLSearchParams({ month_start: monthStart }).toString()}` : "";
  return nullOn404(
    requestJson<MonthlyClinicSupplyPerformance>(`/reporting/monthly-clinic-supply-performance${query}`, undefined, {
      authRequired: true,
    }),
  );
}

export function getLatestPipelineRun(): Promise<PipelineRunStatus | null> {
  return nullOn404(requestJson<PipelineRunStatus>("/reporting/pipeline-runs/latest", undefined, { authRequired: true }));
}
