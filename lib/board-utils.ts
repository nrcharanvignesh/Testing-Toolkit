import type { BoardColumn, WorkItemRow } from "./agent-client";

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

/** board_grid.py _state_color */
export function stateColor(state: string): string {
  const s = (state || "").toLowerCase();
  if (
    ["done", "closed", "accept", "passed", "complete", "resolved"].some((k) =>
      s.includes(k)
    )
  )
    return COLOR_SUCCESS;
  if (s.includes("block")) return COLOR_DANGER;
  if (["qa", "review", "test", "verify"].some((k) => s.includes(k)))
    return COLOR_WARN;
  if (["active", "progress", "development", "dev", "doing"].some((k) =>
    s.includes(k)
  ))
    return COLOR_INFO;
  return COLOR_MUTED;
}

/** board_grid.py _type_color */
export function typeColor(wiType: string): string | null {
  const t = (wiType || "").toLowerCase();
  if (t.includes("bug") || t.includes("defect")) return "#f87171";
  if (t.includes("user story") || t.includes("enhancement") || t.includes("story"))
    return "#4ade80";
  if (t.includes("test case") || t.includes("case")) return "#60a5fa";
  return null;
}

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
  for (const c of columns) {
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
