"use client";

import { motion } from "framer-motion";
import type { InstallProgress } from "@/lib/agent-client";

/** Friendly labels for each installer phase. */
const PHASE_LABEL: Record<string, string> = {
  downloading: "Downloading agent bundle",
  extracting: "Extracting files",
  overlay: "Applying latest agent code",
  cleaning: "Preparing a clean install",
  installing_deps: "Installing packages",
  copying: "Installing agent files",
  starting: "Starting the agent",
  done: "Finishing up",
  error: "Something went wrong",
};

/**
 * Live install/reinstall progress bar, driven by the installer beacon via
 * `useInstallProgress`. Shows a determinate bar when a percent is known and an
 * indeterminate shimmer otherwise (e.g. before the beacon reports in).
 */
export function InstallProgressBar({
  progress,
}: {
  progress: InstallProgress | null;
}) {
  const isError = progress?.phase === "error";
  const percent =
    typeof progress?.percent === "number"
      ? Math.max(0, Math.min(100, progress.percent))
      : null;
  const label =
    progress?.message ||
    (progress ? PHASE_LABEL[progress.phase] ?? "Installing" : "Waiting to start the installer…");

  return (
    <div className="flex w-full flex-col gap-2" aria-live="polite">
      <div className="flex items-center justify-between text-xs">
        <span
          className={
            isError ? "font-medium text-destructive" : "text-muted-foreground"
          }
        >
          {label}
        </span>
        {percent !== null && !isError && (
          <span className="tabular-nums text-muted-foreground/70">
            {percent}%
          </span>
        )}
      </div>

      <div
        className="h-2 w-full overflow-hidden rounded-full bg-muted"
        role="progressbar"
        aria-valuenow={percent ?? undefined}
        aria-valuemin={0}
        aria-valuemax={100}
      >
        {isError ? (
          <div className="h-full w-full bg-destructive" />
        ) : percent !== null ? (
          <motion.div
            className="h-full rounded-full bg-primary"
            initial={false}
            animate={{ width: `${percent}%` }}
            transition={{ duration: 0.4, ease: "easeOut" }}
          />
        ) : (
          // Indeterminate shimmer until the first percent arrives.
          <motion.div
            className="h-full w-1/3 rounded-full bg-primary/70"
            animate={{ x: ["-100%", "300%"] }}
            transition={{ repeat: Infinity, duration: 1.4, ease: "easeInOut" }}
          />
        )}
      </div>
    </div>
  );
}
