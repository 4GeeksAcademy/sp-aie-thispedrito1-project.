import { getCurrentUserId } from "./session";
import type { TelemetryEnvelope } from "../types/telemetry";

const SCHEMA_VERSION = "1.0.0";
const FLUSH_INTERVAL_MS = 10_000;
const MAX_QUEUE_SIZE = 20;
const MAX_RETRIES = 3;
const RETRY_BASE_DELAY_MS = 500;

const ENDPOINT =
  (globalThis as { process?: { env?: { NEXT_PUBLIC_TELEMETRY_ENDPOINT?: string } } }).process?.env
    ?.NEXT_PUBLIC_TELEMETRY_ENDPOINT || "";

const SESSION_STORAGE_KEY = "healthcore.telemetry.sessionId";

let queue: TelemetryEnvelope[] = [];
let flushTimer: ReturnType<typeof setInterval> | null = null;
let cachedSessionId: string | null = null;

function getSessionId(): string {
  if (cachedSessionId) return cachedSessionId;
  if (typeof window === "undefined") {
    return crypto.randomUUID();
  }
  const stored = window.sessionStorage.getItem(SESSION_STORAGE_KEY);
  if (stored) {
    cachedSessionId = stored;
    return stored;
  }
  const fresh = crypto.randomUUID();
  window.sessionStorage.setItem(SESSION_STORAGE_KEY, fresh);
  cachedSessionId = fresh;
  return fresh;
}

function buildEvent(eventType: string, properties: Record<string, unknown>): TelemetryEnvelope {
  return {
    eventId: crypto.randomUUID(),
    timestamp: new Date().toISOString(),
    sessionId: getSessionId(),
    userId: getCurrentUserId(),
    event_type: eventType,
    schemaVersion: SCHEMA_VERSION,
    requestId: crypto.randomUUID(),
    properties,
  };
}

function delay(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function sendBatch(batch: TelemetryEnvelope[]): Promise<boolean> {
  if (!ENDPOINT || batch.length === 0) return true;
  try {
    const response = await fetch(ENDPOINT, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ events: batch }),
      keepalive: true,
    });
    return response.ok;
  } catch {
    return false;
  }
}

// Called right after a sendBatch() attempt has failed for `batch`, with `attempt` = how many
// attempts have happened so far (sendBatch was already tried once by the caller below).
async function retryFlush(batch: TelemetryEnvelope[], attempt: number): Promise<void> {
  if (attempt >= MAX_RETRIES) return; // batch discarded — matches the "then discard" part of the spec

  await delay(RETRY_BASE_DELAY_MS * 2 ** (attempt - 1)); // 500ms, 1000ms, 2000ms
  const ok = await sendBatch(batch);
  if (!ok) await retryFlush(batch, attempt + 1);
}

async function flush(): Promise<void> {
  if (queue.length === 0) return;
  const batch = queue;
  queue = [];
  const ok = await sendBatch(batch);
  if (!ok) await retryFlush(batch, 1);
}

function flushOnHide(): void {
  if (queue.length === 0 || typeof navigator === "undefined" || !navigator.sendBeacon || !ENDPOINT) return;
  const batch = queue;
  queue = [];
  const blob = new Blob([JSON.stringify({ events: batch })], { type: "application/json" });
  const sent = navigator.sendBeacon(ENDPOINT, blob);
  if (!sent) {
    // sendBeacon queues asynchronously and can't be retried after the fact once it returns
    // false — put the batch back so the next flush (or the next visibilitychange) tries again.
    queue = batch.concat(queue);
  }
}

function ensureFlushTimer(): void {
  if (flushTimer !== null || typeof window === "undefined") return;
  flushTimer = setInterval(() => {
    void flush();
  }, FLUSH_INTERVAL_MS);
}

function initListeners(): void {
  if (typeof document === "undefined") return;
  ensureFlushTimer();
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "hidden") flushOnHide();
  });
}

initListeners();

export function track(eventType: string, properties: Record<string, unknown>): void {
  queue.push(buildEvent(eventType, properties));
  if (queue.length >= MAX_QUEUE_SIZE) void flush();
}
