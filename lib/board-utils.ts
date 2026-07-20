import type { Board, BoardColumn, WorkItemRow, WiId } from "./agent-client";
import { sortWiIds } from "./agent-client";

/**
 * All User Story / Story work item ids on the board, sorted.
 * Mirrors the desktop main_window._on_generate auto-select: when SIT/UAT is
 * triggered with no ticked items, every User Story is selected automatically.
 */
export function coveredWorkItemIds(
  rows: WorkItemRow[],
  testCases: Array<{ wi_id: string; step_count: number }>
): Set<string> {
  const currentIds = new Set(rows.map((row) => String(row.wi_id)));
  const covered = new Set<string>();
  for (const testCase of testCases) {
    const wiId = String(testCase.wi_id || "");
    if (wiId && currentIds.has(wiId) && testCase.step_count > 0) covered.add(wiId);
  }
  return covered;
}

/**
 * Total test cases traced to each work item, combining:
 *  - tool-generated test cases (from the E2E/generation sidecar, matched by
 *    parent wi_id with at least one step), and
 *  - test cases already linked in the tracker (ADO "Tested By" relations /
 *    JIRA test issue links), carried on the row as linked_test_case_count.
 * Returns a map of wi_id -> count (only entries with count > 0).
 */
export function testCaseCountsByWorkItem(
  rows: WorkItemRow[],
  testCases: Array<{ wi_id: string; step_count: number }>
): Map<string, number> {
  const counts = new Map<string, number>();
  const currentIds = new Set(rows.map((row) => String(row.wi_id)));
  // Tool-generated (sidecar) test cases.
  for (const testCase of testCases) {
    const wiId = String(testCase.wi_id || "");
    if (wiId && currentIds.has(wiId) && testCase.step_count > 0) {
      counts.set(wiId, (counts.get(wiId) ?? 0) + 1);
    }
  }
  // Test cases already linked in the tracker.
  for (const row of rows) {
    const linked = Number(row.linked_test_case_count ?? 0);
    if (linked > 0) {
      const wiId = String(row.wi_id);
      counts.set(wiId, (counts.get(wiId) ?? 0) + linked);
    }
  }
  return counts;
}

export function userStoryIds(rows: WorkItemRow[]): WiId[] {
  return sortWiIds(
    rows
      .filter((r) => {
        const t = (r.wi_type || "").toLowerCase();
        return t === "user story" || t === "story";
      })
      .map((r) => r.wi_id)
  );
}

/** Work-item source backend for a project. Mirrors core/source_types.py. */
export type ProjectSource = "ado" | "jira";

/**
 * Resolve a (possibly source-suffixed) project name to its backend.
 *
 * When BOTH sources are configured the agent appends " - ADO" / " - JIRA"
 * to project names (core/source_types.SOURCE_SUFFIXES). When only one source
 * is configured names are unsuffixed, so we fall back to whichever source is
 * actually configured (JIRA-only setups resolve to "jira", else "ado").
 */
export function projectSourceType(
  full: string,
  opts: { jiraConfigured?: boolean; adoConfigured?: boolean } = {}
): ProjectSource {
  const name = full.trimEnd();
  if (/\s-\sJIRA$/i.test(name)) return "jira";
  if (/\s-\sADO$/i.test(name)) return "ado";
  // Single-source setup: no suffix — infer from what's configured.
  if (opts.jiraConfigured && !opts.adoConfigured) return "jira";
  return "ado";
}

export const NO_COLUMN = "(no board column)";
export const UNASSIGNED = "(unassigned)";
export const NO_ITER = "(no iteration)";
export const ALL = "(all)";

// Status colors (theme.py COLOR_*)
export const COLOR_SUCCESS = "#10b981";
export const COLOR_DANGER = "#f43f5e";
export const COLOR_WARN = "#f59e0b";
export const COLOR_INFO = "#3b82f6";
export const COLOR_MUTED = "#94a3b8";

function areaLeaf(areaPath: string): string {
  if (!areaPath) return "";
  return areaPath.replace(/\//g, "\\").split("\\").pop()?.trim() ?? "";
}

/** ado/boards.py group_rows_by_column */
export function groupRowsByColumn(
  rows: WorkItemRow[],
  columns: BoardColumn[]
): Array<[string, WorkItemRow[]]> {
  const known = new Set(columns.map((c) => c.name));
  const buckets = new Map<string, WorkItemRow[]>();
  for (const c of columns) buckets.set(c.name, []);

  const orphans: WorkItemRow[] = [];
  for (const r of rows) {
    if (r.board_column && known.has(r.board_column)) {
      buckets.get(r.board_column)!.push(r);
    } else {
      orphans.push(r);
    }
  }
  const out: Array<[string, WorkItemRow[]]> = [];
  const emitted = new Set<string>();
  for (const c of columns) {
    // A board can return multiple columns sharing a display name (e.g. split
    // "Doing" columns). Their rows already collapse into a single bucket, so
    // only emit each lane once to keep React keys unique.
    if (emitted.has(c.name)) continue;
    emitted.add(c.name);
    const list = buckets.get(c.name)!;
    if (list.length) out.push([c.name, sortRows(list)]);
  }
  if (orphans.length) out.push([NO_COLUMN, sortRows(orphans)]);
  return out;
}

function sortRows(rows: WorkItemRow[]): WorkItemRow[] {
  return [...rows].sort((a, b) => {
    const t = (a.wi_type || "").toLowerCase().localeCompare(
      (b.wi_type || "").toLowerCase()
    );
    if (t !== 0) return t;
    return areaLeaf(a.area_path)
      .toLowerCase()
      .localeCompare(areaLeaf(b.area_path).toLowerCase());
  });
}

export function uniqueSorted(values: string[]): string[] {
  return Array.from(new Set(values.filter(Boolean))).sort((a, b) =>
    a.toLowerCase().localeCompare(b.toLowerCase())
  );
}

/**
 * Resolve the boards a project should actually export/display:
 * prefer Stories-suffixed boards, then dedupe by team_name (ADO can return
 * multiple Board entries per team - e.g. a stale/duplicate team-board
 * pairing - and only one is the real one). This mirrors app-state.tsx's
 * reloadBoards() exactly, so every code path that lists a project's boards
 * (single-project browsing, All Boards export, All Projects export) resolves
 * to the identical board set instead of silently diverging.
 */
export function dedupeStoryBoards(all: Board[]): Board[] {
  const stories = all.filter((b) => (b.name || "").toLowerCase().includes("stories"));
  const flat = stories.length ? stories : all;
  const seen = new Set<string>();
  const deduped: Board[] = [];
  for (const b of flat) {
    if (stories.length) {
      if (seen.has(b.team_name)) continue;
      seen.add(b.team_name);
    }
    deduped.push(b);
  }
  return deduped;
}
