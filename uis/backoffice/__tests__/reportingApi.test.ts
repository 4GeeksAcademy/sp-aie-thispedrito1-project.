/**
 * services/reportingApi.ts: un 404 de /reporting es "sin informe", no un fallo.
 */
import { getLatestPipelineRun, getMonthlyClinicSupplyPerformance } from "../services/reportingApi";
import { ApiRequestError, requestJson } from "../services/http";

jest.mock("../services/http", () => {
  const actual = jest.requireActual("../services/http");
  return { ...actual, requestJson: jest.fn() };
});

const mockedRequestJson = requestJson as jest.MockedFunction<typeof requestJson>;

afterEach(() => mockedRequestJson.mockReset());

describe("getMonthlyClinicSupplyPerformance", () => {
  it("pide el mes indicado al endpoint de consulta de KPIs con sesión (camino feliz)", async () => {
    const report = { month_start: "2026-08-01", clinics: [] };
    mockedRequestJson.mockResolvedValue(report);

    await expect(getMonthlyClinicSupplyPerformance("2026-08-01")).resolves.toEqual(report);
    expect(mockedRequestJson).toHaveBeenCalledWith(
      "/reporting/monthly-clinic-supply-performance?month_start=2026-08-01",
      undefined,
      { authRequired: true },
    );
  });

  it("sin mes, deja que la API elija el más reciente (límite)", async () => {
    mockedRequestJson.mockResolvedValue({ month_start: "2026-08-01", clinics: [] });
    await getMonthlyClinicSupplyPerformance();
    expect(mockedRequestJson.mock.calls[0][0]).toBe("/reporting/monthly-clinic-supply-performance");
  });

  it("devuelve null ante un 404 (mes sin informe) y propaga cualquier otro error (fallo)", async () => {
    mockedRequestJson.mockRejectedValueOnce(new ApiRequestError("No computed report for that month.", 404));
    await expect(getMonthlyClinicSupplyPerformance("2026-07-01")).resolves.toBeNull();

    mockedRequestJson.mockRejectedValueOnce(new ApiRequestError("El servidor tuvo un problema inesperado.", 500));
    await expect(getMonthlyClinicSupplyPerformance("2026-07-01")).rejects.toThrow("El servidor tuvo un problema inesperado.");
  });
});

describe("getLatestPipelineRun", () => {
  it("devuelve null si el pipeline nunca ha corrido (404)", async () => {
    mockedRequestJson.mockRejectedValueOnce(new ApiRequestError("The pipeline has never run.", 404));
    await expect(getLatestPipelineRun()).resolves.toBeNull();
  });
});
