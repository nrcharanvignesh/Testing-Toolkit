"use client";

/**
 * board-lanes.ts
 * localStorage-backed collapsed state for the work-item grid's row groups
 * (swim lanes / board columns like "NEW", "BACKLOG", "IN DEVELOPMENT").
 *
 * Clicking a lane header caret collapses that group so its rows are hidden;
 * the state is persisted immediately and restored automatically on the next
 * launch. Lanes are keyed by their display name (the same name is reused
 * across boards, matching the desktop app's by-name collapse behavior).
 */

import { useCallback, useSyncExternalStore } from "react";

const KEY = "tt.board.lanes.v1";

/** Set of collapsed lane names. */
let cache: Set<string> = new Set();
let loaded = false;
const listeners = new Set<() => void>();

function load(): Set<string> {
  if (typeof window === "undefined") return new Set();
  try {
    const raw = window.localStorage.getItem(KEY);
    if (!raw) return new Set();
    const parsed = JSON.parse(raw);
    if (Array.isArray(parsed))
      return new Set(parsed.filter((v) => typeof v === "string"));
    return new Set();
  } catch {
    return new Set();
  }
}

function ensureLoaded() {
  if (!loaded) {
    cache = load();
    loaded = true;
  }
}

function persist(next: Set<string>) {
  cache = next;
  if (typeof window !== "undefined") {
    try {
      window.localStorage.setItem(KEY, JSON.stringify([...next]));
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

function getSnapshot(): Set<string> {
  ensureLoaded();
  return cache;
}

const EMPTY: Set<string> = new Set();
function getServerSnapshot(): Set<string> {
  return EMPTY;
}

/** Non-hook toggle for a lane's collapsed flag (always persisted). */
export function toggleLaneCollapsed(lane: string) {
  ensureLoaded();
  const next = new Set(cache);
  if (next.has(lane)) next.delete(lane);
  else next.add(lane);
  persist(next);
}

/** Collapse or expand every currently-visible lane at once. */
export function setAllLanesCollapsed(lanes: string[], collapsed: boolean) {
  ensureLoaded();
  const next = new Set(cache);
  for (const lane of lanes) {
    if (collapsed) next.add(lane);
    else next.delete(lane);
  }
  persist(next);
}

export function useCollapsedLanes() {
  const state = useSyncExternalStore(subscribe, getSnapshot, getServerSnapshot);
  const isCollapsed = useCallback((lane: string) => state.has(lane), [state]);
  return {
    collapsed: state,
    isCollapsed,
    toggle: toggleLaneCollapsed,
    setAll: setAllLanesCollapsed,
  };
}
