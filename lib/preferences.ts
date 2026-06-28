"use client";

/**
 * preferences.ts
 * Small localStorage-backed store for persistent UI preferences:
 *   - panels: which layout regions are visible (nav, detail, log). The default
 *     layout shows nav + detail and hides the log/progress bar.
 *   - sizes: pixel sizes for the free-hand resizable regions (nav width,
 *     detail-pane width, log-panel height).
 *   - tourCompleted: whether the first-run guided tour has run.
 *
 * Visibility/tour changes are written to localStorage immediately. Live resize
 * drags update the in-memory value continuously but only flush to disk on
 * commit (pointer up) to avoid hammering localStorage, so the layout the user
 * left behind is restored verbatim on the next launch.
 */

import { useCallback, useSyncExternalStore } from "react";

export type PanelKey = "nav" | "detail" | "log";
export type SizeKey = "navWidth" | "detailWidth" | "logHeight";

export interface UiPreferences {
  /** true = visible. Default shows nav + detail, hides the log. */
  panels: Record<PanelKey, boolean>;
  /** pixel sizes for the resizable regions. */
  sizes: Record<SizeKey, number>;
  /** true once the user has finished (or skipped) the guided tour. */
  tourCompleted: boolean;
  /**
   * Set by the Reinstall flow before the agent restarts. Survives the page
   * reload so that, once the freshly-reinstalled agent reconnects, the app
   * automatically re-indexes every knowledge base. Cleared when reindexing
   * finishes. (A reinstall keeps settings, fetched models and these prefs.)
   */
  pendingReindex: boolean;
  /**
   * Set by the Reinstall flow. While true the app forces the Step 1
   * download/install onboarding screen (even if the old agent is still
   * connected) so the user re-downloads and re-runs the installer. Cleared once
   * the freshly reinstalled agent reconnects and the user continues.
   */
  pendingReinstall: boolean;
}

const KEY = "tt.ui.prefs.v3";

// Default launch layout: the left-hand nav rail and the detail panel are shown,
// the log/progress bar is hidden (progress is surfaced inline so users don't
// have to rely on the log). The tour has not run yet.
const DEFAULTS: UiPreferences = {
  panels: { nav: true, detail: true, log: false },
  sizes: { navWidth: 224, detailWidth: 440, logHeight: 180 },
  tourCompleted: false,
  pendingReindex: false,
  pendingReinstall: false,
};

let cache: UiPreferences = DEFAULTS;
let loaded = false;
const listeners = new Set<() => void>();

function load(): UiPreferences {
  if (typeof window === "undefined") return DEFAULTS;
  try {
    const raw = window.localStorage.getItem(KEY);
    if (!raw) return DEFAULTS;
    const parsed = JSON.parse(raw) as Partial<UiPreferences>;
    return {
      panels: { ...DEFAULTS.panels, ...(parsed.panels ?? {}) },
      sizes: { ...DEFAULTS.sizes, ...(parsed.sizes ?? {}) },
      tourCompleted: !!parsed.tourCompleted,
      pendingReindex: !!parsed.pendingReindex,
      pendingReinstall: !!parsed.pendingReinstall,
    };
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

function persist(next: UiPreferences, write = true) {
  cache = next;
  if (write && typeof window !== "undefined") {
    try {
      window.localStorage.setItem(KEY, JSON.stringify(next));
    } catch {
      /* storage may be unavailable (private mode) — keep in-memory copy */
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

function getSnapshot(): UiPreferences {
  ensureLoaded();
  return cache;
}

function getServerSnapshot(): UiPreferences {
  return DEFAULTS;
}

/** Non-hook read for one-shot reads (e.g. initial useState values). */
export function getPreferences(): UiPreferences {
  ensureLoaded();
  return cache;
}

/** Non-hook setter for panel visibility (always persisted). */
export function setPanelPref(key: PanelKey, visible: boolean) {
  ensureLoaded();
  persist({ ...cache, panels: { ...cache.panels, [key]: visible } });
}

/**
 * Non-hook setter for a resizable size. Pass write=false during a live drag and
 * write=true (the default) once on commit so localStorage isn't hit every frame.
 */
export function setSizePref(key: SizeKey, px: number, write = true) {
  ensureLoaded();
  persist({ ...cache, sizes: { ...cache.sizes, [key]: px } }, write);
}

/** Non-hook setter for the guided-tour completion flag (always persisted). */
export function setTourCompletedPref(value: boolean) {
  ensureLoaded();
  persist({ ...cache, tourCompleted: value });
}

/** Non-hook setter for the pending-reindex flag (always persisted). */
export function setPendingReindexPref(value: boolean) {
  ensureLoaded();
  persist({ ...cache, pendingReindex: value });
}

/** Non-hook setter for the pending-reinstall flag (always persisted). */
export function setPendingReinstallPref(value: boolean) {
  ensureLoaded();
  persist({ ...cache, pendingReinstall: value });
}

export function usePreferences() {
  const prefs = useSyncExternalStore(subscribe, getSnapshot, getServerSnapshot);

  const setPanel = useCallback((key: PanelKey, visible: boolean) => {
    setPanelPref(key, visible);
  }, []);

  const togglePanel = useCallback((key: PanelKey) => {
    ensureLoaded();
    setPanelPref(key, !cache.panels[key]);
  }, []);

  const setSize = useCallback((key: SizeKey, px: number, write = true) => {
    setSizePref(key, px, write);
  }, []);

  const setTourCompleted = useCallback((value: boolean) => {
    setTourCompletedPref(value);
  }, []);

  const setPendingReindex = useCallback((value: boolean) => {
    setPendingReindexPref(value);
  }, []);

  const setPendingReinstall = useCallback((value: boolean) => {
    setPendingReinstallPref(value);
  }, []);

  return {
    prefs,
    setPanel,
    togglePanel,
    setSize,
    setTourCompleted,
    setPendingReindex,
    setPendingReinstall,
  };
}
