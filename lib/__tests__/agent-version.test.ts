import { describe, it, expect } from "vitest";
import {
  REQUIRED_AGENT_VERSION,
  compareVersions,
  isAgentOutdated,
} from "../agent-version";

describe("REQUIRED_AGENT_VERSION", () => {
  it("is a non-empty dotted version string", () => {
    expect(REQUIRED_AGENT_VERSION).toMatch(/^\d+\.\d+\.\d+$/);
  });

  it("equals 3.33.3", () => {
    expect(REQUIRED_AGENT_VERSION).toBe("3.33.3");
  });
});

describe("compareVersions", () => {
  describe("equal versions", () => {
    it("returns 0 for identical versions", () => {
      expect(compareVersions("1.0.0", "1.0.0")).toBe(0);
    });

    it("returns 0 for matching multi-digit versions", () => {
      expect(compareVersions("10.20.30", "10.20.30")).toBe(0);
    });

    it("returns 0 for single-part versions", () => {
      expect(compareVersions("5", "5")).toBe(0);
    });
  });

  describe("a < b", () => {
    it("returns -1 when major is less", () => {
      expect(compareVersions("1.0.0", "2.0.0")).toBe(-1);
    });

    it("returns -1 when minor is less", () => {
      expect(compareVersions("1.2.0", "1.3.0")).toBe(-1);
    });

    it("returns -1 when patch is less", () => {
      expect(compareVersions("1.2.3", "1.2.4")).toBe(-1);
    });
  });

  describe("a > b", () => {
    it("returns 1 when major is greater", () => {
      expect(compareVersions("3.0.0", "2.0.0")).toBe(1);
    });

    it("returns 1 when minor is greater", () => {
      expect(compareVersions("1.5.0", "1.4.0")).toBe(1);
    });

    it("returns 1 when patch is greater", () => {
      expect(compareVersions("1.2.9", "1.2.8")).toBe(1);
    });
  });

  describe("multi-digit parts (numeric, not lexicographic)", () => {
    it("1.10.0 > 1.9.0", () => {
      expect(compareVersions("1.10.0", "1.9.0")).toBe(1);
    });

    it("2.0.10 > 2.0.9", () => {
      expect(compareVersions("2.0.10", "2.0.9")).toBe(1);
    });

    it("10.0.0 > 9.0.0", () => {
      expect(compareVersions("10.0.0", "9.0.0")).toBe(1);
    });

    it("1.100.0 > 1.99.0", () => {
      expect(compareVersions("1.100.0", "1.99.0")).toBe(1);
    });
  });

  describe("different lengths", () => {
    it("treats 1.0 as equal to 1.0.0 (missing parts default to 0)", () => {
      expect(compareVersions("1.0", "1.0.0")).toBe(0);
    });

    it("treats 1.0.0.0 as equal to 1.0.0", () => {
      expect(compareVersions("1.0.0.0", "1.0.0")).toBe(0);
    });

    it("1.0.1 > 1.0 (extra part is nonzero)", () => {
      expect(compareVersions("1.0.1", "1.0")).toBe(1);
    });

    it("2 == 2.0.0", () => {
      expect(compareVersions("2", "2.0.0")).toBe(0);
    });

    it("1.2 < 1.2.1", () => {
      expect(compareVersions("1.2", "1.2.1")).toBe(-1);
    });
  });

  describe("null inputs (treated as 0.0.0)", () => {
    it("null vs null returns 0", () => {
      expect(compareVersions(null, null)).toBe(0);
    });

    it("null vs 0.0.0 returns 0", () => {
      expect(compareVersions(null, "0.0.0")).toBe(0);
    });

    it("null vs 1.0.0 returns -1", () => {
      expect(compareVersions(null, "1.0.0")).toBe(-1);
    });

    it("1.0.0 vs null returns 1", () => {
      expect(compareVersions("1.0.0", null)).toBe(1);
    });
  });

  describe("garbage strings", () => {
    it("treats non-numeric parts as 0", () => {
      expect(compareVersions("abc.def.ghi", "0.0.0")).toBe(0);
    });

    it("partially numeric string parses valid parts", () => {
      // "1.abc.3" -> [1, 0, 3]
      expect(compareVersions("1.abc.3", "1.0.3")).toBe(0);
    });

    it("completely garbage vs a real version returns -1", () => {
      expect(compareVersions("xyz", "1.0.0")).toBe(-1);
    });

    it("garbage with dots still parses each part", () => {
      // "a.b.c" -> [0, 0, 0]
      expect(compareVersions("a.b.c", "0.0.1")).toBe(-1);
    });
  });

  describe("empty strings", () => {
    it("empty string treated as 0.0.0 (falsy)", () => {
      expect(compareVersions("", "0.0.0")).toBe(0);
    });

    it("empty string vs 1.0.0 returns -1", () => {
      expect(compareVersions("", "1.0.0")).toBe(-1);
    });

    it("empty string vs empty string returns 0", () => {
      expect(compareVersions("", "")).toBe(0);
    });
  });
});

describe("isAgentOutdated", () => {
  describe("null/undefined/unknown -> true", () => {
    it("returns true for null", () => {
      expect(isAgentOutdated(null)).toBe(true);
    });

    it("returns true for undefined", () => {
      expect(isAgentOutdated(undefined)).toBe(true);
    });

    it('returns true for "unknown"', () => {
      expect(isAgentOutdated("unknown")).toBe(true);
    });

    it("returns true for empty string", () => {
      expect(isAgentOutdated("")).toBe(true);
    });
  });

  describe("version below required -> true", () => {
    it("returns true for 2.26.0 (one minor below)", () => {
      expect(isAgentOutdated("2.26.0")).toBe(true);
    });

    it("returns true for 2.28.0 (below 3.0.0)", () => {
      expect(isAgentOutdated("2.28.0")).toBe(true);
    });

    it("returns true for 1.99.99 (major below)", () => {
      expect(isAgentOutdated("1.99.99")).toBe(true);
    });

    it("returns true for 0.0.1", () => {
      expect(isAgentOutdated("0.0.1")).toBe(true);
    });

    it("returns true for 2.0.0", () => {
      expect(isAgentOutdated("2.0.0")).toBe(true);
    });
  });

  describe("version equal to required -> false", () => {
    it("returns false for exact match", () => {
      expect(isAgentOutdated(REQUIRED_AGENT_VERSION)).toBe(false);
    });

    it("returns false for 3.33.3", () => {
      expect(isAgentOutdated("3.33.3")).toBe(false);
    });
  });

  describe("version above required -> false", () => {
    it("returns false for 3.0.2 (patch above)", () => {
      expect(isAgentOutdated("3.0.2")).toBe(false);
    });

    it("returns false for 3.1.0 (minor above)", () => {
      expect(isAgentOutdated("3.1.0")).toBe(false);
    });

    it("returns false for 4.0.0 (major above)", () => {
      expect(isAgentOutdated("4.0.0")).toBe(false);
    });

    it("returns false for 99.99.99", () => {
      expect(isAgentOutdated("99.99.99")).toBe(false);
    });
  });

  describe("version with extra parts", () => {
    it("returns false for 3.33.3.1 (extra patch part, still >= required)", () => {
      expect(isAgentOutdated("3.33.3.1")).toBe(false);
    });

    it("returns false for 3.33.3.0 (extra zero part)", () => {
      expect(isAgentOutdated("3.33.3.0")).toBe(false);
    });

    it("returns true for 2.99.9.99 (still below despite extra part)", () => {
      expect(isAgentOutdated("2.99.9.99")).toBe(true);
    });
  });
});

describe("edge cases", () => {
  describe("leading/trailing whitespace in version strings", () => {
    it("compareVersions trims whitespace before comparing", () => {
      expect(compareVersions("  1.2.3  ", "1.2.3")).toBe(0);
    });

    it("compareVersions handles leading whitespace", () => {
      expect(compareVersions(" 2.0.0", "2.0.0")).toBe(0);
    });

    it("compareVersions handles trailing whitespace", () => {
      expect(compareVersions("2.0.0 ", "2.0.0")).toBe(0);
    });

    it("isAgentOutdated trims whitespace on valid version", () => {
      expect(isAgentOutdated("  3.33.3  ")).toBe(false);
    });

    it("isAgentOutdated trims whitespace on outdated version", () => {
      expect(isAgentOutdated("  2.22.0  ")).toBe(true);
    });

    it("whitespace-only string is falsy, treated as outdated", () => {
      // "   " is truthy in JS but parseVersion trims to "" which splits to [""]
      // However isAgentOutdated checks !agentVersion first -- "   " is truthy
      // so it passes the null check, then compareVersions("   ", "3.0.0")
      // parseVersion trims "   " to "", splits to [""], parseInt("") = NaN -> 0
      // so it becomes [0] vs [2,23,0] -> -1 -> outdated
      expect(isAgentOutdated("   ")).toBe(true);
    });
  });

  describe("very large version numbers", () => {
    it("handles very large major version", () => {
      expect(compareVersions("999999.0.0", "999998.0.0")).toBe(1);
    });

    it("handles very large minor version", () => {
      expect(compareVersions("1.999999.0", "1.999998.0")).toBe(1);
    });

    it("handles very large patch version", () => {
      expect(compareVersions("1.0.999999", "1.0.999998")).toBe(1);
    });

    it("large version is not outdated", () => {
      expect(isAgentOutdated("999.999.999")).toBe(false);
    });

    it("equal large versions return 0", () => {
      expect(compareVersions("100000.200000.300000", "100000.200000.300000")).toBe(0);
    });
  });
});
