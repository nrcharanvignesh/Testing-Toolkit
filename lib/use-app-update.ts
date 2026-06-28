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
import { compareVersions } from "./agent-version";

type Pushed = (level: "INFO" | "SUCCESS" | "WARN" | "ERROR", text: string) => void;

export type UpdatePhase =
  | "idle"
  | "checking"
  | "applying"
  | "restarting"
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

  /**
   * Make auto-update self-sufficient: if the agent isn't configured for updates
   * (token-less / older install), fetch the read-only update token from the
   * SSO-protected web app and hand it to the agent's /update/config. This is the
   * key to fully autonomous updates — afterwards the agent's own 60s poller and
   * the on-refresh check both work, with no reinstall and no human step.
   * Returns the (possibly refreshed) status. Always best-effort.
   */
  const ensureConfigured = useCallback(
    async (current?: UpdateStatus | null): Promise<UpdateStatus | null> => {
      const s = current ?? (await check());
      if (!s || s.configured) return s; // already self-updating, or can't tell
      try {
        const res = await fetch("/api/agent-update/config", {
          cache: "no-store",
        });
        if (!res.ok) return s; // web app has no token configured server-side
        const cfg = await res.json();
        if (!cfg?.token) return s;
        const healed = await agent.configureUpdate({
          token: cfg.token,
          repo: cfg.repo,
          ref: cfg.ref,
          manifest_url: cfg.manifest_url,
        });
        if (healed) {
          setStatus(healed);
          log("INFO", "Auto-update enabled for this install.");
          return healed;
        }
      } catch {
        // Network / agent hiccup — leave as-is; we retry on the next check.
      }
      return s;
    },
    [check, log]
  );

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
      if (!back) {
        log(
          "WARN",
          "Update applied, but the agent is taking a while to restart. " +
            "Reload the page in a moment."
        );
        return true;
      }

      // Post-apply verification: confirm the restarted agent actually reports
      // the expected version. If it didn't advance, the apply silently didn't
      // take effect — treat that as a failure so the caller can fall through to
      // the block-and-reinstall path instead of believing we're up to date.
      if (r.latest) {
        try {
          const h = await agent.health();
          if (compareVersions(h.version, r.latest) < 0) {
            log(
              "ERROR",
              `Update did not take effect (agent still v${h.version}, expected v${r.latest}).`
            );
            setPhase("idle");
            return false;
          }
        } catch {
          // Couldn't read health to verify — fall through and reload anyway;
          // the next launch's handshake will re-evaluate.
        }
      }

      log("SUCCESS", "Update applied. Reloading the app...");
      await new Promise((r) => setTimeout(r, 600));
      if (typeof window !== "undefined") window.location.reload();
      return true;
    } catch (e) {
      log("ERROR", `Update failed: ${(e as Error).message}`);
      setPhase("idle");
      return false;
    }
  }, [log, waitForReconnect]);

  return {
    phase,
    status,
    check,
    apply,
    ensureConfigured,
    busy: phase !== "idle" && phase !== "done",
  };
}
