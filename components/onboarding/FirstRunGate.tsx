"use client";

import { useState, type ReactNode } from "react";
import { motion } from "framer-motion";
import { agent } from "@/lib/agent-client";
import { useAppState } from "@/lib/app-state";
import { usePreferences } from "@/lib/preferences";
import { GuidedTour } from "@/components/onboarding/GuidedTour";
import {
  ConnectionFields,
  toPayload,
  useConnectionFields,
} from "@/components/dialogs/ConnectionFields";

/**
 * Drives the first-run onboarding flow. The agent download/install is handled
 * upstream (OnboardingScreen, shown while the agent is offline). Once the agent
 * is online this gate runs the remaining stages on top of the always-rendered
 * app shell:
 *   Stage 2 — Setup (SetupWizard): credentials + read-only model defaults.
 *   Stage 3 — Quick tour (GuidedTour): a short walkthrough of the app.
 *   Stage 4 — Full app usage: nothing overlaid; the shell is fully usable.
 * Tour completion is persisted so returning users land straight in stage 4.
 */
export function FirstRunGate({ children }: { children: ReactNode }) {
  const { settings, setSettings } = useAppState();
  const { prefs, setTourCompleted } = usePreferences();
  const [dismissed, setDismissed] = useState(false);

  // The agent is the source of truth for tour completion: the browser
  // localStorage copy gets wiped whenever the web origin/port changes between
  // launches, which used to make the tour reappear on a simple refresh. Treat
  // the tour as done if EITHER the server flag or the local cache says so.
  const tourCompleted = settings?.tour_completed === true || prefs.tourCompleted;

  const showWizard = !settings?.configured && !dismissed;
  const setupDone = !!settings?.configured || dismissed;
  const showTour = setupDone && !showWizard && !tourCompleted;

  // Persist completion to both the local cache (instant) and the agent
  // (durable). Ignore a 404 from older agents that lack the /settings/tour
  // route — the local cache still suppresses the tour for this session.
  const completeTour = () => {
    setTourCompleted(true);
    setSettings(settings ? { ...settings, tour_completed: true } : settings);
    agent.setTourCompleted(true).catch(() => {
      /* older agent without the route — local cache is enough */
    });
  };

  return (
    <>
      {children}
      {showWizard && (
        <SetupWizard
          onConnected={(s) => setSettings(s)}
          onSkip={() => setDismissed(true)}
        />
      )}
      {showTour && <GuidedTour onDone={completeTour} />}
    </>
  );
}

function SetupWizard({
  onConnected,
  onSkip,
}: {
  onConnected: (s: Awaited<ReturnType<typeof agent.getSettings>>) => void;
  onSkip: () => void;
}) {
  // The first-run form starts every field empty so the
  // Base URL shows its placeholder and is directly editable (the backend
  // supplies the default endpoint when none is submitted on save).
  const { values, setValues } = useConnectionFields();
  const [busy, setBusy] = useState(false);
  const [logs, setLogs] = useState<string[]>([]);
  const log = (m: string) => setLogs((p) => [...p, m]);

  const connect = async () => {
    if (!values.pat.trim() || !values.organization.trim()) {
      log("[ERROR] PAT and Organization are required.");
      return;
    }
    setBusy(true);
    setLogs([]);
    log("[INFO] Saving settings and connecting...");
    try {
      await agent.saveSettings(toPayload(values));
      const v = await agent.verifyPat();
      if (!v.ok) {
        log(`[ERROR] ADO connection failed: ${v.detail}`);
        setBusy(false);
        return;
      }
      log("[SUCCESS] ADO connected.");
      const s = await agent.getSettings();
      onConnected(s);
    } catch (e) {
      log(`[ERROR] Setup failed: ${(e as Error).message}`);
      setBusy(false);
    }
  };

  const skip = async () => {
    setBusy(true);
    try {
      await agent.saveSettings(toPayload(values));
    } catch {
      /* ignore — manual mode does not require valid credentials */
    }
    onSkip();
  };

  return (
    // Scrollable overlay: a min-h-full flex wrapper keeps the dialog centered
    // when it fits and lets the whole panel scroll (top reachable) when the
    // form is taller than the viewport — otherwise the step label/title get
    // clipped above the fold on short screens.
    <div className="tt-overlay fixed inset-0 z-50 overflow-y-auto">
      <div className="flex min-h-full items-center justify-center p-4">
        <motion.div
          initial={{ opacity: 0, scale: 0.97 }}
          animate={{ opacity: 1, scale: 1 }}
          transition={{ duration: 0.2 }}
          className="tt-dialog my-auto w-full max-w-xl p-6"
        >
        <span className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
          Setup · Step 2 of 4
        </span>
        <h2 className="mt-1 text-lg font-bold tracking-tight text-[var(--tt-text-bright)]">
          Set up your connection
        </h2>
        <p className="mt-1 text-sm leading-relaxed text-muted-foreground">
          Enter your LLM API and Azure DevOps details. On{" "}
          <b className="text-[var(--tt-text-secondary)]">Save &amp; Connect</b> the app stores
          credentials, verifies the PAT, and loads your projects. No API key?
          You can still proceed and use Manual Mode.
        </p>

        <div className="mt-5">
          <ConnectionFields values={values} setValues={setValues} readOnlyModels />
        </div>

        {logs.length > 0 && (
          <div className="mt-4 max-h-28 overflow-auto rounded-lg border border-[var(--tt-outline)] bg-[var(--tt-surface-deepest)] p-3 font-mono text-xs">
            {logs.map((l, i) => (
              <div key={i} className="text-[var(--tt-text-secondary)]">
                {l}
              </div>
            ))}
          </div>
        )}

        <div className="mt-6 flex justify-end gap-2">
          <button className="tt-btn-ghost" onClick={skip} disabled={busy}>
            Skip (manual mode)
          </button>
          <button
            className="tt-btn-success"
            onClick={connect}
            disabled={busy}
          >
            {busy ? "Connecting..." : "Save & Connect"}
          </button>
        </div>
      </motion.div>
      </div>
    </div>
  );
}
