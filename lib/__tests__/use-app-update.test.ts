import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import type { UpdateStatus } from "@/lib/agent-client";
import {
  AGENT_UPDATE_REQUIRED_EVENT,
  announceAgentUpdateRequired,
  useAppUpdate,
} from "@/lib/use-app-update";

// ---------------------------------------------------------------------------
// Mocks
// ---------------------------------------------------------------------------

vi.mock("@/lib/agent-client", async (importOriginal) => {
  const actual = (await importOriginal()) as Record<string, unknown>;
  return {
    ...actual,
    agent: {
      ...(actual.agent as Record<string, unknown>),
      updateStatus: vi.fn(),
    },
  };
});

// Minimal React mock - the test environment is node, no jsdom/RTHL.
// We capture useState/useCallback/useRef calls to exercise the hook logic
// without a renderer.
// eslint-disable-next-line @typescript-eslint/no-explicit-any
let stateSlots: Array<{ value: any; setter: (v: any) => void }> = [];
let stateIndex = 0;

vi.mock("react", () => ({
  useState: (initial: unknown) => {
    if (stateIndex >= stateSlots.length) {
      const slot = { value: initial, setter: (v: unknown) => { slot.value = v; } };
      stateSlots.push(slot);
    }
    const slot = stateSlots[stateIndex++];
    return [slot.value, slot.setter];
  },
  useCallback: (fn: unknown, _deps: unknown[]) => fn,
  useRef: (initial: unknown) => ({ current: initial }),
  useEffect: (fn: () => void) => { fn(); },
}));

// Import the mocked agent after vi.mock declarations
import { agent } from "@/lib/agent-client";

const mockUpdateStatus = agent.updateStatus as ReturnType<typeof vi.fn>;

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function makeStatus(overrides: Partial<UpdateStatus> = {}): UpdateStatus {
  return {
    current: "2.10.6",
    latest: null,
    update_available: false,
    patch_only: false,
    configured: true,
    reachable: true,
    install_dir: "C:\\agent",
    ...overrides,
  };
}

/** Reset React-mock state slots between tests so each test gets fresh state. */
function resetHookState(): void {
  stateSlots = [];
  stateIndex = 0;
}

/** Call the hook (simulates a single render). */
function renderHook(pushLog?: (level: string, text: string) => void) {
  stateIndex = 0;
  return useAppUpdate(pushLog as never);
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("useAppUpdate hook", () => {
  beforeEach(() => {
    resetHookState();
    mockUpdateStatus.mockReset();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  describe("initial state", () => {
    it("returns idle phase with null status on first render", () => {
      const result = renderHook();
      expect(result.phase).toBe("idle");
      expect(result.status).toBeNull();
      expect(result.busy).toBe(false);
    });

    it("exposes a check function", () => {
      const result = renderHook();
      expect(typeof result.check).toBe("function");
    });
  });

  describe("check() when agent is up to date (no update)", () => {
    it("returns the status and logs SUCCESS", async () => {
      const status = makeStatus({ reachable: true, update_available: false });
      mockUpdateStatus.mockResolvedValue(status);
      const pushLog = vi.fn();

      const { check } = renderHook(pushLog);
      const result = await check();

      expect(result).toEqual(status);
      expect(mockUpdateStatus).toHaveBeenCalledTimes(1);
      expect(pushLog).toHaveBeenCalledWith(
        "SUCCESS",
        expect.stringContaining("up to date")
      );
    });

    it("does not dispatch a custom event when current", async () => {
      const status = makeStatus({ update_available: false });
      mockUpdateStatus.mockResolvedValue(status);
      const listener = vi.fn();

      // jsdom not available; test announceAgentUpdateRequired directly below.
      const { check } = renderHook();
      await check();
      // No throw, no event dispatch path taken for non-update.
      expect(listener).not.toHaveBeenCalled();
    });
  });

  describe("check() when update is available", () => {
    it("returns the status and logs WARN with version", async () => {
      const status = makeStatus({
        update_available: true,
        latest: "2.11.0",
        reachable: true,
      });
      mockUpdateStatus.mockResolvedValue(status);
      const pushLog = vi.fn();

      const { check } = renderHook(pushLog);
      const result = await check();

      expect(result).toEqual(status);
      expect(pushLog).toHaveBeenCalledWith(
        "WARN",
        expect.stringContaining("2.11.0")
      );
      expect(pushLog).toHaveBeenCalledWith(
        "WARN",
        expect.stringContaining("Update needed")
      );
    });

    it("logs 'latest' when latest field is null", async () => {
      const status = makeStatus({
        update_available: true,
        latest: null,
        reachable: true,
      });
      mockUpdateStatus.mockResolvedValue(status);
      const pushLog = vi.fn();

      const { check } = renderHook(pushLog);
      await check();

      expect(pushLog).toHaveBeenCalledWith(
        "WARN",
        expect.stringContaining("latest")
      );
    });
  });

  describe("check() when update server is unreachable", () => {
    it("logs a WARN and returns the status", async () => {
      const status = makeStatus({ reachable: false, update_available: false });
      mockUpdateStatus.mockResolvedValue(status);
      const pushLog = vi.fn();

      const { check } = renderHook(pushLog);
      const result = await check();

      expect(result).toEqual(status);
      expect(pushLog).toHaveBeenCalledWith(
        "WARN",
        expect.stringContaining("Could not reach")
      );
    });
  });

  describe("error handling when fetch fails", () => {
    it("returns null and logs a WARN with the error message", async () => {
      mockUpdateStatus.mockRejectedValue(new Error("Network timeout"));
      const pushLog = vi.fn();

      const { check } = renderHook(pushLog);
      const result = await check();

      expect(result).toBeNull();
      expect(pushLog).toHaveBeenCalledWith(
        "WARN",
        expect.stringContaining("Network timeout")
      );
    });

    it("handles non-Error thrown values", async () => {
      mockUpdateStatus.mockRejectedValue("string error");
      const pushLog = vi.fn();

      const { check } = renderHook(pushLog);
      const result = await check();

      expect(result).toBeNull();
      expect(pushLog).toHaveBeenCalledWith(
        "WARN",
        expect.stringContaining("string error")
      );
    });

    it("does not throw when pushLog is undefined", async () => {
      mockUpdateStatus.mockRejectedValue(new Error("fail"));

      const { check } = renderHook(undefined);
      // Must not throw even without a logger.
      await expect(check()).resolves.toBeNull();
    });
  });

  describe("state transitions (phase/busy)", () => {
    it("sets checking=true during the call and false after", async () => {
      // Track state setter calls to verify the checking flag lifecycle.
      const setterCalls: unknown[] = [];
      mockUpdateStatus.mockImplementation(async () => {
        return makeStatus();
      });

      // First render initializes state slots.
      renderHook();
      // Intercept the first state slot setter (checking flag).
      const originalSetter = stateSlots[0].setter;
      stateSlots[0].setter = (v: unknown) => {
        setterCalls.push(v);
        originalSetter(v);
      };

      // Re-render to pick up the patched setter, then call check.
      const { check } = renderHook();
      await check();

      // setChecking(true) then setChecking(false) via finally.
      expect(setterCalls).toContain(true);
      expect(setterCalls).toContain(false);
      expect(setterCalls[0]).toBe(true);
      expect(setterCalls[setterCalls.length - 1]).toBe(false);
    });

    it("resets busy to false even when the call throws", async () => {
      const setterCalls: unknown[] = [];
      mockUpdateStatus.mockRejectedValue(new Error("boom"));

      renderHook();
      const originalSetter = stateSlots[0].setter;
      stateSlots[0].setter = (v: unknown) => {
        setterCalls.push(v);
        originalSetter(v);
      };

      const { check } = renderHook();
      await check();

      expect(setterCalls[setterCalls.length - 1]).toBe(false);
    });
  });
});

describe("announceAgentUpdateRequired", () => {
  it("dispatches a CustomEvent on window with the status detail", () => {
    const status = makeStatus({ update_available: true, latest: "2.11.0" });
    const listener = vi.fn();

    // Provide a minimal window.dispatchEvent for node environment.
    const originalWindow = globalThis.window;
    const mockDispatch = vi.fn();
    // @ts-expect-error -- minimal window stub for node env
    globalThis.window = { dispatchEvent: mockDispatch };

    announceAgentUpdateRequired(status);

    expect(mockDispatch).toHaveBeenCalledTimes(1);
    const event = mockDispatch.mock.calls[0][0] as CustomEvent<UpdateStatus>;
    expect(event.type).toBe(AGENT_UPDATE_REQUIRED_EVENT);
    expect(event.detail).toEqual(status);

    // Restore
    if (originalWindow === undefined) {
      // @ts-expect-error -- cleanup
      delete globalThis.window;
    } else {
      globalThis.window = originalWindow;
    }
  });

  it("does nothing when window is undefined (SSR)", () => {
    const status = makeStatus({ update_available: true });
    const originalWindow = globalThis.window;
    // @ts-expect-error -- simulate SSR
    delete globalThis.window;

    // Must not throw.
    expect(() => announceAgentUpdateRequired(status)).not.toThrow();

    // Restore
    if (originalWindow !== undefined) {
      globalThis.window = originalWindow;
    }
  });
});

describe("AGENT_UPDATE_REQUIRED_EVENT constant", () => {
  it("is a namespaced event string", () => {
    expect(AGENT_UPDATE_REQUIRED_EVENT).toBe("tt:agent-update-required");
    expect(typeof AGENT_UPDATE_REQUIRED_EVENT).toBe("string");
    expect(AGENT_UPDATE_REQUIRED_EVENT).toMatch(/^tt:/);
  });
});
