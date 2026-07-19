/**
 * event-bus.ts
 * Captures user actions (dialog opens, button clicks, selections) and
 * batches them to the backend POST /events/batch endpoint.
 */

const AGENT_URL = "http://127.0.0.1:7842";
const FLUSH_INTERVAL_MS = 3000;
const MAX_BATCH_SIZE = 50;

interface TraceEvent {
  event_type: string;
  source: string;
  action: string;
  user_context: string;
  duration_ms: number | null;
  metadata: Record<string, unknown>;
  client_ts: number;
}

const buffer: TraceEvent[] = [];
let timer: ReturnType<typeof setTimeout> | null = null;

function flush(): void {
  if (buffer.length === 0) return;
  const batch = buffer.splice(0, buffer.length);
  fetch(`${AGENT_URL}/events/batch`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ events: batch }),
  }).catch(() => {
    // Agent offline - drop silently. Trace data is best-effort.
  });
}

function scheduleFlush(): void {
  if (timer !== null) return;
  timer = setTimeout(() => {
    timer = null;
    flush();
  }, FLUSH_INTERVAL_MS);
}

export function trackEvent(
  eventType: "user_action" | "state_change" | "system_event",
  source: string,
  action: string,
  extra?: {
    userContext?: string;
    durationMs?: number;
    metadata?: Record<string, unknown>;
  },
): void {
  buffer.push({
    event_type: eventType,
    source: source,
    action: action,
    user_context: extra?.userContext ?? "",
    duration_ms: extra?.durationMs ?? null,
    metadata: extra?.metadata ?? {},
    client_ts: Date.now(),
  });
  if (buffer.length >= MAX_BATCH_SIZE) {
    flush();
  } else {
    scheduleFlush();
  }
}

if (typeof window !== "undefined") {
  window.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "hidden") flush();
  });
  window.addEventListener("beforeunload", flush);
}
