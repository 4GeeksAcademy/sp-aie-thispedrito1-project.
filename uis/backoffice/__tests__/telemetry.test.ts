/**
 * Servicio de telemetría del frontend (services/telemetry.ts). Bug real que
 * motiva este test: ENDPOINT se resolvía siempre a "" en el navegador porque
 * el código leía la variable de entorno a través de `globalThis.process`,
 * un patrón que Next.js no sabe inlinear en build time (solo reconoce el
 * literal `process.env.X`) — sendBatch trataba ese "" como éxito silencioso
 * y ningún evento llegaba nunca al backend, sin ningún error visible.
 *
 * ENDPOINT se calcula una sola vez al cargar el módulo, así que cada test
 * fija process.env antes de importar con jest.resetModules() + import()
 * dinámico, para simular cómo Next.js inlinearía el valor en cada build.
 */

const ENDPOINT_URL = "http://localhost:8000/telemetry/events";
const originalEndpoint = process.env.NEXT_PUBLIC_TELEMETRY_ENDPOINT;

afterEach(() => {
  process.env.NEXT_PUBLIC_TELEMETRY_ENDPOINT = originalEndpoint;
  jest.resetModules();
  jest.restoreAllMocks();
});

describe("track", () => {
  it("envía el batch al NEXT_PUBLIC_TELEMETRY_ENDPOINT configurado (camino feliz)", async () => {
    process.env.NEXT_PUBLIC_TELEMETRY_ENDPOINT = ENDPOINT_URL;
    jest.resetModules();

    const fetchMock = jest.fn().mockResolvedValue({ ok: true });
    global.fetch = fetchMock as unknown as typeof fetch;

    const { track } = await import("../services/telemetry");
    for (let i = 0; i < 20; i++) {
      track("page_viewed", { route_template: "/inventory/products" });
    }
    // El flush del lote lleno es fire-and-forget (track no devuelve promesa);
    // una vuelta de microtareas alcanza para que sendBatch -> fetch corra.
    await new Promise((resolve) => setTimeout(resolve, 0));

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, options] = fetchMock.mock.calls[0];
    expect(url).toBe(ENDPOINT_URL);
    const body = JSON.parse(options.body);
    expect(body.events).toHaveLength(20);
    expect(body.events[0].event_type).toBe("page_viewed");
  });

  it("no llama a fetch cuando NEXT_PUBLIC_TELEMETRY_ENDPOINT no está configurado (modo de fallo)", async () => {
    delete process.env.NEXT_PUBLIC_TELEMETRY_ENDPOINT;
    jest.resetModules();

    const fetchMock = jest.fn();
    global.fetch = fetchMock as unknown as typeof fetch;

    const { track } = await import("../services/telemetry");
    for (let i = 0; i < 20; i++) {
      track("page_viewed", { route_template: "/inventory/products" });
    }
    await new Promise((resolve) => setTimeout(resolve, 0));

    expect(fetchMock).not.toHaveBeenCalled();
  });
});
