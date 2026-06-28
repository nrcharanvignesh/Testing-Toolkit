"use client";

import { useEffect, useRef, useState } from "react";
import { agent, type InstallProgress } from "./agent-client";

/**
 * Polls the installer's temporary progress beacon (127.0.0.1:7842
 * /install/progress) while an install/reinstall is expected to be running.
 *
 * The beacon is served by the smart installer during the download phase and by
 * the offline `install.py` during the clean/install/copy/start phases, then it
 * releases the port so the real agent can bind it. This hook simply surfaces
 * the latest snapshot so the UI can show a live progress bar before the agent
 * is reachable.
 *
 * It is intentionally tolerant: a null result (no beacon yet, or the brief gap
 * between the old agent stopping and the beacon binding) does not clear the
 * last known progress — we keep showing the most recent snapshot so the bar
 * never flickers back to nothing mid-install.
 */
export function useInstallProgress(enabled: boolean, intervalMs = 1000) {
  const [progress, setProgress] = useState<InstallProgress | null>(null);
  // Remember the highest percent we've seen so transient nulls / out-of-order
  // snapshots can't make the bar jump backwards.
  const maxPercent = useRef(0);

  useEffect(() => {
    if (!enabled) {
      setProgress(null);
      maxPercent.current = 0;
      return;
    }
    let cancelled = false;

    const sample = async () => {
      const p = await agent.installProgress();
      if (cancelled || !p) return;
      // Monotonic percent: never regress (except on an explicit error phase,
      // where we want to surface the failure immediately).
      if (p.phase !== "error" && typeof p.percent === "number") {
        if (p.percent < maxPercent.current) {
          p.percent = maxPercent.current;
        } else {
          maxPercent.current = p.percent;
        }
      }
      setProgress(p);
    };

    void sample();
    const id = setInterval(sample, intervalMs);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, [enabled, intervalMs]);

  return progress;
}
