import { act } from "react";
import { createRoot, type Root } from "react-dom/client";

import { useRfpEvents, type RfpStreamState, type UseRfpEventsOptions } from "../hooks/useRfpEvents";
import { RfpStreamAuthError, streamRfpEvents, type StreamRfpEventsOptions } from "../services/rfpEvents";
import { redirectToLogin } from "../services/http";

jest.mock("../services/rfpEvents", () => {
  const actual = jest.requireActual("../services/rfpEvents");
  return { ...actual, streamRfpEvents: jest.fn() };
});
jest.mock("../services/http", () => ({ redirectToLogin: jest.fn(), apiUrl: (p: string) => p }));

const streamMock = streamRfpEvents as jest.MockedFunction<typeof streamRfpEvents>;

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

let container: HTMLDivElement;
let root: Root;
let latest: RfpStreamState;

function Probe(props: UseRfpEventsOptions) {
  latest = useRfpEvents(props);
  return null;
}

beforeEach(() => {
  jest.useFakeTimers();
  jest.spyOn(Math, "random").mockReturnValue(0.5); // backoff sin azar: 1 s, 2 s, 4 s…
  container = document.createElement("div");
  root = createRoot(container);
  streamMock.mockReset();
});

afterEach(() => {
  act(() => root.unmount());
  jest.useRealTimers();
  jest.restoreAllMocks();
});

/** Conexión que abre y luego se corta (red caída). */
function openThenDrop(options: StreamRfpEventsOptions): Promise<void> {
  options.onOpen();
  return Promise.reject(new Error("network"));
}

it("al cortarse espera con backoff, reconecta y avisa de que es una reconexión", async () => {
  const onOpen = jest.fn();
  streamMock
    .mockImplementationOnce(openThenDrop)
    .mockImplementationOnce(async () => Promise.reject(new Error("network")))
    .mockImplementationOnce((options) => {
      options.onOpen();
      return new Promise(() => {}); // conexión estable
    });

  await act(async () => root.render(<Probe onOpen={onOpen} onTicketCreated={jest.fn()} />));
  expect(onOpen).toHaveBeenNthCalledWith(1, false);
  expect(latest).toEqual({ status: "reconnecting", retryInMs: 1000 });

  // Tras una conexión buena el contador vuelve a 0: el primer fallo espera 1 s…
  await act(async () => jest.advanceTimersByTime(1000));
  expect(streamMock).toHaveBeenCalledTimes(2);
  // …y el segundo seguido, 2 s.
  expect(latest).toEqual({ status: "reconnecting", retryInMs: 2000 });
  await act(async () => jest.advanceTimersByTime(1999));
  expect(streamMock).toHaveBeenCalledTimes(2);
  await act(async () => jest.advanceTimersByTime(1));

  expect(streamMock).toHaveBeenCalledTimes(3);
  expect(onOpen).toHaveBeenNthCalledWith(2, true);
  expect(latest.status).toBe("live");
});

it("el evento 'online' del navegador adelanta el reintento", async () => {
  streamMock.mockImplementationOnce(openThenDrop).mockImplementationOnce(() => new Promise(() => {}));
  await act(async () => root.render(<Probe onOpen={jest.fn()} onTicketCreated={jest.fn()} />));
  expect(latest.status).toBe("reconnecting");

  await act(async () => window.dispatchEvent(new Event("online")));
  expect(streamMock).toHaveBeenCalledTimes(2);
});

it("un 401 no se reintenta: lleva al login", async () => {
  streamMock.mockImplementationOnce(async () => Promise.reject(new RfpStreamAuthError()));
  await act(async () => root.render(<Probe onOpen={jest.fn()} onTicketCreated={jest.fn()} />));
  await act(async () => jest.advanceTimersByTime(60000));

  expect(redirectToLogin).toHaveBeenCalledTimes(1);
  expect(streamMock).toHaveBeenCalledTimes(1);
});

it("al desmontar cierra la conexión y no reintenta", async () => {
  let signal: AbortSignal | undefined;
  streamMock.mockImplementationOnce((options) => {
    signal = options.signal;
    return new Promise(() => {});
  });
  await act(async () => root.render(<Probe onOpen={jest.fn()} onTicketCreated={jest.fn()} />));
  act(() => root.unmount());
  root = createRoot(container); // el afterEach vuelve a desmontar

  expect(signal?.aborted).toBe(true);
  await act(async () => jest.advanceTimersByTime(60000));
  expect(streamMock).toHaveBeenCalledTimes(1);
});
