"use client";

/**
 * CoverageBar
 * A compact, single-row strip shown between ActionBar and BoardGrid that gives
 * the Senior QA engineer at-a-glance coverage and health metrics without
 * opening any dialog. Reads entirely from app-state — zero extra API calls.
 */

import { Layers, CheckSquare } from "lucide-react";
import { useAppState } from "@/lib/app-state";

export function CoverageBar() {
  const { boardView, selected, currentBoard } = useAppState();

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
            background: "rgba(91,168,255,0.10)",
            borderColor: "rgba(91,168,255,0.3)",
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
