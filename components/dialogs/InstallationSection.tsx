"use client";

import { useEffect, useState } from "react";
import { AlertTriangle, RotateCcw } from "lucide-react";
import { agent, type UpdateStatus } from "@/lib/agent-client";
import { useAppUpdate } from "@/lib/use-app-update";
import { useAppState } from "@/lib/app-state";
import { requestReinstall } from "@/lib/reinstall";

/**
 * Installation & Updates panel for the Settings dialog.
 * Shows the installed agent version / location and lets the user pull the
 * latest patch on demand (the agent then restarts and the app reloads).
 */
export function InstallationSection() {
  const { pushLog } = useAppState();
  const { busy } = useAppUpdate(pushLog);
  const [status, setStatus] = useState<UpdateStatus | null>(null);
  const [checking, setChecking] = useState(true);
  const [confirmReinstall, setConfirmReinstall] = useState(false);

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
  }, []);

  const onReinstall = () => {
    setConfirmReinstall(false);
    pushLog("INFO", "Reinstall requested — returning to the installer step.");
    // Shared with the blocking AgentUpdateRequired gate: reopen the installer
    // while preserving settings, project data and artifacts.
    requestReinstall();
  };

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
          <p className="text-[var(--tt-success-soft)]">
            A new version (v{status.latest}) is available.
          </p>
        )}
        {!checking && status?.reachable && !status?.update_available && (
          <p className="text-muted-foreground">You&apos;re up to date.</p>
        )}
        {!checking && !status?.reachable && (
          <p className="text-amber-300/90">
            Update server unreachable — check your network or VPN, then Check
            for updates again.
          </p>
        )}
      </div>


      <div className="mt-4 border-t border-border pt-4">
        <div className="flex items-center justify-between gap-3">
          <div>
            <h4 className="text-xs font-semibold text-foreground">
              Reinstall application
            </h4>
            <p className="mt-0.5 text-[11px] leading-relaxed text-muted-foreground">
              Refresh the local agent from the latest installer. Use this for
              recovery without deleting project data.
            </p>
          </div>
          <button
            className="tt-btn-ghost flex shrink-0 items-center gap-2 !border-amber-500/40 !text-amber-300 hover:!bg-amber-500/10"
            onClick={() => setConfirmReinstall(true)}
            disabled={busy}
          >
            <RotateCcw className="h-3.5 w-3.5" strokeWidth={2} />
            Reinstall app
          </button>
        </div>
      </div>

      {confirmReinstall && (
        <ReinstallConfirm
          onCancel={() => setConfirmReinstall(false)}
          onConfirm={onReinstall}
        />
      )}
    </div>
  );
}

/**
 * Confirmation modal for a full reinstall. Spells out exactly what happens so
 * the user understands it is heavier than a refresh: onboarding runs again, but
 * their settings, fetched models and preferences are kept, and every knowledge
 * base is rebuilt automatically afterwards.
 */
function ReinstallConfirm({
  onCancel,
  onConfirm,
}: {
  onCancel: () => void;
  onConfirm: () => void;
}) {
  return (
    <div className="fixed inset-0 z-[60] flex items-center justify-center bg-black/60 px-6">
      <div className="tt-dialog w-full max-w-md p-6">
        <div className="flex items-start gap-3">
          <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-amber-500/15">
            <AlertTriangle className="h-5 w-5 text-amber-400" strokeWidth={2} />
          </div>
          <div>
            <h2 className="text-base font-bold tracking-tight text-foreground">
              Reinstall the Testing Toolkit?
            </h2>
            <p className="mt-1 text-sm leading-relaxed text-muted-foreground">
              This will refresh the agent. Here&apos;s what to expect:
            </p>
          </div>
        </div>

        <ul className="mt-4 space-y-2 text-sm leading-relaxed">
          <li className="flex gap-2">
            <span className="mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full bg-amber-400" />
            <span className="text-foreground">
              You&apos;ll go through{" "}
              <b className="text-foreground">onboarding again</b> — the app
              returns to Step 1 so you{" "}
              <b className="text-foreground">re-download and run the installer</b>
              .
            </span>
          </li>
          <li className="flex gap-2">
            <span className="mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full bg-emerald-400" />
            <span className="text-muted-foreground">
              Your{" "}
              <b className="text-foreground">
                settings, fetched models and preferences are kept
              </b>{" "}
              — nothing to re-enter.
            </span>
          </li>
          <li className="flex gap-2">
            <span className="mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full bg-emerald-400" />
            <span className="text-muted-foreground">
              Your{" "}
              <b className="text-foreground">generated artifacts (outputs)</b>{" "}
              are kept — nothing you&apos;ve produced is deleted.
            </span>
          </li>
            <li className="flex gap-2">
              <span className="mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full bg-amber-400" />
              <span className="text-muted-foreground">
                Your <b className="text-foreground">project knowledge base</b>,
                vector index and project context are retained. Afterward, the
                selective indexer processes only files that were added, changed or
                removed.
              </span>
            </li>
            <li className="flex gap-2">
              <span className="mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full bg-amber-400" />
              <span className="text-muted-foreground">
                Agent code and disposable runtime caches are refreshed while
                your settings and data are preserved.
              </span>
            </li>
          <li className="flex gap-2">
            <span className="mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full bg-amber-400" />
            <span className="text-muted-foreground">
              The app reloads to the installer step now — don&apos;t close it
              until the fresh agent has reconnected.
            </span>
          </li>
        </ul>

        <div className="mt-6 flex justify-end gap-2">
          <button className="tt-btn-ghost" onClick={onCancel}>
            Cancel
          </button>
          <button
            className="tt-btn-ghost flex items-center gap-2 !border-amber-500/50 !bg-amber-500/15 !text-amber-200 hover:!bg-amber-500/25"
            onClick={onConfirm}
          >
            <RotateCcw className="h-3.5 w-3.5" strokeWidth={2} />
            Reinstall now
          </button>
        </div>
      </div>
    </div>
  );
}
