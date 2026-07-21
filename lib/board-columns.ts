"use client";

/**
 * board-columns.ts
 * localStorage-backed state for the work-item grid's columns:
 *   - per-column pixel width (Excel-like drag-to-resize), and
 *   - per-column collapsed flag (click the header caret to hide/show a column).
 *
 * State is global (the column set is fixed) and persisted immediately on every
 * commit so the layout the user left behind is restored automatically on the
 * next launch. Live resize drags update the in-memory value continuously and
 * flush to disk only on pointer-up to avoid hammering localStorage.
 */

import { useCallback, useSyncExternalStore } from "react";

/** Stable identifiers for every resizable/collapsible grid column. The leading
 * checkbox column is intentionally excluded (fixed width, always visible). */
export type BoardColumnId =
  | "id"
  | "title"
  | "type"
  | "state"
  | "assignee"
  | "sprint"
  | "tests";

export interface BoardColumnMeta {
  id: BoardColumnId;
  label: string;
  /** Default pixel width. */
  width: number;
  /** Minimum pixel width when resizing. */
  min: number;
}

/** Column order + defaults (matches the desktop board layout). */
export const BOARD_COLUMNS: readonly BoardColumnMeta[] = [
  { id: "id", label: "ID", width: 90, min: 60 },
  { id: "title", label: "Title", width: 380, min: 160 },
  { id: "type", label: "Type", width: 110, min: 80 },
  { id: "state", label: "State", width: 140, min: 90 },
  { id: "assignee", label: "Assignee", width: 140, min: 90 },
  { id: "sprint", label: "Sprint", width: 150, min: 90 },
  { id: "tests", label: "Generated Tests", width: 120, min: 90 },
] as const;

/** Pixel width of a collapsed column (just enough for the expand caret). */
export const COLLAPSED_WIDTH = 30;

interface ColumnState {
  widths: Partial<Record<BoardColumnId, number>>;
  collapsed: Partial<Record<BoardColumnId, boolean>>;
}

const KEY = "tt.board.columns.v1";

const DEFAULTS: ColumnState = { widths: {}, collapsed: {} };

let cache: ColumnState = DEFAULTS;
let loaded = false;
const listeners = new Set<() => void>();

const VALID_IDS = new Set<string>(BOARD_COLUMNS.map((c) => c.id));

function load(): ColumnState {
  if (typeof window === "undefined") return DEFAULTS;
  try {
    const raw = window.localStorage.getItem(KEY);
    if (!raw) return DEFAULTS;
    const parsed = JSON.parse(raw) as Partial<ColumnState>;
    const widths: Partial<Record<BoardColumnId, number>> = {};
    const collapsed: Partial<Record<BoardColumnId, boolean>> = {};
    for (const [k, v] of Object.entries(parsed.widths ?? {})) {
      if (VALID_IDS.has(k) && typeof v === "number" && v > 0)
        widths[k as BoardColumnId] = v;
    }
    for (const [k, v] of Object.entries(parsed.collapsed ?? {})) {
      if (VALID_IDS.has(k) && v === true) collapsed[k as BoardColumnId] = true;
    }
    return { widths, collapsed };
  } catch {
    return DEFAULTS;
  }
}

function ensureLoaded() {
  if (!loaded) {
    cache = load();
    loaded = true;
  }
}

function persist(next: ColumnState, write = true) {
  cache = next;
  if (write && typeof window !== "undefined") {
    try {
      window.localStorage.setItem(KEY, JSON.stringify(next));
    } catch {
      /* storage unavailable (private mode) — keep in-memory copy */
    }
  }
  for (const l of listeners) l();
}

function subscribe(cb: () => void): () => void {
  listeners.add(cb);
  return () => {
    listeners.delete(cb);
  };
}

function getSnapshot(): ColumnState {
  ensureLoaded();
  return cache;
}

function getServerSnapshot(): ColumnState {
  return DEFAULTS;
}

const META_BY_ID = new Map<BoardColumnId, BoardColumnMeta>(
  BOARD_COLUMNS.map((c) => [c.id, c])
);

/** Effective width for a column (collapsed → COLLAPSED_WIDTH). */
export function columnWidth(state: ColumnState, id: BoardColumnId): number {
  if (state.collapsed[id]) return COLLAPSED_WIDTH;
  return state.widths[id] ?? META_BY_ID.get(id)?.width ?? 120;
}

/**
 * Auto-fit column widths to fill the container on first launch (no persisted
 * widths). Distributes extra space proportionally across all columns so the
 * grid uses the full viewport width instead of leaving a gap on the right.
 * Only runs once -- after any manual resize, localStorage takes over.
 */
export function autofitColumns(containerWidth: number): void {
  ensureLoaded();
  if (Object.keys(cache.widths).length > 0) return;
  const CHECKBOX_COL = 32;
  const available = containerWidth - CHECKBOX_COL;
  const defaultTotal = BOARD_COLUMNS.reduce((s, c) => s + c.width, 0);
  if (available <= defaultTotal) return;
  const scale = available / defaultTotal;
  const widths: Partial<Record<BoardColumnId, number>> = {};
  for (const c of BOARD_COLUMNS) {
    widths[c.id] = Math.max(c.min, Math.round(c.width * scale));
  }
  persist({ ...cache, widths }, true);
}

/** Non-hook setter for a column width. Pass write=false during a live drag. */
function setColumnWidth(id: BoardColumnId, px: number, write = true) {
  ensureLoaded();
  const min = META_BY_ID.get(id)?.min ?? 60;
  const clamped = Math.max(min, Math.round(px));
  persist(
    { ...cache, widths: { ...cache.widths, [id]: clamped } },
    write
  );
}

/** Non-hook toggle for a column's collapsed flag (always persisted). */
function toggleColumnCollapsed(id: BoardColumnId) {
  ensureLoaded();
  const next = !cache.collapsed[id];
  persist({
    ...cache,
    collapsed: { ...cache.collapsed, [id]: next },
  });
}

/** Reset all columns to their default widths and expand every column. */
export function resetBoardColumns() {
  persist({ widths: {}, collapsed: {} });
}

export function useBoardColumns() {
  const state = useSyncExternalStore(
    subscribe,
    getSnapshot,
    getServerSnapshot
  );

  const width = useCallback(
    (id: BoardColumnId) => columnWidth(state, id),
    [state]
  );
  const isCollapsed = useCallback(
    (id: BoardColumnId) => !!state.collapsed[id],
    [state]
  );

  return {
    state,
    width,
    isCollapsed,
    setWidth: setColumnWidth,
    toggleCollapsed: toggleColumnCollapsed,
    reset: resetBoardColumns,
    autofit: autofitColumns,
  };
}
