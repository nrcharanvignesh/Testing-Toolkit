"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { agent, type UpdateStatus } from "./agent-client";

type Pushed = (
  level: "INFO" | "SUCCESS" | "WARN" | "ERROR",
  text: string
) => void;

export const AGENT_UPDATE_REQUIRED_EVENT = "tt:agent-update-required";

/** Notify the shell that version detection found a newer agent. */
export function announceAgentUpdateRequired(status: UpdateStatus) {
  if (typeof window === "undefined") return;
  window.dispatchEvent(
    new CustomEvent<UpdateStatus>(AGENT_UPDATE_REQUIRED_EVENT, {
      detail: status,
    })
  );
}

/**
 * Detection-only agent update policy.
 *
 * This hook never configures, downloads, applies, polls, or restarts the local
 * agent. A newer version is announced to AppShell, which pauses the app and
 * presents the single supported upgrade path: reinstalling the agent while
 * preserving user data and completed onboarding.
 */
export function useAppUpdate(pushLog?: Pushed): {
  phase: "checking" | "idle";
  status: UpdateStatus | null;
  check: () => Promise<UpdateStatus | null>;
  busy: boolean;
} {
  const [checking, setChecking] = useState(false);
  const [status, setStatus] = useState<UpdateStatus | null>(null);
  const pushLogRef = useRef(pushLog);
  useEffect(() => { pushLogRef.current = pushLog; }, [pushLog]);

  const log: Pushed = useCallback(
    (level, text) => pushLogRef.current?.(level, text),
    []
  );

  const check = useCallback(async (): Promise<UpdateStatus | null> => {
    setChecking(true);
    try {
      const next = await agent.updateStatus();
      setStatus(next);
      if (next.update_available && next.patch_only) {
        log("INFO", `Patch v${next.latest} available. Applying...`);
        try {
          const result = await agent.applyPatch();
          if (result.ok) {
            log("SUCCESS", `Patch v${result.version} applied. Reconnecting...`);
            setTimeout(() => window.location.reload(), 3000);
          } else {
            log("WARN", `Patch failed: ${result.error}. Update required.`);
            announceAgentUpdateRequired(next);
          }
        } catch {
          log("WARN", "Patch apply failed. Update required.");
          announceAgentUpdateRequired(next);
        }
      } else if (next.update_available) {
        log("WARN", `Agent v${next.latest ?? "latest"} is required. Update needed.`);
        announceAgentUpdateRequired(next);
      } else if (next.reachable) {
        log("SUCCESS", `Agent v${next.current} is up to date.`);
      } else {
        log("WARN", "Could not reach the update server. No changes were made.");
      }
      return next;
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      log("WARN", `Could not check for agent updates: ${msg}`);
      return null;
    } finally {
      setChecking(false);
    }
  }, [log]);

  return {
    phase: checking ? ("checking" as const) : ("idle" as const),
    status,
    check,
    busy: checking,
  };
}
