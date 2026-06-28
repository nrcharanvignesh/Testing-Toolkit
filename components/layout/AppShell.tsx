"use client";

import { useEffect, useRef } from "react";
import { useAgent } from "@/lib/agent-context";
import { useAppState } from "@/lib/app-state";
import { useAppUpdate } from "@/lib/use-app-update";
import { getPreferences, setPendingReindexPref } from "@/lib/preferences";
import { ActivityBar } from "./ActivityBar";
import { NavPanel } from "./NavPanel";
import { StatusBar } from "./StatusBar";
import { BoardGrid } from "@/components/board/BoardGrid";
import { ActionBar } from "@/components/board/ActionBar";
import { LogPanel } from "@/components/board/LogPanel";
import { DialogHost } from "@/components/dialogs/DialogHost";

export function AppShell() {
  const { status } = useAgent();
  const {
    navVisible,
    logVisible,
    settings,
    reloadProjects,
    reindexAllKbs,
    pushLog,
  } = useAppState();
  const { check, apply } = useAppUpdate(pushLog);
  const bootstrapped = useRef(false);
  const reindexed = useRef(false);
  const autoUpdated = useRef(false);

  // Bootstrap: once connected & configured, load the project list (desktop
  // main.py _bootstrap -> reload_projects).
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

  // On every refresh: once connected & configured, silently check for the
  // latest agent patch and apply it automatically. apply() restarts the agent,
  // waits for it to come back, then reloads the page so the new code is live —
  // so the user just refreshes and the patch "arrives" on its own. Runs once
  // per load; nothing happens (no noise) when already up to date or when the
  // install has no update config.
  useEffect(() => {
    if (
      status === "connected" &&
      settings?.configured &&
      !autoUpdated.current
    ) {
      autoUpdated.current = true;
      void (async () => {
        const s = await check();
        if (s?.configured && s.update_available) {
          pushLog?.(
            "INFO",
            `New patch available (v${s.latest}). Applying automatically...`
          );
          await apply();
        }
      })();
    }
  }, [status, settings?.configured, check, apply, pushLog]);

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
      <div className="flex min-h-0 flex-1 overflow-hidden">
        {navVisible ? <NavPanel /> : <ActivityBar />}
        <main className="flex min-h-0 flex-1 flex-col gap-2 overflow-hidden px-2 py-2">
          <BoardGrid />
          <ActionBar />
          {logVisible && <LogPanel />}
        </main>
      </div>
      <StatusBar />
      <DialogHost />
    </div>
  );
}
