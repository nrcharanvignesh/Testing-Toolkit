import { describe, it, expect, beforeEach, vi } from "vitest";

// Minimal localStorage stub (same pattern as board-lanes.test.ts).
function installLocalStorage(): Map<string, string> {
  const store = new Map<string, string>();
  const ls = {
    getItem: (k: string) => (store.has(k) ? store.get(k)! : null),
    setItem: (k: string, v: string) => void store.set(k, String(v)),
    removeItem: (k: string) => void store.delete(k),
    clear: () => store.clear(),
  };
  vi.stubGlobal("window", { localStorage: ls });
  vi.stubGlobal("localStorage", ls);
  return store;
}

describe("board-columns constants", () => {
  beforeEach(() => {
    vi.resetModules();
    installLocalStorage();
  });

  it("BOARD_COLUMNS has 7 entries each with id, label, and min width", async () => {
    const { BOARD_COLUMNS } = await import("../board-columns");
    expect(BOARD_COLUMNS).toHaveLength(7);
    for (const col of BOARD_COLUMNS) {
      expect(typeof col.id).toBe("string");
      expect(col.id.length).toBeGreaterThan(0);
      expect(typeof col.label).toBe("string");
      expect(col.label.length).toBeGreaterThan(0);
      expect(typeof col.min).toBe("number");
      expect(col.min).toBeGreaterThan(0);
    }
  });

  it("COLLAPSED_WIDTH is a positive number", async () => {
    const { COLLAPSED_WIDTH } = await import("../board-columns");
    expect(typeof COLLAPSED_WIDTH).toBe("number");
    expect(COLLAPSED_WIDTH).toBeGreaterThan(0);
  });
});

describe("columnWidth pure function", () => {
  beforeEach(() => {
    vi.resetModules();
    installLocalStorage();
  });

  it("returns default width when column is not in state", async () => {
    const { columnWidth, BOARD_COLUMNS } = await import("../board-columns");
    const emptyState = { widths: {}, collapsed: {} };
    // Each column should return its defined default width
    for (const col of BOARD_COLUMNS) {
      expect(columnWidth(emptyState, col.id)).toBe(col.width);
    }
  });

  it("returns stored width when present in state", async () => {
    const { columnWidth } = await import("../board-columns");
    const state = { widths: { title: 500 }, collapsed: {} };
    expect(columnWidth(state, "title")).toBe(500);
  });

  it("returns COLLAPSED_WIDTH when column is collapsed", async () => {
    const { columnWidth, COLLAPSED_WIDTH } = await import("../board-columns");
    const state = { widths: { title: 500 }, collapsed: { title: true } };
    expect(columnWidth(state, "title")).toBe(COLLAPSED_WIDTH);
  });

  it("collapsed takes precedence over stored width", async () => {
    const { columnWidth, COLLAPSED_WIDTH } = await import("../board-columns");
    const state = {
      widths: { assignee: 200 },
      collapsed: { assignee: true },
    };
    expect(columnWidth(state, "assignee")).toBe(COLLAPSED_WIDTH);
  });
});
