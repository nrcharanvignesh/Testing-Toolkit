"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useAgent } from "@/lib/agent-context";
import { useAppState } from "@/lib/app-state";
import { useAppUpdate } from "@/lib/use-app-update";
import { useWebFreshness } from "@/lib/use-web-freshness";
import { isAgentOutdated, REQUIRED_AGENT_VERSION } from "@/lib/agent-version";
import {
  getPreferences,
  setPendingReindexPref,
  isFirstLaunchToday,
  markUpdateCheckedToday,
} from "@/lib/preferences";
import type { UpdateStatus } from "@/lib/agent-client";
import { AgentUpdateRequired } from "@/components/onboarding/AgentUpdateRequired";
import { AgentUpdating } from "@/components/onboarding/AgentUpdating";
import { ActivityBar } from "./ActivityBar";
import { NavPanel } from "./NavPanel";
import { StatusBar } from "./StatusBar";

import { BoardGrid } from "@/components/board/BoardGrid";
import { ActionBar } from "@/components/board/ActionBar";
import { LogPanel } from "@/components/board/LogPanel";
import { CoverageBar } from "@/components/dashboard/CoverageBar";
import { DialogHost } from "@/components/dialogs/DialogHost";

export function AppShell() {
  const { status, health } = useAgent();
  const {
    navVisible,
    logVisible,
    settings,
    reloadProjects,
    reindexAllKbs,
    pushLog,
    currentProject,
    currentBoard,
    displayName,
  } = useAppState();
  const { check, apply, ensureConfigured, progress } = useAppUpdate(pushLog);
  const bootstrapped = useRef(false);
  const reindexed = useRef(false);
  const autoUpdated = useRef(false);
  // When an agent update exists but can't be applied silently, we block the
  // whole app with AgentUpdateRequired until the user reinstalls.
  const [updateBlocked, setUpdateBlocked] = useState<UpdateStatus | null>(null);

  // (B) Keep the WEB app itself current: reload the tab when a newer deployment
  // ships. The orchestrator must be fresh for the other guarantees to hold.
  useWebFreshness();

  // The agent version reported by /health, used for the (A) handshake below.
  const agentVersion = health?.version ?? null;

  // Bootstrap: once connected & configured, load the project list.
  useEffect(() => {
    if (
      status === "connected" &&
      settings?.configured &&
      !bootstrapped.current
    ) {
      bootstrapped.current = true;
      reloadProjects();
    }
  }, [status, settings?.configured, reloadProjects]);

  // (A) Minimum-version handshake — the hard guarantee. The web app knows the
  // lowest agent version it works with (REQUIRED_AGENT_VERSION). The moment a
  // connected agent reports an older version, BLOCK the whole app immediately
  // and unconditionally — no GitHub, manifest, or update-config dependency. A
  // newer-than-required agent is fine. This runs on every connect/version change
  // and cannot be bypassed by network failures, so a stale agent is never
  // silently usable against this build.
  useEffect(() => {
    if (status !== "connected" || !agentVersion) return;
    if (isAgentOutdated(agentVersion)) {
      setUpdateBlocked((prev) =>
        prev?.current === agentVersion
          ? prev
          : {
              current: agentVersion,
              latest: REQUIRED_AGENT_VERSION,
              update_available: true,
              // We don't yet know if auto-update is configured; the check below
              // refines this. Default false so the gate shows reinstall first.
              configured: false,
              reachable: true,
              install_dir: "",
            }
      );
    }
  }, [status, agentVersion]);

  // Shared update routine. Strategy is "silent first, then block":
  //   1. If an update exists and auto-update IS configured, apply it silently —
  //      apply() restarts the agent, verifies the new version, and reloads.
  //   2. If that silent apply can't happen (not configured) or fails, the agent
  //      is out of date with the shipped patch → BLOCK with AgentUpdateRequired.
  // Nothing happens (no noise, no block) when already up to date.
  const runUpdateCheck = useCallback(async () => {
    let s = await check();
    if (!s) return; // check failed (offline / unreachable) — leave as-is
    // If this install never got an update token (token-less / older install),
    // bridge one from the SSO-protected web app so it can self-update without a
    // reinstall. This is what makes updates fully autonomous for everyone.
    if (!s.configured) {
      s = (await ensureConfigured(s)) ?? s;
    }
    if (!s.update_available) {
      // Up to date per the manifest. If a stale handshake block is showing for
      // an out-of-date agent it stays; otherwise nothing to do.
      return;
    }
    if (s.configured) {
      pushLog?.(
        "INFO",
        `New patch available (v${s.latest}). Applying automatically...`
      );
      const applied = await apply(); // reloads the page on success
      if (applied) return;
    }
    // Either not configured for auto-update, or the silent apply failed.
    pushLog?.(
      "WARN",
      "Agent changes require a reinstall to take effect. Pausing the app."
    );
    setUpdateBlocked(s);
  }, [check, apply, ensureConfigured, pushLog]);

  // Self-heal auto-update config as soon as the agent connects, once per load.
  // This bridges a read-only update token to token-less / older installs so the
  // agent's own background poller can take over — independent of whether a
  // manifest check runs this session. Fully autonomous, no reinstall, no prompt.
  const configHealed = useRef(false);
  useEffect(() => {
    if (status !== "connected" || configHealed.current) return;
    configHealed.current = true;
    void ensureConfigured();
  }, [status, ensureConfigured]);

  // When to run the manifest check: configured sessions check on every refresh.
  // On top of that, the FIRST LAUNCH OF EACH DAY always checks regardless of
  // whether the toolkit is configured yet — only a connected agent is required —
  // so shipped agent changes are never missed for days at a time.
  useEffect(() => {
    if (status !== "connected" || autoUpdated.current) return;
    if (!settings?.configured && !isFirstLaunchToday()) return;
    autoUpdated.current = true;
    markUpdateCheckedToday();
    void runUpdateCheck();
  }, [status, settings?.configured, runUpdateCheck]);

  // (C) Harden detection for long-open sessions: re-check on tab focus, when the
  // network returns, and on a periodic interval — not just at launch. These are
  // no-ops while offline or already up to date.
  useEffect(() => {
    if (status !== "connected") return;
    const recheck = () => {
      if (settings?.configured || isFirstLaunchToday()) {
        markUpdateCheckedToday();
        void runUpdateCheck();
      }
    };
    const onFocus = () => recheck();
    const onOnline = () => recheck();
    const interval = setInterval(recheck, 30 * 60 * 1000); // every 30 min
    window.addEventListener("focus", onFocus);
    window.addEventListener("online", onOnline);
    return () => {
      clearInterval(interval);
      window.removeEventListener("focus", onFocus);
      window.removeEventListener("online", onOnline);
    };
  }, [status, settings?.configured, runUpdateCheck]);

  // After a reinstall the agent restarts and the app reloads with a persisted
  // pendingReindex flag — rebuild every KB vector index once we're back online.
  useEffect(() => {
    if (
      status === "connected" &&
      settings?.configured &&
      !reindexed.current &&
      getPreferences().pendingReindex
    ) {
      reindexed.current = true;
      setPendingReindexPref(false);
      void reindexAllKbs();
    }
  }, [status, settings?.configured, reindexAllKbs]);

  return (
    <div className="flex h-full flex-col overflow-hidden">
      {/* ── Title bar ──────────────────────────────────────────────── */}
      <header
        className="flex h-9 shrink-0 items-center gap-3 border-b px-3"
        style={{
          background: "var(--tt-teal)",
          borderColor: "var(--tt-teal-dim)",
        }}
      >
        {/* Wordmark */}
        <span className="text-sm font-bold tracking-tight text-white">
          Testing Toolkit
        </span>
        {/* Breadcrumb */}
        {currentProject && (
          <>
            <span className="select-none text-white/40" aria-hidden>/</span>
            <span className="truncate text-xs font-medium text-white/80">
              {displayName(currentProject)}
            </span>
          </>
        )}
        {currentBoard && (
          <>
            <span className="text-white/40 select-none">/</span>
            <span className="truncate text-xs text-white/60">
              {currentBoard.team_name || currentBoard.name}
            </span>
          </>
        )}
        <div className="flex-1" />
        {/* Agent version badge */}
        <span
          className="rounded px-1.5 py-0.5 text-[10px] font-semibold tabular-nums"
          style={{
            background: "rgba(0,0,0,0.22)",
            color: "rgba(255,255,255,0.75)",
          }}
          title={`Required agent v${REQUIRED_AGENT_VERSION}`}
        >
          web&nbsp;{REQUIRED_AGENT_VERSION}
        </span>
      </header>

      <div className="flex min-h-0 flex-1 overflow-hidden">
        {navVisible ? <NavPanel /> : <ActivityBar />}
        <main className="flex min-h-0 flex-1 flex-col overflow-hidden">
          <ActionBar />
          <CoverageBar />
          <div className="flex min-h-0 flex-1 flex-col gap-2 overflow-hidden px-2 py-2">
            <BoardGrid />
            {logVisible && <LogPanel />}
          </div>
        </main>
      </div>
      <StatusBar />
      <DialogHost />
      {/* Live "Update in progress" screen while a patch downloads/applies and
          the agent restarts headlessly. Takes precedence over the reinstall
          gate so an in-flight auto-update is never hidden behind it. */}
      {progress ? (
        <AgentUpdating progress={progress} />
      ) : (
        updateBlocked && (
          <AgentUpdateRequired
            status={updateBlocked}
            onRetry={updateBlocked.configured ? apply : undefined}
          />
        )
      )}
    </div>
  );
}
