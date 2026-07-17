"use client";

import { useEffect, useRef, useState } from "react";
import { motion } from "framer-motion";
import { useAgent } from "@/lib/agent-context";

function getOS(): "windows" | "mac" | "linux" {
  if (typeof navigator === "undefined") return "windows";
  const ua = navigator.userAgent.toLowerCase();
  if (ua.includes("mac")) return "mac";
  if (ua.includes("linux")) return "linux";
  return "windows";
}

const INSTALLER_MAP = {
  windows: { label: "Windows" },
  mac: { label: "macOS" },
  linux: { label: "Linux" },
} as const;

/**
 * Step 1 of onboarding: download & run the installer.
 *
 * Two modes:
 *  - first run (default): shown while the agent is offline.
 *  - reinstall (`reinstall` prop): forced on top of a connected agent so the
 *    user re-downloads and re-runs the installer. Settings, preferences,
 *    artifacts, KB documents, vectors and project context are retained; only
 *    disposable runtime caches are refreshed. We detect
 *    completion by watching the agent drop (installer stops it) and reconnect;
 *    the app resumes automatically as soon as the fresh agent is healthy.
 */
export function OnboardingScreen({
  reinstall = false,
  onReinstallComplete,
  onReinstallCancel,
}: {
  reinstall?: boolean;
  onReinstallComplete?: () => void;
  onReinstallCancel?: () => void;
}) {
  const { status } = useAgent();
  const [os, setOS] = useState<"windows" | "mac" | "linux">("windows");
  const [downloaded, setDownloaded] = useState(false);
  // Did we observe the old agent drop after the user started the reinstall?
  const sawDrop = useRef(false);

  useEffect(() => {
    setOS(getOS());
  }, []);

  // Reinstall completion: once the user has downloaded the new installer, watch
  // for the agent going offline (installer replacing/restarting it) and then
  // coming back connected — that round-trip means the fresh agent is live.
  useEffect(() => {
    if (!reinstall || !downloaded) return;
    if (status === "offline" || status === "connecting") {
      sawDrop.current = true;
    } else if (status === "connected" && sawDrop.current) {
      onReinstallComplete?.();
    }
  }, [reinstall, downloaded, status, onReinstallComplete]);

  const installer = INSTALLER_MAP[os];

  function handleDownload() {
    // Download the production installer generated server-side by /api/installer.
    // The route embeds a read-only token and streams a tiny launcher that pulls
    // the agent bundle from GitHub at install time. On a reinstall we request a
    // fresh download (fresh=1) so the installer ignores any cached bundle parts
    // and re-downloads everything from scratch.
    const link = document.createElement("a");
    link.href = `/api/installer?os=${os}${reinstall ? "&fresh=1" : ""}`;
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
    setDownloaded(true);
  }

  const downloadLabel = reinstall
    ? `Download update for ${installer.label}`
    : `Download for ${installer.label}`;

  return (
    <div className="flex h-screen flex-col items-center justify-center bg-background px-6">
      <motion.div
        initial={{ opacity: 0, y: 20 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.5 }}
        className="flex max-w-lg flex-col items-center gap-8 text-center"
      >
        {/* Logo / Hero */}
        <div className="flex flex-col items-center gap-3">
          <div className="flex h-16 w-16 items-center justify-center rounded-2xl bg-primary/10">
            <svg
              className="h-8 w-8 text-primary"
              fill="none"
              viewBox="0 0 24 24"
              strokeWidth={1.5}
              stroke="currentColor"
            >
              <path
                strokeLinecap="round"
                strokeLinejoin="round"
                d="M9.75 3.104v5.714a2.25 2.25 0 0 1-.659 1.591L5 14.5M9.75 3.104c-.251.023-.501.05-.75.082m.75-.082a24.301 24.301 0 0 1 4.5 0m0 0v5.714c0 .597.237 1.17.659 1.591L19.8 15.3M14.25 3.104c.251.023.501.05.75.082M19.8 15.3l-1.57.393A9.065 9.065 0 0 1 12 15a9.065 9.065 0 0 0-6.23.693L5 14.5m14.8.8 1.402 1.402c1.232 1.232.65 3.318-1.067 3.611A48.309 48.309 0 0 1 12 21c-2.773 0-5.491-.235-8.135-.687-1.718-.293-2.3-2.379-1.067-3.61L5 14.5"
              />
            </svg>
          </div>
          <h1 className="text-3xl font-bold tracking-tight">
            {reinstall ? "Reinstall Testing Toolkit" : "Testing Toolkit"}
          </h1>
          <p className="text-muted-foreground">
            AI-powered testing and quality automation
          </p>
        </div>

        {/* Reinstall warning note */}
        {reinstall && (
          <div className="w-full rounded-xl border border-amber-500/30 bg-amber-500/10 px-5 py-4 text-left">
            <p className="text-sm font-semibold text-amber-300">
              You&apos;re reinstalling the agent
            </p>
            <ul className="mt-2 space-y-1.5 text-xs leading-relaxed text-muted-foreground">
              <li>
                Re-download the installer below and run it — it replaces the
                local agent and restarts it.
              </li>
              <li>
                Your{" "}
                <b className="text-foreground">
                  settings, fetched models, preferences and artifacts are kept
                </b>
                .
              </li>
              <li>
                Your <b className="text-foreground">project knowledge base</b>,
                vector index and project context are preserved. Only documents
                you add, replace or remove are indexed afterward.
              </li>
              <li>
                The installer refreshes agent code and disposable runtime
                caches; your settings and data are preserved.
              </li>
            </ul>
          </div>
        )}

        {/* Action area */}
        {!downloaded ? (
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            transition={{ delay: 0.3 }}
            className="flex flex-col items-center gap-4"
          >
            <p className="text-sm text-muted-foreground">
              {reinstall
                ? "Download the installer to refresh the local agent while preserving project data."
                : "To get started, install the local compute agent on your machine."}
            </p>
            <button
              onClick={handleDownload}
              className="inline-flex h-12 items-center gap-2 rounded-xl bg-primary px-8 text-sm font-medium text-primary-foreground shadow-lg shadow-primary/20 transition-all hover:scale-[1.02] hover:shadow-xl hover:shadow-primary/30 active:scale-[0.98]"
            >
              <svg
                className="h-4 w-4"
                fill="none"
                viewBox="0 0 24 24"
                strokeWidth={2}
                stroke="currentColor"
              >
                <path
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  d="M3 16.5v2.25A2.25 2.25 0 0 0 5.25 21h13.5A2.25 2.25 0 0 0 21 18.75V16.5M16.5 12 12 16.5m0 0L7.5 12m4.5 4.5V3"
                />
              </svg>
              {downloadLabel}
            </button>
            <p className="text-xs text-muted-foreground/60">
              One file. Double-click to install. No admin rights needed.
            </p>
            {reinstall && (
              <button
                onClick={onReinstallCancel}
                className="text-xs text-muted-foreground underline-offset-2 hover:underline"
              >
                Cancel update
              </button>
            )}
          </motion.div>
        ) : (
          <motion.div
            initial={{ opacity: 0, y: 10 }}
            animate={{ opacity: 1, y: 0 }}
            className="flex flex-col items-center gap-4"
          >
            <div className="flex w-full items-center gap-3 rounded-xl border border-border/50 bg-muted/30 px-6 py-4">
              <motion.div
                animate={{ y: [0, 4, 0] }}
                transition={{ repeat: Infinity, duration: 1.5 }}
              >
                <svg
                  className="h-5 w-5 text-primary"
                  fill="none"
                  viewBox="0 0 24 24"
                  strokeWidth={2}
                  stroke="currentColor"
                >
                  <path
                    strokeLinecap="round"
                    strokeLinejoin="round"
                    d="m19.5 4.5-15 15m0 0h11.25m-11.25 0V8.25"
                  />
                </svg>
              </motion.div>
              <div className="flex flex-1 items-center justify-between gap-4">
                <p className="text-sm">
                  Run the downloaded file to{" "}
                  {reinstall ? "complete the update" : "complete setup"}
                </p>
                <span className="shrink-0 rounded-md bg-muted px-2 py-1 text-xs text-muted-foreground">
                  Usually 10–20 min
                </span>
              </div>
            </div>

            {/* This is intentionally an indeterminate visual indicator. The
                browser cannot read CLI progress, so it animates until the agent
                reconnects and the onboarding screen closes automatically. */}
            <div className="flex w-full flex-col gap-2 text-left">
              <div className="flex items-center justify-between gap-4 text-xs text-muted-foreground">
                <span>{reinstall ? "Reinstalling agent" : "Installing agent"}</span>
                <span className="animate-pulse">In progress</span>
              </div>
              <div
                className="h-2 w-full overflow-hidden rounded-full bg-[var(--tt-outline)]"
                role="progressbar"
                aria-label={reinstall ? "Reinstalling agent" : "Installing agent"}
                aria-valuetext="In progress"
              >
                <span className="tt-progress-indeterminate block h-full w-2/5 rounded-full bg-primary" />
              </div>
              <p className="text-center text-xs text-muted-foreground">
                {reinstall
                  ? "Keep the installer open. This page will resume when the agent restarts."
                  : "Keep the installer open. This page will continue when the agent connects."}
              </p>
            </div>

          </motion.div>
        )}
      </motion.div>
    </div>
  );
}
