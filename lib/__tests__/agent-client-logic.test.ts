import { describe, it, expect } from "vitest";
import { sortWiIds, splitWiIds, agentLogLevel } from "../agent-client";
import { humanSize } from "../../components/ui/download-links";

describe("sortWiIds", () => {
  it("numeric ids ascending, before string keys", () => {
    expect(sortWiIds([3, "PROJ-2", 1, "ABC-1"])).toEqual([
      1, 3, "ABC-1", "PROJ-2",
    ]);
  });
  it("does not mutate input", () => {
    const input = [2, 1];
    const out = sortWiIds(input);
    expect(input).toEqual([2, 1]);
    expect(out).toEqual([1, 2]);
  });
  it("string keys sort lexicographically", () => {
    expect(sortWiIds(["PROJ-10", "PROJ-2"])).toEqual(["PROJ-10", "PROJ-2"]);
  });
});

describe("splitWiIds", () => {
  it("separates ADO numeric ids from JIRA string keys", () => {
    expect(splitWiIds([1, "PROJ-2", 3])).toEqual({
      ids: [1, 3],
      keys: ["PROJ-2"],
    });
  });
  it("coerces digit-strings into numeric ids", () => {
    expect(splitWiIds(["42", "PROJ-1"])).toEqual({
      ids: [42],
      keys: ["PROJ-1"],
    });
  });
  it("skips blank/whitespace-only entries", () => {
    expect(splitWiIds(["", "   ", "PROJ-1"])).toEqual({
      ids: [],
      keys: ["PROJ-1"],
    });
  });
  it("handles empty input", () => {
    expect(splitWiIds([])).toEqual({ ids: [], keys: [] });
  });
});

describe("agentLogLevel", () => {
  it("maps known prefixes", () => {
    expect(agentLogLevel("[ERROR] boom")).toBe("ERROR");
    expect(agentLogLevel("[SUCCESS] ok")).toBe("SUCCESS");
    expect(agentLogLevel("  [WARN] careful")).toBe("WARN");
    expect(agentLogLevel("[WARNING] careful")).toBe("WARN");
    expect(agentLogLevel("[INFO] fyi")).toBe("INFO");
  });
  it("defaults to INFO for unprefixed lines", () => {
    expect(agentLogLevel("plain line")).toBe("INFO");
  });
});

describe("humanSize", () => {
  it("formats byte sizes with units", () => {
    expect(humanSize(0)).toBe("");
    expect(humanSize(-5)).toBe("");
    expect(humanSize(512)).toBe("512 B");
    expect(humanSize(1024)).toBe("1.0 KB");
    expect(humanSize(1536)).toBe("1.5 KB");
    expect(humanSize(1024 * 1024)).toBe("1.0 MB");
    expect(humanSize(1024 * 1024 * 1024)).toBe("1.0 GB");
  });
});
