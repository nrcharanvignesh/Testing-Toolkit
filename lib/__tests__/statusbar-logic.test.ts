import { describe, it, expect } from "vitest";
import { fmtMem, KB_COLOR } from "../../components/layout/StatusBar";

describe("StatusBar > fmtMem", () => {
  it("returns '--' for null", () => {
    expect(fmtMem(null)).toBe("--");
  });

  it("returns '--' for undefined", () => {
    expect(fmtMem(undefined)).toBe("--");
  });

  it("returns '--' for NaN", () => {
    expect(fmtMem(NaN)).toBe("--");
  });

  it("returns '--' for Infinity", () => {
    expect(fmtMem(Infinity)).toBe("--");
  });

  it("returns '--' for -Infinity", () => {
    expect(fmtMem(-Infinity)).toBe("--");
  });

  it("formats values below 1024 as MB", () => {
    expect(fmtMem(512)).toBe("512 MB");
    expect(fmtMem(0)).toBe("0 MB");
    expect(fmtMem(1023)).toBe("1023 MB");
  });

  it("formats values at or above 1024 as GB with one decimal", () => {
    expect(fmtMem(1024)).toBe("1.0 GB");
    expect(fmtMem(2048)).toBe("2.0 GB");
    expect(fmtMem(1536)).toBe("1.5 GB");
    expect(fmtMem(3072)).toBe("3.0 GB");
  });

  it("handles fractional GB correctly", () => {
    // 1280 MB = 1.25 GB -> "1.3 GB" (toFixed(1) rounds .25 up)
    expect(fmtMem(1280)).toBe("1.3 GB");
    // 1126 MB = 1.099... GB -> "1.1 GB"
    expect(fmtMem(1126)).toBe("1.1 GB");
  });
});

describe("StatusBar > KB_COLOR", () => {
  it("maps every KbState to a CSS variable", () => {
    expect(KB_COLOR.none).toBe("var(--tt-danger)");
    expect(KB_COLOR.indexing).toBe("var(--tt-warn)");
    expect(KB_COLOR.context).toBe("var(--tt-info)");
    expect(KB_COLOR.ready).toBe("var(--tt-success)");
    expect(KB_COLOR.error).toBe("var(--tt-danger)");
  });

  it("covers exactly the five known states", () => {
    const keys = Object.keys(KB_COLOR).sort();
    expect(keys).toEqual(["context", "error", "indexing", "none", "ready"]);
  });
});
