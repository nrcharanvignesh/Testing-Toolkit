"use client";

import { useEffect, useState } from "react";
import { RefreshCw } from "lucide-react";
import { agent, type UpdateStatus } from "@/lib/agent-client";
import { useAppUpdate } from "@/lib/use-app-update";
import { useAppState } from "@/lib/app-state";

/**
 * Installation & Updates panel for the Settings dialog.
 * Shows the installed agent version / location and lets the user pull the
 * latest patch on demand (the agent then restarts and the app reloads).
 */
export function InstallationSection() {
  const { pushLog } = useAppState();
  const { apply, phase, busy } = useAppUpdate(pushLog);
  const [status, setStatus] = useState<UpdateStatus | null>(null);
  const [checking, setChecking] = useState(true);

  const refresh = async () => {
    setChecking(true);
    try {
      setStatus(await agent.updateStatus());
    } catch {
      setStatus(null);
    } finally {
      setChecking(false);
    }
  };

  useEffect(() => {
    void refresh();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const onUpdate = async () => {
    const applied = await apply();
    if (!applied) void refresh();
  };

  const label =
    phase === "applying"
      ? "Checking..."
      : phase === "restarting"
        ? "Restarting agent..."
        : "Check for updates";

  return (
    <div className="mt-5 border-t border-border pt-4">
      <h3 className="mb-2 text-sm font-semibold text-foreground">
        Installation &amp; Updates
      </h3>

      <div className="tt-input flex flex-col gap-2 !p-3 text-xs">
        <div className="flex items-center justify-between gap-3">
          <span className="text-muted-foreground">Installed version</span>
          <span className="font-mono text-foreground">
            {checking ? "..." : status?.current ?? "unknown"}
          </span>
        </div>

        <div className="flex items-center justify-between gap-3">
          <span className="text-muted-foreground">Latest available</span>
          <span className="font-mono text-foreground">
            {checking
              ? "..."
              : !status?.configured
                ? "not configured"
                : !status?.reachable
                  ? "unreachable"
                  : status?.latest ?? status?.current ?? "unknown"}
          </span>
        </div>

        {status?.install_dir && (
          <div className="flex items-center justify-between gap-3">
            <span className="shrink-0 text-muted-foreground">Location</span>
            <span
              className="truncate font-mono text-foreground"
              title={status.install_dir}
            >
              {status.install_dir}
            </span>
          </div>
        )}

        {!checking && status?.update_available && (
          <p className="text-[#7fd1b9]">
            A new version (v{status.latest}) is available.
          </p>
        )}
        {!checking && status?.configured && status?.reachable && !status?.update_available && (
          <p className="text-muted-foreground">You&apos;re up to date.</p>
        )}
      </div>

      <div className="mt-2 flex items-center gap-2">
        <button
          className="tt-btn-primary flex items-center gap-2"
          onClick={onUpdate}
          disabled={busy}
        >
          <RefreshCw
            className={`h-3.5 w-3.5 ${busy ? "animate-spin" : ""}`}
            strokeWidth={2}
          />
          {label}
        </button>
        <button
          className="tt-btn-ghost"
          onClick={() => void refresh()}
          disabled={busy || checking}
        >
          Recheck
        </button>
      </div>

      <p className="mt-2 text-[11px] leading-relaxed text-muted-foreground">
        Updates are downloaded from the Testing Toolkit repository. When a patch
        is installed the local agent restarts and this app reloads automatically.
      </p>
    </div>
  );
}
