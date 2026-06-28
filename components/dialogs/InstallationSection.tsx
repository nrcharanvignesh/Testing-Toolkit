"use client";

import { useEffect, useState } from "react";
import { RefreshCw, AlertTriangle, RotateCcw } from "lucide-react";
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
  const { apply, reinstall, phase, busy } = useAppUpdate(pushLog);
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
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const onUpdate = async () => {
    const applied = await apply();
    if (!applied) void refresh();
  };

  const onReinstall = async () => {
    setConfirmReinstall(false);
    await reinstall();
  };

  const label =
    phase === "applying"
      ? "Checking..."
      : phase === "restarting"
        ? "Restarting agent..."
        : "Check for updates";

  const reinstallLabel =
    phase === "reinstalling"
      ? "Reinstalling..."
      : phase === "restarting"
        ? "Restarting agent..."
        : "Reinstall app";

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

      {/* Reinstall — a heavier action than a refresh/update. */}
      <div className="mt-4 border-t border-border pt-4">
        <div className="flex items-center justify-between gap-3">
          <div>
            <h4 className="text-xs font-semibold text-foreground">
              Reinstall the agent
            </h4>
            <p className="mt-0.5 text-[11px] leading-relaxed text-muted-foreground">
              A full clean reinstall — not just a refresh. Use this if the app is
              broken or behaving unexpectedly.
            </p>
          </div>
          <button
            className="tt-btn-ghost flex shrink-0 items-center gap-2 !border-amber-500/40 !text-amber-300 hover:!bg-amber-500/10"
            onClick={() => setConfirmReinstall(true)}
            disabled={busy}
          >
            <RotateCcw
              className={`h-3.5 w-3.5 ${phase === "reinstalling" ? "animate-spin" : ""}`}
              strokeWidth={2}
            />
            {reinstallLabel}
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
              This is a full reinstall, not a refresh. Here&apos;s what to expect:
            </p>
          </div>
        </div>

        <ul className="mt-4 space-y-2 text-sm leading-relaxed">
          <li className="flex gap-2">
            <span className="mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full bg-amber-400" />
            <span className="text-foreground">
              You&apos;ll go through{" "}
              <b className="text-foreground">onboarding again</b> (download,
              install and the quick tour).
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
              All your{" "}
              <b className="text-foreground">knowledge bases are re-indexed</b>{" "}
              automatically once the agent restarts. This can take a while for
              large KBs.
            </span>
          </li>
          <li className="flex gap-2">
            <span className="mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full bg-amber-400" />
            <span className="text-muted-foreground">
              The agent restarts and the app reloads during the process — don&apos;t
              close it until it&apos;s back.
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
