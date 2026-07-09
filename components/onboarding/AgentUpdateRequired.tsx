"use client";

import { useState } from "react";
import { AlertTriangle, RotateCcw, RefreshCw } from "lucide-react";
import type { UpdateStatus } from "@/lib/agent-client";
import { requestReinstall } from "@/lib/reinstall";

/**
 * Full-screen, non-dismissable gate shown when an agent update is available but
 * the app could NOT apply it silently (the install isn't configured for
 * auto-update, or the silent apply failed). It deliberately makes the app
 * unusable until the user reinstalls, because the running agent is out of date
 * with the shipped patch. "Try update again" is offered only when auto-update
 * is configured (e.g. a transient network failure); otherwise reinstall is the
 * only way forward.
 */
export function AgentUpdateRequired({
  status,
  onRetry,
}: {
  status: UpdateStatus;
  onRetry?: () => Promise<boolean>;
}) {
  const [retrying, setRetrying] = useState(false);

  const retry = async () => {
    if (!onRetry) return;
    setRetrying(true);
    try {
      // On success apply() reloads the page; if it returns we stay blocked.
      await onRetry();
    } finally {
      setRetrying(false);
    }
  };

  return (
    <div
      role="alertdialog"
      aria-modal="true"
      aria-labelledby="agent-update-title"
      className="fixed inset-0 z-[100] flex items-center justify-center bg-background/95 px-6 backdrop-blur-sm"
    >
      <div className="tt-dialog w-full max-w-lg p-7">
        <div className="flex items-start gap-3">
          <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg bg-amber-500/15">
            <AlertTriangle className="h-5 w-5 text-amber-400" strokeWidth={2} />
          </div>
          <div>
            <h1
              id="agent-update-title"
              className="text-balance text-lg font-bold tracking-tight text-foreground"
            >
              Agent changes have been made
            </h1>
            <p className="mt-1 text-pretty text-sm leading-relaxed text-muted-foreground">
              A new version of the Testing Toolkit agent
              {status.latest ? (
                <>
                  {" "}
                  (<span className="font-mono">v{status.latest}</span>)
                </>
              ) : null}{" "}
              is available. Please update the app to use the latest version.
              The app is paused until you do.
            </p>
          </div>
        </div>

        <div className="tt-input mt-5 flex flex-col gap-2 !p-3 text-xs">
          <div className="flex items-center justify-between gap-3">
            <span className="text-muted-foreground">Installed version</span>
            <span className="font-mono text-foreground">
              {status.current ?? "unknown"}
            </span>
          </div>
          <div className="flex items-center justify-between gap-3">
            <span className="text-muted-foreground">Latest version</span>
            <span className="font-mono text-foreground">
              {status.latest ?? "unknown"}
            </span>
          </div>
        </div>

        <div className="mt-6 flex flex-col gap-2 sm:flex-row sm:justify-end">
          {onRetry && (
            <button
              className="tt-btn-ghost flex items-center justify-center gap-2"
              onClick={retry}
              disabled={retrying}
            >
              <RefreshCw
                className={`h-3.5 w-3.5 ${retrying ? "animate-spin" : ""}`}
                strokeWidth={2}
              />
              {retrying ? "Trying..." : "Try update again"}
            </button>
          )}
          <button
            className="tt-btn-primary flex items-center justify-center gap-2"
            onClick={requestReinstall}
            disabled={retrying}
          >
            <RotateCcw className="h-3.5 w-3.5" strokeWidth={2} />
            Update the app
          </button>
        </div>

        <p className="mt-3 text-[11px] leading-relaxed text-muted-foreground">
          Updating installs the latest agent and takes you through onboarding
          again. Your settings, fetched models, preferences and generated
          artifacts are kept; knowledge bases are re-indexed automatically once
          the updated agent reconnects.
        </p>
      </div>
    </div>
  );
}
