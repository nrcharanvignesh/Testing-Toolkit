import { describe, it, expect } from "vitest";
import { PROMPT_SCOPES } from "../../components/dialogs/ProjectKbDialog";
import { TC_TYPES, TC_DISPLAY_NAME } from "../agent-client";

describe("ProjectKbDialog > PROMPT_SCOPES", () => {
  it("is a non-empty array of scope objects", () => {
    expect(Array.isArray(PROMPT_SCOPES)).toBe(true);
    expect(PROMPT_SCOPES.length).toBeGreaterThan(0);
  });

  it("each entry has a value and label string", () => {
    for (const scope of PROMPT_SCOPES) {
      expect(typeof scope.value).toBe("string");
      expect(typeof scope.label).toBe("string");
      expect(scope.label.length).toBeGreaterThan(0);
    }
  });

  it("includes a default/general scope with empty string value", () => {
    const general = PROMPT_SCOPES.find((s) => s.value === "");
    expect(general).toBeDefined();
    expect(general!.label.toLowerCase()).toContain("general");
  });

  it("includes implementation, sit, and uat scopes", () => {
    const values = PROMPT_SCOPES.map((s) => s.value);
    expect(values).toContain("implementation");
    expect(values).toContain("sit");
    expect(values).toContain("uat");
  });

  it("has no duplicate values", () => {
    const values = PROMPT_SCOPES.map((s) => s.value);
    expect(new Set(values).size).toBe(values.length);
  });
});

describe("ProjectKbDialog > TC_TYPES / TC_DISPLAY_NAME contract", () => {
  it("TC_TYPES contains the expected test case phases", () => {
    expect(TC_TYPES).toContain("implementation");
    expect(TC_TYPES).toContain("sit");
    expect(TC_TYPES).toContain("uat");
  });

  it("TC_DISPLAY_NAME has an entry for every TC_TYPE", () => {
    for (const t of TC_TYPES) {
      expect(TC_DISPLAY_NAME[t]).toBeDefined();
      expect(typeof TC_DISPLAY_NAME[t]).toBe("string");
      expect(TC_DISPLAY_NAME[t].length).toBeGreaterThan(0);
    }
  });

  it("TC_DISPLAY_NAME keys exactly match TC_TYPES", () => {
    const keys = Object.keys(TC_DISPLAY_NAME).sort();
    const types = [...TC_TYPES].sort();
    expect(keys).toEqual(types);
  });
});

describe("ProjectKbDialog > module imports without error", () => {
  it("exports ProjectKbDialog as a function", async () => {
    const mod = await import("../../components/dialogs/ProjectKbDialog");
    expect(typeof mod.ProjectKbDialog).toBe("function");
  });
});
