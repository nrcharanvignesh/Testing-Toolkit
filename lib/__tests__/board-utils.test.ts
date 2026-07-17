import { describe, it, expect, vi } from "vitest";

vi.mock("@/lib/agent-client", () => ({
  sortWiIds: (ids: any[]) => [...ids].sort(),
}));

import {
  coveredWorkItemIds,
  testCaseCountsByWorkItem,
  userStoryIds,
  projectSourceType,
  groupRowsByColumn,
  uniqueSorted,
  NO_COLUMN,
  UNASSIGNED,
  NO_ITER,
  ALL,
  COLOR_SUCCESS,
  COLOR_DANGER,
  COLOR_WARN,
  COLOR_INFO,
  COLOR_MUTED,
} from "../board-utils";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

interface WorkItemRow {
  wi_id: string | number;
  wi_type: string;
  title: string;
  assigned_to: string;
  area_path: string;
  board_column: string;
  board_lane: string;
  linked_test_case_count?: number;
  state: string;
  tags: string[];
  iteration_path: string;
}

interface BoardColumn {
  id: string;
  name: string;
  column_type: string;
}

function makeRow(overrides: Partial<WorkItemRow> = {}): WorkItemRow {
  return {
    wi_id: "1",
    wi_type: "User Story",
    title: "title",
    assigned_to: "dev",
    area_path: "Team\\Area",
    board_column: "Doing",
    board_lane: "",
    state: "Active",
    tags: [],
    iteration_path: "Sprint 1",
    ...overrides,
  };
}

function makeColumn(name: string): BoardColumn {
  return { id: name.toLowerCase().replace(/\s/g, "-"), name, column_type: "inProgress" };
}

// ---------------------------------------------------------------------------
// coveredWorkItemIds
// ---------------------------------------------------------------------------

describe("coveredWorkItemIds", () => {
  it("returns empty set when both inputs are empty", () => {
    const result = coveredWorkItemIds([], []);
    expect(result.size).toBe(0);
  });

  it("returns empty set when rows exist but no test cases", () => {
    const result = coveredWorkItemIds([makeRow({ wi_id: "10" })], []);
    expect(result.size).toBe(0);
  });

  it("ignores test cases with step_count === 0", () => {
    const rows = [makeRow({ wi_id: "5" })];
    const testCases = [{ wi_id: "5", step_count: 0 }];
    const result = coveredWorkItemIds(rows, testCases);
    expect(result.size).toBe(0);
  });

  it("ignores test cases whose wi_id is not in rows", () => {
    const rows = [makeRow({ wi_id: "1" })];
    const testCases = [{ wi_id: "999", step_count: 3 }];
    const result = coveredWorkItemIds(rows, testCases);
    expect(result.size).toBe(0);
  });

  it("returns covered ids for matching test cases with steps > 0", () => {
    const rows = [
      makeRow({ wi_id: "10" }),
      makeRow({ wi_id: "20" }),
      makeRow({ wi_id: "30" }),
    ];
    const testCases = [
      { wi_id: "10", step_count: 2 },
      { wi_id: "20", step_count: 0 },
      { wi_id: "30", step_count: 5 },
      { wi_id: "40", step_count: 1 },
    ];
    const result = coveredWorkItemIds(rows, testCases);
    expect(result).toEqual(new Set(["10", "30"]));
  });

  it("deduplicates when multiple test cases reference the same wi_id", () => {
    const rows = [makeRow({ wi_id: "7" })];
    const testCases = [
      { wi_id: "7", step_count: 1 },
      { wi_id: "7", step_count: 3 },
    ];
    const result = coveredWorkItemIds(rows, testCases);
    expect(result.size).toBe(1);
    expect(result.has("7")).toBe(true);
  });
});

// ---------------------------------------------------------------------------
// testCaseCountsByWorkItem
// ---------------------------------------------------------------------------

describe("testCaseCountsByWorkItem", () => {
  it("returns empty map when both inputs are empty", () => {
    const result = testCaseCountsByWorkItem([], []);
    expect(result.size).toBe(0);
  });

  it("counts generated test cases with steps > 0", () => {
    const rows = [makeRow({ wi_id: "1" })];
    const testCases = [
      { wi_id: "1", step_count: 2 },
      { wi_id: "1", step_count: 4 },
    ];
    const result = testCaseCountsByWorkItem(rows, testCases);
    expect(result.get("1")).toBe(2);
  });

  it("skips generated test cases with step_count === 0", () => {
    const rows = [makeRow({ wi_id: "1" })];
    const testCases = [
      { wi_id: "1", step_count: 0 },
      { wi_id: "1", step_count: 3 },
    ];
    const result = testCaseCountsByWorkItem(rows, testCases);
    expect(result.get("1")).toBe(1);
  });

  it("adds linked_test_case_count from the row", () => {
    const rows = [makeRow({ wi_id: "1", linked_test_case_count: 5 })];
    const result = testCaseCountsByWorkItem(rows, []);
    expect(result.get("1")).toBe(5);
  });

  it("combines generated + linked counts", () => {
    const rows = [makeRow({ wi_id: "1", linked_test_case_count: 3 })];
    const testCases = [{ wi_id: "1", step_count: 1 }];
    const result = testCaseCountsByWorkItem(rows, testCases);
    // 1 generated + 3 linked = 4
    expect(result.get("1")).toBe(4);
  });

  it("handles missing linked_test_case_count (undefined)", () => {
    const rows = [makeRow({ wi_id: "1" })]; // no linked_test_case_count
    const testCases = [{ wi_id: "1", step_count: 2 }];
    const result = testCaseCountsByWorkItem(rows, testCases);
    expect(result.get("1")).toBe(1);
  });

  it("does not include work items with zero total", () => {
    const rows = [makeRow({ wi_id: "1", linked_test_case_count: 0 })];
    const testCases = [{ wi_id: "1", step_count: 0 }];
    const result = testCaseCountsByWorkItem(rows, testCases);
    expect(result.has("1")).toBe(false);
  });
});

// ---------------------------------------------------------------------------
// userStoryIds
// ---------------------------------------------------------------------------

describe("userStoryIds", () => {
  it("returns empty array for empty rows", () => {
    expect(userStoryIds([])).toEqual([]);
  });

  it("includes 'User Story' (exact case)", () => {
    const rows = [makeRow({ wi_id: "1", wi_type: "User Story" })];
    const result = userStoryIds(rows);
    expect(result).toContain("1");
  });

  it("includes 'story' (lowercase)", () => {
    const rows = [makeRow({ wi_id: "2", wi_type: "story" })];
    const result = userStoryIds(rows);
    expect(result).toContain("2");
  });

  it("includes 'Story' (title case)", () => {
    const rows = [makeRow({ wi_id: "3", wi_type: "Story" })];
    const result = userStoryIds(rows);
    expect(result).toContain("3");
  });

  it("ignores Bug type", () => {
    const rows = [makeRow({ wi_id: "4", wi_type: "Bug" })];
    expect(userStoryIds(rows)).toEqual([]);
  });

  it("ignores Task type", () => {
    const rows = [makeRow({ wi_id: "5", wi_type: "Task" })];
    expect(userStoryIds(rows)).toEqual([]);
  });

  it("filters mixed types and returns sorted ids", () => {
    const rows = [
      makeRow({ wi_id: "30", wi_type: "User Story" }),
      makeRow({ wi_id: "10", wi_type: "Bug" }),
      makeRow({ wi_id: "20", wi_type: "story" }),
      makeRow({ wi_id: "5", wi_type: "Task" }),
    ];
    const result = userStoryIds(rows);
    // sortWiIds mock does [...ids].sort() which is lexicographic
    expect(result).toEqual(["20", "30"]);
  });
});

// ---------------------------------------------------------------------------
// projectSourceType
// ---------------------------------------------------------------------------

describe("projectSourceType", () => {
  it("detects ' - JIRA' suffix", () => {
    expect(projectSourceType("My Project - JIRA")).toBe("jira");
  });

  it("detects ' - ADO' suffix", () => {
    expect(projectSourceType("My Project - ADO")).toBe("ado");
  });

  it("is case insensitive for JIRA suffix", () => {
    expect(projectSourceType("Proj - jira")).toBe("jira");
    expect(projectSourceType("Proj - Jira")).toBe("jira");
  });

  it("is case insensitive for ADO suffix", () => {
    expect(projectSourceType("Proj - ado")).toBe("ado");
    expect(projectSourceType("Proj - Ado")).toBe("ado");
  });

  it("handles trailing whitespace before matching", () => {
    expect(projectSourceType("Proj - JIRA   ")).toBe("jira");
    expect(projectSourceType("Proj - ADO  ")).toBe("ado");
  });

  it("returns 'jira' when no suffix and only jiraConfigured", () => {
    expect(projectSourceType("My Project", { jiraConfigured: true })).toBe("jira");
  });

  it("returns 'ado' when no suffix and only adoConfigured", () => {
    expect(projectSourceType("My Project", { adoConfigured: true })).toBe("ado");
  });

  it("returns 'jira' when no suffix and jiraConfigured without adoConfigured", () => {
    expect(
      projectSourceType("My Project", { jiraConfigured: true, adoConfigured: false })
    ).toBe("jira");
  });

  it("defaults to 'ado' when no suffix and no opts", () => {
    expect(projectSourceType("My Project")).toBe("ado");
  });

  it("defaults to 'ado' when no suffix and both configured", () => {
    expect(
      projectSourceType("My Project", { jiraConfigured: true, adoConfigured: true })
    ).toBe("ado");
  });
});

// ---------------------------------------------------------------------------
// groupRowsByColumn
// ---------------------------------------------------------------------------

describe("groupRowsByColumn", () => {
  it("assigns rows to their known columns", () => {
    const columns = [makeColumn("To Do"), makeColumn("Doing"), makeColumn("Done")];
    const rows = [
      makeRow({ wi_id: "1", board_column: "To Do" }),
      makeRow({ wi_id: "2", board_column: "Doing" }),
      makeRow({ wi_id: "3", board_column: "Done" }),
    ];
    const result = groupRowsByColumn(rows, columns);
    const names = result.map(([name]) => name);
    expect(names).toContain("To Do");
    expect(names).toContain("Doing");
    expect(names).toContain("Done");
    // Each column has 1 row
    for (const [, items] of result) {
      expect(items).toHaveLength(1);
    }
  });

  it("places rows with unknown board_column into orphans bucket", () => {
    const columns = [makeColumn("Doing")];
    const rows = [
      makeRow({ wi_id: "1", board_column: "Doing" }),
      makeRow({ wi_id: "2", board_column: "Unknown Col" }),
      makeRow({ wi_id: "3", board_column: "" }),
    ];
    const result = groupRowsByColumn(rows, columns);
    const orphanEntry = result.find(([name]) => name === NO_COLUMN);
    expect(orphanEntry).toBeDefined();
    expect(orphanEntry![1]).toHaveLength(2);
  });

  it("does not emit empty columns", () => {
    const columns = [makeColumn("To Do"), makeColumn("Doing"), makeColumn("Done")];
    const rows = [makeRow({ wi_id: "1", board_column: "Doing" })];
    const result = groupRowsByColumn(rows, columns);
    const names = result.map(([name]) => name);
    expect(names).not.toContain("To Do");
    expect(names).not.toContain("Done");
    expect(names).toContain("Doing");
  });

  it("deduplicates columns with the same name", () => {
    // Board can return duplicate column names
    const columns = [makeColumn("Doing"), makeColumn("Doing"), makeColumn("Done")];
    const rows = [
      makeRow({ wi_id: "1", board_column: "Doing" }),
      makeRow({ wi_id: "2", board_column: "Doing" }),
    ];
    const result = groupRowsByColumn(rows, columns);
    const doingEntries = result.filter(([name]) => name === "Doing");
    expect(doingEntries).toHaveLength(1);
    expect(doingEntries[0][1]).toHaveLength(2);
  });

  it("sorts rows within a column by wi_type then area_path", () => {
    const columns = [makeColumn("Doing")];
    const rows = [
      makeRow({ wi_id: "1", wi_type: "Task", area_path: "Team\\Zulu", board_column: "Doing" }),
      makeRow({ wi_id: "2", wi_type: "Bug", area_path: "Team\\Beta", board_column: "Doing" }),
      makeRow({ wi_id: "3", wi_type: "Bug", area_path: "Team\\Alpha", board_column: "Doing" }),
      makeRow({ wi_id: "4", wi_type: "Task", area_path: "Team\\Alpha", board_column: "Doing" }),
    ];
    const result = groupRowsByColumn(rows, columns);
    const doingRows = result.find(([name]) => name === "Doing")![1];
    // "Bug" < "Task" lexicographically
    expect(doingRows[0].wi_type).toBe("Bug");
    expect(doingRows[1].wi_type).toBe("Bug");
    // Within Bug: Alpha < Beta (area_path leaf)
    expect(doingRows[0].area_path).toBe("Team\\Alpha");
    expect(doingRows[1].area_path).toBe("Team\\Beta");
    // Tasks follow
    expect(doingRows[2].wi_type).toBe("Task");
    expect(doingRows[3].wi_type).toBe("Task");
    // Within Task: Alpha < Zulu
    expect(doingRows[2].area_path).toBe("Team\\Alpha");
    expect(doingRows[3].area_path).toBe("Team\\Zulu");
  });

  it("returns empty array when no rows and no columns", () => {
    expect(groupRowsByColumn([], [])).toEqual([]);
  });
});

// ---------------------------------------------------------------------------
// uniqueSorted
// ---------------------------------------------------------------------------

describe("uniqueSorted", () => {
  it("deduplicates values", () => {
    expect(uniqueSorted(["a", "b", "a", "c", "b"])).toEqual(["a", "b", "c"]);
  });

  it("filters out empty strings", () => {
    expect(uniqueSorted(["a", "", "b", ""])).toEqual(["a", "b"]);
  });

  it("sorts case-insensitively", () => {
    const result = uniqueSorted(["banana", "Apple", "cherry", "avocado"]);
    expect(result).toEqual(["Apple", "avocado", "banana", "cherry"]);
  });

  it("returns empty array for empty input", () => {
    expect(uniqueSorted([])).toEqual([]);
  });

  it("returns empty array when all values are empty strings", () => {
    expect(uniqueSorted(["", "", ""])).toEqual([]);
  });

  it("deduplication is case-sensitive (distinct casing kept as separate entries)", () => {
    const result = uniqueSorted(["Foo", "foo"]);
    // Set treats "Foo" and "foo" as distinct strings
    expect(result).toHaveLength(2);
    // But sort is case-insensitive so relative order is stable
    expect(result).toEqual(["Foo", "foo"]);
  });
});

// ---------------------------------------------------------------------------
// Constants sanity checks
// ---------------------------------------------------------------------------

describe("exported constants", () => {
  it("NO_COLUMN is the expected string", () => {
    expect(NO_COLUMN).toBe("(no board column)");
  });

  it("UNASSIGNED is the expected string", () => {
    expect(UNASSIGNED).toBe("(unassigned)");
  });

  it("NO_ITER is the expected string", () => {
    expect(NO_ITER).toBe("(no iteration)");
  });

  it("ALL is the expected string", () => {
    expect(ALL).toBe("(all)");
  });

  it("color constants are valid hex", () => {
    const hexRegex = /^#[0-9a-f]{6}$/;
    expect(COLOR_SUCCESS).toMatch(hexRegex);
    expect(COLOR_DANGER).toMatch(hexRegex);
    expect(COLOR_WARN).toMatch(hexRegex);
    expect(COLOR_INFO).toMatch(hexRegex);
    expect(COLOR_MUTED).toMatch(hexRegex);
  });
});
