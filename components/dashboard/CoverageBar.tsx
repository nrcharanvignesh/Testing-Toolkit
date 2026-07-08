"use client";

/**
 * CoverageBar
 * A compact, single-row strip shown between ActionBar and BoardGrid that gives
 * the Senior QA engineer at-a-glance coverage and health metrics without
 * opening any dialog. Reads from app-state and fetches the last E2E run summary.
 */

import { Layers, CheckSquare, CheckCircle2, XCircle, Clock } from "lucide-react";
import useSWR from "swr";
import { useAppState } from "@/lib/app-state";
import { agent, type E2ELastRun } from "@/lib/agent-client";

/** Time since `epochSeconds` as a short human string. */
function timeSince(epochSeconds: number): string {
  const diff = Math.floor(Date.now() / 1000) - epochSeconds;
  if (diff < 60) return `${diff}s ago`;
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
  return `${Math.floor(diff / 86400)}d ago`;
}

export function CoverageBar() {
  const { boardView, selected, currentBoard, currentProject } = useAppState();

  // Fetch last E2E run for the current project (re-validates on project switch).
  const { data: lastRun } = useSWR<E2ELastRun | null>(
    currentProject ? ["e2e-last-run", currentProject] : null,
    ([, proj]: [string, string]) => agent.e2eLastRun(proj),
    { refreshInterval: 60_000, revalidateOnFocus: false }
  );

  // Only render when a board is loaded
  if (!boardView) return null;

  const totalWi = boardView.rows.length;
  const selectedCount = selected.size;

  const hasSelection = selectedCount > 0;

  return (
    <div
      className="flex shrink-0 items-center gap-1.5 border-b border-[var(--tt-outline-soft)] px-3 py-1 tt-animate-fade-up"
      style={{ background: "var(--tt-surface-deepest)" }}
      aria-label="Board coverage summary"
    >
      {/* Board label */}
      {currentBoard && (
        <span className="mr-1 truncate text-[10px] font-semibold uppercase tracking-wide text-[var(--tt-text-faint)]">
          {currentBoard.team_name}
        </span>
      )}

      {/* Work item count */}
      <span className="tt-metric-chip" title={`${totalWi} total work items on this board`}>
        <Layers className="h-3 w-3 text-[var(--tt-primary)]" />
        {totalWi} items
      </span>

      {/* Selection chip */}
      {hasSelection && (
        <span
          className="tt-metric-chip"
          style={{
            background: "rgba(111,154,201,0.12)",
            borderColor: "rgba(111,154,201,0.3)",
            color: "var(--tt-primary)",
          }}
          title={`${selectedCount} items selected for generation`}
        >
          <CheckSquare className="h-3 w-3" />
          {selectedCount} selected
        </span>
      )}

      {/* Type breakdown mini-pills */}
      <TypeBreakdown rows={boardView.rows} />

      <div className="flex-1" />

      {/* E2E last run summary */}
      {lastRun && (
        <>
          <span
            className="tt-metric-chip"
            style={{
              background: lastRun.failed === 0
                ? "rgba(61,143,102,0.12)"
                : "rgba(192,95,95,0.12)",
              borderColor: lastRun.failed === 0
                ? "rgba(61,143,102,0.3)"
                : "rgba(192,95,95,0.3)",
              color: lastRun.failed === 0
                ? "var(--tt-success)"
                : "var(--tt-danger)",
            }}
            title={`Last E2E run: ${lastRun.passed} passed, ${lastRun.failed} failed, ${lastRun.skipped} skipped`}
          >
            {lastRun.failed === 0 ? (
              <CheckCircle2 className="h-3 w-3" />
            ) : (
              <XCircle className="h-3 w-3" />
            )}
            E2E {lastRun.passed}/{lastRun.total}
          </span>
          <span
            className="tt-metric-chip"
            title={`Last E2E run finished at ${new Date(lastRun.finished_at * 1000).toLocaleString()}`}
          >
            <Clock className="h-3 w-3 text-[var(--tt-text-muted)]" />
            {timeSince(lastRun.finished_at)}
          </span>
        </>
      )}
    </div>
  );
}

/**
 * Shows up to 3 small type-count chips (User Story, Bug, Task) without taking
 * too much horizontal space.
 */
function TypeBreakdown({
  rows,
}: {
  rows: { wi_type: string }[];
}) {
  if (!rows.length) return null;

  // Count by normalized type key
  const counts: Record<string, number> = {};
  for (const r of rows) {
    const k = normalizeType(r.wi_type);
    counts[k] = (counts[k] ?? 0) + 1;
  }

  const ORDER = ["story", "bug", "task", "epic", "feature", "other"];
  const TYPE_COLOR: Record<string, string> = {
    story:   "var(--tt-type-story)",
    bug:     "var(--tt-type-bug)",
    task:    "var(--tt-type-task)",
    epic:    "var(--tt-type-epic)",
    feature: "var(--tt-type-feature)",
    other:   "var(--tt-text-muted)",
  };
  const TYPE_BG: Record<string, string> = {
    story:   "var(--tt-type-story-bg)",
    bug:     "var(--tt-type-bug-bg)",
    task:    "var(--tt-type-task-bg)",
    epic:    "var(--tt-type-epic-bg)",
    feature: "var(--tt-type-feature-bg)",
    other:   "rgba(138,143,153,0.10)",
  };
  const TYPE_LABEL: Record<string, string> = {
    story: "Story",
    bug: "Bug",
    task: "Task",
    epic: "Epic",
    feature: "Feature",
    other: "Other",
  };

  return (
    <>
      {ORDER.filter((k) => counts[k] > 0)
        .slice(0, 4)
        .map((k) => (
          <span
            key={k}
            className="tt-metric-chip"
            style={{
              background: TYPE_BG[k],
              borderColor: TYPE_COLOR[k] + "44",
              color: TYPE_COLOR[k],
            }}
            title={`${counts[k]} ${TYPE_LABEL[k]} work item${counts[k] === 1 ? "" : "s"}`}
          >
            <span
              className="tt-chip-dot"
              style={{ background: TYPE_COLOR[k] }}
            />
            {counts[k]} {TYPE_LABEL[k]}
          </span>
        ))}
    </>
  );
}

function normalizeType(t: string): string {
  const k = (t || "").toLowerCase();
  if (k.includes("story") || k.includes("user story")) return "story";
  if (k.includes("bug") || k.includes("issue")) return "bug";
  if (k.includes("task")) return "task";
  if (k.includes("epic")) return "epic";
  if (k.includes("feature")) return "feature";
  return "other";
}
