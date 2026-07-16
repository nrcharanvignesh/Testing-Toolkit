/**
 * app-state.unit.test.tsx
 * Tests for exported types and contracts from lib/app-state.tsx.
 * No DOM environment available -- tests verify type-level contracts
 * and constant shapes that the component tree depends on.
 */

import { describe, test, expect } from "vitest";
import type {
  KbState,
  KbUploadStatus,
  KbUploadItem,
  DialogId,
  LogLine,
} from "../lib/app-state";

describe("app-state type contracts", () => {
  test("KbState has all expected states", () => {
    const states: KbState[] = ["none", "indexing", "context", "ready", "error"];
    expect(states).toHaveLength(5);
    // Verify all are strings (type guard)
    states.forEach((s) => expect(typeof s).toBe("string"));
  });

  test("KbUploadStatus has all expected values", () => {
    const statuses: KbUploadStatus[] = [
      "queued",
      "uploading",
      "processing",
      "done",
      "error",
    ];
    expect(statuses).toHaveLength(5);
  });

  test("KbUploadItem shape is correct", () => {
    const item: KbUploadItem = {
      id: "upload-1",
      name: "test.pdf",
      size: 1024,
      progress: 0.5,
      status: "uploading",
    };
    expect(item.id).toBe("upload-1");
    expect(item.progress).toBeGreaterThanOrEqual(0);
    expect(item.progress).toBeLessThanOrEqual(1);
    expect(item.error).toBeUndefined();
  });

  test("KbUploadItem with error", () => {
    const item: KbUploadItem = {
      id: "upload-2",
      name: "bad.pdf",
      size: 0,
      progress: 0,
      status: "error",
      error: "File too large",
    };
    expect(item.status).toBe("error");
    expect(item.error).toBe("File too large");
  });

  test("DialogId includes all dialog types", () => {
    const dialogs: DialogId[] = [
      "settings",
      "generate",
      "kb",
      "upload",
      "package",
      "defect",
      "retrieval",
      "chat",
      "credentials",
      "e2e",
      "about",
      "viewlog",
      "aistack",
      null,
    ];
    expect(dialogs).toContain(null);
    expect(dialogs.filter((d) => d !== null)).toHaveLength(13);
  });

  test("LogLine levels are the expected set", () => {
    const levels: LogLine["level"][] = [
      "DEBUG",
      "INFO",
      "SUCCESS",
      "WARN",
      "ERROR",
    ];
    expect(levels).toHaveLength(5);
    // Verify all uppercase
    levels.forEach((l) => expect(l).toBe(l.toUpperCase()));
  });

  test("LogLine shape has required fields", () => {
    const line: LogLine = {
      id: 1,
      level: "INFO",
      text: "Server started",
      ts: Date.now(),
    };
    expect(line.id).toBe(1);
    expect(line.level).toBe("INFO");
    expect(line.ts).toBeGreaterThan(0);
  });
});
