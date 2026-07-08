"use client";

import { useAgent } from "@/lib/agent-context";
import { useAppState, type KbState } from "@/lib/app-state";
import { useMetrics } from "@/lib/use-metrics";
import { REQUIRED_AGENT_VERSION } from "@/lib/agent-version";

/** Format a megabyte value as GB when large enough, else MB. */
function fmtMem(mb: number | null): string {
  if (mb === null || Number.isNaN(mb)) return "--";
  return mb >= 1024 ? `${(mb / 1024).toFixed(1)} GB` : `${mb} MB`;
}

/** A compact metric readout: label + value, no status dot. */
function Metric({
  label,
  value,
  title,
}: {
  label: string;
  value: string;
  title?: string;
}) {
  return (
    <span className="flex items-center gap-1" title={title}>
      <span className="text-[var(--tt-text-muted)]">{label}</span>
      <span className="font-medium tabular-nums text-[var(--tt-text-bright)]">{value}</span>
    </span>
  );
}

function Dot({ color, pulse }: { color: string; pulse?: boolean }) {
  return (
    <span
      className={`h-2 w-2 rounded-full ${pulse ? "animate-pulse" : ""}`}
      style={{ background: color }}
    />
  );
}

/** Right-side status chip: colored pill background + dot + label. */
function Chip({
  label,
  ok,
  warn,
  pulse,
  title,
  color: colorOverride,
}: {
  label: string;
  ok?: boolean;
  warn?: boolean;
  pulse?: boolean;
  title?: string;
  color?: string;
}) {
  const color =
    colorOverride ??
    (ok ? "var(--tt-success)" : warn ? "var(--tt-warn)" : "var(--tt-text-faint)");
  // Derive a translucent background from the dot color
  const bg = ok
    ? "rgba(26,171,92,0.12)"
    : warn
      ? "rgba(245,158,11,0.12)"
      : colorOverride === "var(--tt-success)"
        ? "rgba(26,171,92,0.12)"
        : colorOverride === "var(--tt-warn)"
          ? "rgba(245,158,11,0.12)"
          : colorOverride === "var(--tt-danger)"
            ? "rgba(229,62,62,0.12)"
            : "rgba(138,143,153,0.10)";
  return (
    <span
      className="flex items-center gap-1.5 rounded-md px-2 py-0.5"
      style={{ background: bg, color }}
      title={title}
    >
      <span
        className={`tt-chip-dot${pulse ? " tt-animate-pulse-dot" : ""}`}
        style={{ background: color }}
      />
      <span className="font-medium">{label}</span>
    </span>
  );
}

const KB_COLOR: Record<KbState, string> = {
  none: "var(--tt-danger)",
  indexing: "var(--tt-warn)",
  ready: "var(--tt-success)",
  error: "var(--tt-danger)",
};

export function StatusBar() {
  const { status, health } = useAgent();
  const agentVer = health?.version ?? null;
  const {
    settings,
    kbState,
    kbMessage,
    kbProgress,
    kbUploads,
    kbUploading,
    boardLoading,
    projectsLoading,
    logVisible,
    setLogVisible,
  } = useAppState();

  const hasOrg = !!settings?.organization;
  const hasKey = !!settings?.has_api_key;
  const connected = status === "connected";
  const working = boardLoading || projectsLoading;

  // Live CPU/RAM/GPU usage (agent >= 1.8.0; gracefully absent on older agents).
  const metrics = useMetrics(connected);
  const gpu = metrics?.gpu ?? null;

  const toggleLogs = () => setLogVisible(!logVisible);

  // Aggregate upload progress for the status-bar indicator.
  const uploadTotal = kbUploads.length;
  const uploadDone = kbUploads.filter((u) => u.status === "done").length;
  const uploadFrac =
    uploadTotal > 0
      ? kbUploads.reduce((s, u) => s + (u.status === "done" ? 1 : u.progress), 0) /
        uploadTotal
      : 0;

  // Left side: activity label + KB status (green when ready).
  const activity = kbUploading ? "Uploading" : working ? "Working" : "Idle";
  const kbReady = kbState === "ready";

  return (
    <footer
      role="button"
      tabIndex={0}
      aria-pressed={logVisible}
      aria-label={logVisible ? "Hide logs" : "Show logs"}
      title="Click to toggle logs"
      onClick={toggleLogs}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          toggleLogs();
        }
      }}
      className="tt-statusbar flex h-7 cursor-pointer items-center justify-between px-3 outline-none focus-visible:ring-1 focus-visible:ring-[var(--tt-info)]"
    >
      <div className="flex items-center gap-2">
        <span className="text-[var(--tt-text-muted)]">{activity}</span>
        {kbUploading ? (
          <>
            <span className="font-medium text-[var(--tt-primary)]">
              Uploading {uploadDone}/{uploadTotal} file(s)
            </span>
            <span
              className="h-1.5 w-28 overflow-hidden rounded-full bg-[var(--tt-outline)]"
              aria-label="Knowledge base upload progress"
            >
              <span
                className="block h-full rounded-full bg-[var(--tt-primary)] transition-[width] duration-200 ease-out"
                style={{ width: `${Math.round(uploadFrac * 100)}%` }}
              />
            </span>
            <span className="tabular-nums text-[var(--tt-text-muted)]">
              {Math.round(uploadFrac * 100)}%
            </span>
          </>
        ) : (
          <span
            title={kbMessage}
            style={{ color: kbReady ? "var(--tt-success)" : KB_COLOR[kbState] }}
            className="font-medium"
          >
            {kbMessage}
          </span>
        )}
        {kbState === "indexing" && (
          <>
            <span
              className="h-1.5 w-28 overflow-hidden rounded-full bg-[var(--tt-outline)]"
              aria-label="Knowledge base indexing progress"
            >
              <span
                className={`block h-full rounded-full bg-[var(--tt-warn)] transition-[width] duration-200 ease-out ${
                  kbProgress === null ? "tt-progress-indeterminate w-2/5" : ""
                }`}
                style={
                  kbProgress === null
                    ? undefined
                    : { width: `${Math.round(kbProgress * 100)}%` }
                }
              />
            </span>
            {kbProgress !== null && (
              <span className="tabular-nums text-[var(--tt-text-muted)]">
                {Math.round(kbProgress * 100)}%
              </span>
            )}
          </>
        )}
      </div>
      <div className="flex items-center gap-4">
        {metrics && (
          <div className="flex items-center gap-3 border-r border-[var(--tt-outline)] pr-4">
            {/* CPU, RAM and Data are always shown — scoped to the app alone,
                not the whole machine. RAM and Data are shown as actual amounts
                (MB/GB), not percentages. */}
            <Metric
              label="CPU"
              value={
                metrics.cpu_percent !== null ? `${metrics.cpu_percent}%` : "--"
              }
              title="CPU used by Testing Toolkit"
            />
            <Metric
              label="RAM"
              value={fmtMem(metrics.proc_mem_mb)}
              title={`Memory used by Testing Toolkit${
                metrics.ram_total_mb !== null
                  ? ` (system: ${fmtMem(metrics.ram_used_mb)} / ${fmtMem(
                      metrics.ram_total_mb
                    )})`
                  : ""
              }`}
            />
            <Metric
              label="Data"
              value={fmtMem(metrics.app_data_mb)}
              title="Disk space used by Testing Toolkit's data (workspace)"
            />
            {gpu?.in_use && (
              <Metric
                label="GPU"
                value={
                  gpu.util_percent !== null
                    ? `${gpu.util_percent}%`
                    : gpu.mem_used_mb !== null
                      ? fmtMem(gpu.mem_used_mb)
                      : "on"
                }
                title={
                  `${gpu.name}` +
                  (gpu.unified_memory
                    ? // Unified-memory SoC (e.g. Apple Silicon): shared pool,
                      // not separate VRAM.
                      gpu.mem_total_mb !== null
                      ? ` — unified memory (${fmtMem(gpu.mem_total_mb)} shared)`
                      : " — unified memory"
                    : gpu.mem_used_mb !== null && gpu.mem_total_mb !== null
                      ? ` — ${fmtMem(gpu.mem_used_mb)} / ${fmtMem(
                          gpu.mem_total_mb
                        )} VRAM`
                      : "") +
                  // The execution provider a model actually bound to (>= 2.3.0):
                  // distinguishes a real GPU run from a CPU fallback.
                  (gpu.ep ? ` — ${gpu.ep}` : "")
                }
              />
            )}
          </div>
        )}
        <Chip
          label="AI"
          ok={hasKey}
          warn={!hasKey}
          title={hasKey ? "AI API key set" : "no API key (manual mode)"}
        />
        <Chip
          label="ADO"
          ok={hasOrg && connected}
          warn={!hasOrg || !connected}
          title="Azure DevOps status"
        />
        <Chip
          label="KB"
          color={KB_COLOR[kbState]}
          pulse={kbState === "indexing"}
          title={kbMessage || `Knowledge base: ${kbState}`}
        />
        {/* Agent / web version */}
        <span
          className="font-mono tabular-nums"
          style={{ color: "var(--tt-text-faint)", fontSize: "9px" }}
          title={`Agent: ${agentVer ?? "not connected"} | Web: ${REQUIRED_AGENT_VERSION}`}
        >
          {agentVer ? `v${agentVer}` : `web ${REQUIRED_AGENT_VERSION}`}
        </span>
      </div>
    </footer>
  );
}
