"use client";

import { useEffect, useState } from "react";
import { agent, type MetricsResponse } from "./agent-client";

/**
 * Polls the agent's `/metrics` endpoint for live CPU/RAM/GPU usage while the
 * agent is connected. Returns `null` until the first successful sample.
 *
 * Graceful degradation: agents older than 1.8.0 don't have `/metrics` and will
 * 404. After a few consecutive failures we stop polling and stay `null`, so the
 * status bar simply omits the metrics on older agents instead of spamming
 * requests.
 */
export function useMetrics(enabled: boolean, intervalMs = 5000) {
  const [metrics, setMetrics] = useState<MetricsResponse | null>(null);

  useEffect(() => {
    if (!enabled) {
      setMetrics(null);
      return;
    }
    let cancelled = false;
    // Per-connection state: each (re)connect re-probes from scratch, so an
    // agent upgraded to one that has /metrics is rediscovered without a reload.
    let failures = 0;
    let id: ReturnType<typeof setInterval> | null = null;

    const stop = () => {
      if (id !== null) {
        clearInterval(id);
        id = null;
      }
    };

    const sample = async () => {
      try {
        const m = await agent.metrics();
        if (cancelled) return;
        failures = 0;
        setMetrics(m);
      } catch {
        if (cancelled) return;
        failures += 1;
        // 3 strikes => treat as an old agent without /metrics; actually stop
        // polling (don't keep hammering a 404 every interval) and stay null.
        if (failures >= 3) {
          setMetrics(null);
          stop();
        }
      }
    };

    void sample();
    id = setInterval(sample, intervalMs);
    return () => {
      cancelled = true;
      stop();
    };
  }, [enabled, intervalMs]);

  return metrics;
}
