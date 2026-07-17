import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";

// Stub fetch before importing the module (side-effects attach listeners)
const fetchMock = vi.fn(() => Promise.resolve(new Response("ok")));
vi.stubGlobal("fetch", fetchMock);

// Minimal window/document stubs for the visibilitychange/beforeunload listeners
const listeners: Record<string, Function[]> = {};
vi.stubGlobal("window", {
  addEventListener: (evt: string, fn: Function) => {
    listeners[evt] = listeners[evt] || [];
    listeners[evt].push(fn);
  },
});
vi.stubGlobal("document", { visibilityState: "visible" });

// Now import - side effects will bind to stubs above
import { trackEvent } from "../event-bus";

describe("event-bus > trackEvent", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    fetchMock.mockClear();
  });

  afterEach(() => {
    // Drain any pending module-level timer so `timer` resets to null
    vi.runAllTimers();
    vi.useRealTimers();
  });

  it("adds event to buffer with correct shape", () => {
    const now = Date.now();
    trackEvent("user_action", "dialog", "open", {
      userContext: "board-view",
      durationMs: 120,
      metadata: { id: "abc" },
    });

    // Advance timer to flush
    vi.advanceTimersByTime(3000);

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const body = JSON.parse(fetchMock.mock.calls[0][1]!.body as string);
    const evt = body.events[0];

    expect(evt.event_type).toBe("user_action");
    expect(evt.source).toBe("dialog");
    expect(evt.action).toBe("open");
    expect(evt.user_context).toBe("board-view");
    expect(evt.duration_ms).toBe(120);
    expect(evt.metadata).toEqual({ id: "abc" });
    expect(evt.client_ts).toBeGreaterThanOrEqual(now);
  });

  it("defaults userContext to empty string, durationMs to null, metadata to {}", () => {
    trackEvent("state_change", "store", "update");
    vi.advanceTimersByTime(3000);

    const body = JSON.parse(fetchMock.mock.calls[0][1]!.body as string);
    const evt = body.events[0];

    expect(evt.user_context).toBe("");
    expect(evt.duration_ms).toBeNull();
    expect(evt.metadata).toEqual({});
  });

  it("auto-flushes at MAX_BATCH_SIZE (50 events)", () => {
    for (let i = 0; i < 50; i++) {
      trackEvent("system_event", "perf", `tick-${i}`);
    }

    // Should flush synchronously at 50 - no timer advance needed
    expect(fetchMock).toHaveBeenCalledTimes(1);
    const body = JSON.parse(fetchMock.mock.calls[0][1]!.body as string);
    expect(body.events).toHaveLength(50);
  });

  it("timer-based flush fires after 3000ms", () => {
    trackEvent("user_action", "btn", "click");

    // Not flushed yet
    expect(fetchMock).not.toHaveBeenCalled();

    vi.advanceTimersByTime(2999);
    expect(fetchMock).not.toHaveBeenCalled();

    vi.advanceTimersByTime(1);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("calls fetch with correct URL, method, headers, and body", () => {
    trackEvent("user_action", "menu", "select");
    vi.advanceTimersByTime(3000);

    expect(fetchMock).toHaveBeenCalledWith(
      "http://127.0.0.1:7842/events/batch",
      expect.objectContaining({
        method: "POST",
        headers: { "Content-Type": "application/json" },
      }),
    );

    const body = JSON.parse(fetchMock.mock.calls[0][1]!.body as string);
    expect(body).toHaveProperty("events");
    expect(Array.isArray(body.events)).toBe(true);
  });

  it("fetch failure does not throw", () => {
    fetchMock.mockImplementationOnce(() => Promise.reject(new Error("offline")));
    trackEvent("user_action", "nav", "back");
    vi.advanceTimersByTime(3000);

    // No exception - test passes if we reach here
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("multiple trackEvent calls before flush accumulate in one batch", () => {
    trackEvent("user_action", "a", "1");
    trackEvent("state_change", "b", "2");
    trackEvent("system_event", "c", "3");

    vi.advanceTimersByTime(3000);

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const body = JSON.parse(fetchMock.mock.calls[0][1]!.body as string);
    expect(body.events).toHaveLength(3);
    expect(body.events[0].source).toBe("a");
    expect(body.events[1].source).toBe("b");
    expect(body.events[2].source).toBe("c");
  });
});
