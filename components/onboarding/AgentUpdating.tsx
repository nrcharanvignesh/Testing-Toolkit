"use client";

import { Download, RefreshCw } from "lucide-react";
import type { AppUpdateProgress } from "@/lib/use-app-update";

/**
 * Full-screen, non-dismissable "Update in progress" screen. Shown while the
 * agent is downloading + applying a patch and restarting itself (headless). It
 * relays live progress (phase message + percentage bar) so the self-update is
 * visible rather than a silent freeze, and pauses the rest of the app until the
 * freshly-restarted agent reconnects and the page reloads.
 */
export function AgentUpdating({ progress }: { progress: AppUpdateProgress }) {
  const pct = Math.max(0, Math.min(100, Math.round(progress.percent)));
  const showPct = !progress.indeterminate;

  return (
    <div
      role="alertdialog"
      aria-modal="true"
      aria-labelledby="agent-updating-title"
      aria-describedby="agent-updating-status"
      className="fixed inset-0 z-[110] flex items-center justify-center bg-background/95 px-6 backdrop-blur-sm"
    >
      <div className="tt-dialog w-full max-w-md p-7">
        <div className="flex items-start gap-3">
          <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg bg-[var(--tt-warn)]/15">
            <Download className="h-5 w-5 text-[var(--tt-warn)]" strokeWidth={2} />
          </div>
          <div className="min-w-0">
            <h1
              id="agent-updating-title"
              className="text-balance text-lg font-bold tracking-tight text-foreground"
            >
              Update in progress
            </h1>
            <p className="mt-1 text-pretty text-sm leading-relaxed text-muted-foreground">
              The Testing Toolkit agent is updating itself
              {progress.version ? (
                <>
                  {" "}
                  to <span className="font-mono">v{progress.version}</span>
                </>
              ) : null}
              . Please keep this window open — it will reload automatically when
              the update finishes.
            </p>
          </div>
        </div>

        <div className="mt-6">
          <div className="mb-2 flex items-center justify-between gap-3 text-xs">
            <span
              id="agent-updating-status"
              className="flex items-center gap-2 text-foreground"
            >
              <RefreshCw
                className="h-3.5 w-3.5 animate-spin text-[var(--tt-warn)]"
                strokeWidth={2}
              />
              {progress.message}
            </span>
            {showPct && (
              <span className="tabular-nums font-mono text-muted-foreground">
                {pct}%
              </span>
            )}
          </div>

          <div
            className="h-2 w-full overflow-hidden rounded-full bg-[var(--tt-outline)]"
            role="progressbar"
            aria-valuemin={0}
            aria-valuemax={100}
            aria-valuenow={showPct ? pct : undefined}
            aria-label="Update progress"
          >
            <div
              className={`h-full rounded-full bg-[var(--tt-warn)] transition-[width] duration-300 ease-out ${
                progress.indeterminate ? "tt-progress-indeterminate w-2/5" : ""
              }`}
              style={
                progress.indeterminate ? undefined : { width: `${pct}%` }
              }
            />
          </div>
        </div>

        <p className="mt-5 text-[11px] leading-relaxed text-muted-foreground">
          This happens automatically to keep you on the latest version. No action
          is needed.
        </p>
      </div>
    </div>
  );
}
