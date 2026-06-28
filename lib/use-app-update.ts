"use client";

/**
 * use-app-update.ts
 * Shared "refresh the app for new patches" flow used by both the sidebar
 * top button and the Settings > Installation section.
 *
 * The local Python agent self-updates from a GitHub manifest. Triggering it
 * on demand downloads the latest source, applies it, and restarts the agent.
 * After a successful apply we poll /health until the freshly-restarted agent
 * answers again, then reload the page so the browser picks up any frontend
 * changes too.
 */

import { useCallback, useState } from "react";
import { agent, type UpdateStatus } from "./agent-client";
import { setTourCompletedPref, setPendingReindexPref } from "./preferences";

type Pushed = (level: "INFO" | "SUCCESS" | "WARN" | "ERROR", text: string) => void;

export type UpdatePhase =
  | "idle"
  | "checking"
  | "applying"
  | "restarting"
  | "reinstalling"
  | "done";

export function useAppUpdate(pushLog?: Pushed) {
  const [phase, setPhase] = useState<UpdatePhase>("idle");
  const [status, setStatus] = useState<UpdateStatus | null>(null);

  const log: Pushed = useCallback(
    (level, text) => pushLog?.(level, text),
    [pushLog]
  );

  /** Non-destructive version check. Returns the status (also stored). */
  const check = useCallback(async (): Promise<UpdateStatus | null> => {
    setPhase("checking");
    try {
      const s = await agent.updateStatus();
      setStatus(s);
      return s;
    } catch (e) {
      log("WARN", `Could not check for updates: ${(e as Error).message}`);
      return null;
    } finally {
      setPhase((p) => (p === "checking" ? "idle" : p));
    }
  }, [log]);

  /** Wait until the agent answers /health again after a restart. */
  const waitForReconnect = useCallback(async (timeoutMs = 60000) => {
    const start = Date.now();
    // Give the old process a moment to exit before we start polling.
    await new Promise((r) => setTimeout(r, 1500));
    while (Date.now() - start < timeoutMs) {
      const ok = await agent.checkConnection();
      if (ok === "connected") return true;
      await new Promise((r) => setTimeout(r, 1500));
    }
    return false;
  }, []);

  /**
   * Apply the latest patch. Returns true if an update was applied (and the app
   * is about to reload), false otherwise (already current / not configured).
   */
  const apply = useCallback(async (): Promise<boolean> => {
    setPhase("applying");
    log("INFO", "Checking for the latest patch...");
    try {
      const r = await agent.applyUpdate();
      if (r.status === "not_configured") {
        log("WARN", "Automatic updates are not configured for this install.");
        setPhase("idle");
        return false;
      }
      if (r.status === "unreachable") {
        log("WARN", "Could not reach the update server. Check your connection.");
        setPhase("idle");
        return false;
      }
      if (r.status === "failed") {
        log("ERROR", "Update failed to apply. The agent kept the current version.");
        setPhase("idle");
        return false;
      }
      if (!r.applied || r.status === "up_to_date") {
        log("SUCCESS", `You're already on the latest version (v${r.current}).`);
        setPhase("idle");
        return false;
      }

      // Applied -> the agent is restarting.
      log("INFO", `Updating to v${r.latest ?? "latest"}; restarting the agent...`);
      setPhase("restarting");
      const back = await waitForReconnect();
      setPhase("done");
      if (back) {
        log("SUCCESS", "Update applied. Reloading the app...");
        await new Promise((r) => setTimeout(r, 600));
        if (typeof window !== "undefined") window.location.reload();
      } else {
        log(
          "WARN",
          "Update applied, but the agent is taking a while to restart. " +
            "Reload the page in a moment."
        );
      }
      return true;
    } catch (e) {
      log("ERROR", `Update failed: ${(e as Error).message}`);
      setPhase("idle");
      return false;
    }
  }, [log, waitForReconnect]);

  /**
   * Full reinstall (distinct from apply/refresh). Marks onboarding to run again
   * and flags a KB re-index for after the restart, then reinstalls the agent
   * and reloads. Settings, fetched models and UI preferences are kept; only the
   * guided tour is reset and the vector indexes are rebuilt on next launch.
   */
  const reinstall = useCallback(async (): Promise<boolean> => {
    setPhase("reinstalling");
    log("INFO", "Reinstalling the Testing Toolkit agent...");
    // Persist the post-restart intentions BEFORE the agent goes down so they
    // survive the reload even if the connection drops mid-flight.
    setTourCompletedPref(false);
    setPendingReindexPref(true);
    try {
      const r = await agent.reinstall();
      if (r.status === "not_configured") {
        log("WARN", "Reinstall is not configured for this install.");
        setPendingReindexPref(false);
        setPhase("idle");
        return false;
      }
      if (r.status === "unreachable") {
        log("WARN", "Could not reach the install server. Check your connection.");
        setPendingReindexPref(false);
        setPhase("idle");
        return false;
      }
      if (r.status === "failed") {
        log("ERROR", "Reinstall failed. The agent kept the current version.");
        setPendingReindexPref(false);
        setPhase("idle");
        return false;
      }

      log("INFO", "Agent reinstalled; restarting...");
      setPhase("restarting");
      const back = await waitForReconnect(120000);
      setPhase("done");
      if (back) {
        log("SUCCESS", "Reinstall complete. Reloading the app...");
        await new Promise((r) => setTimeout(r, 600));
        if (typeof window !== "undefined") window.location.reload();
      } else {
        log(
          "WARN",
          "Reinstall applied, but the agent is taking a while to restart. " +
            "Reload the page in a moment."
        );
      }
      return true;
    } catch (e) {
      log("ERROR", `Reinstall failed: ${(e as Error).message}`);
      setPendingReindexPref(false);
      setPhase("idle");
      return false;
    }
  }, [log, waitForReconnect]);

  return {
    phase,
    status,
    check,
    apply,
    reinstall,
    busy: phase !== "idle" && phase !== "done",
  };
}
