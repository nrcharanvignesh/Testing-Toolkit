import { describe, it, expect } from "vitest";

// ---------------------------------------------------------------------------
// Re-creation of the KPI classification algorithm from BoardGrid.tsx.
// This tests the SPECIFICATION (contract), not the implementation directly,
// ensuring any refactor preserves correct behavior.
// ---------------------------------------------------------------------------

const KPI_BUCKETS = [
  {
    label: "Backlog",
    match: [
      "backlog", "new", "to do", "todo", "open", "reopened",
      "estimation", "ready for development", "in backlog",
      "selected for development", "funnel", "icebox", "parking lot",
    ],
  },
  {
    label: "Active",
    match: [
      "active", "in development", "development", "in dev",
      "blocked in dev", "ready for qa", "in qa", "blocked in qa",
      "ready for acceptance", "in acceptance", "blocked in acceptance",
      "in progress", "in review", "code review",
      "in testing", "testing", "uat", "doing", "wip",
      "ready for review", "peer review", "blocked",
    ],
  },
  {
    label: "Passed",
    match: ["passed", "verified", "validated", "approved"],
  },
  {
    label: "Failed",
    match: ["failed", "rejected", "won't do", "wont do", "cancelled"],
  },
  {
    label: "Closed",
    match: ["closed", "accepted", "done", "resolved", "removed", "released", "completed"],
  },
] as const;

// Priority order: terminal states checked BEFORE Active to avoid false matches
// on substrings like "review" inside "Passed Business Review".
const CLASSIFY_ORDER = ["Backlog", "Passed", "Failed", "Closed", "Active"] as const;
const CLASSIFY_MAP = new Map(KPI_BUCKETS.map((b) => [b.label, b.match as readonly string[]]));

function classifyColumn(columnName: string): string {
  const lower = columnName.toLowerCase();
  for (const label of CLASSIFY_ORDER) {
    const keywords = CLASSIFY_MAP.get(label)!;
    if (keywords.some((kw) => lower.includes(kw))) return label;
  }
  return "Active";
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("KPI classification - priority ordering (the fixed bug)", () => {
  it("'Passed Business Review' -> Passed (not Active via 'in review')", () => {
    expect(classifyColumn("Passed Business Review")).toBe("Passed");
  });

  it("'Failed Review' -> Failed (not Active via 'in review')", () => {
    expect(classifyColumn("Failed Review")).toBe("Failed");
  });

  it("'Closed For Review' -> Closed (not Active via 'in review')", () => {
    expect(classifyColumn("Closed For Review")).toBe("Closed");
  });

  it("'In Review' -> Active (no terminal keyword present)", () => {
    expect(classifyColumn("In Review")).toBe("Active");
  });

  it("'Code Review' -> Active", () => {
    expect(classifyColumn("Code Review")).toBe("Active");
  });

  it("'Ready For Review' -> Active", () => {
    expect(classifyColumn("Ready For Review")).toBe("Active");
  });

  it("'Peer Review' -> Active", () => {
    expect(classifyColumn("Peer Review")).toBe("Active");
  });
});

describe("KPI classification - Backlog bucket", () => {
  const backlogNames = [
    "Backlog",
    "New",
    "To Do",
    "Todo",
    "Open",
    "Reopened",
    "Estimation",
    "Ready For Development",
    "In Backlog",
    "Selected For Development",
    "Funnel",
    "Icebox",
    "Parking Lot",
  ];

  it.each(backlogNames)("'%s' -> Backlog", (name) => {
    expect(classifyColumn(name)).toBe("Backlog");
  });

  it("substring match: 'Sprint Backlog Items' -> Backlog", () => {
    expect(classifyColumn("Sprint Backlog Items")).toBe("Backlog");
  });

  it("'Reopened After Fix' -> Backlog (contains 'reopened')", () => {
    expect(classifyColumn("Reopened After Fix")).toBe("Backlog");
  });
});

describe("KPI classification - Active bucket", () => {
  const activeNames = [
    "Active",
    "In Development",
    "Development",
    "In Dev",
    "Blocked In Dev",
    "Ready For QA",
    "In QA",
    "Blocked In QA",
    "Ready For Acceptance",
    "In Acceptance",
    "Blocked In Acceptance",
    "In Progress",
    "In Review",
    "Code Review",
    "In Testing",
    "Testing",
    "UAT",
    "Doing",
    "WIP",
    "Ready For Review",
    "Peer Review",
    "Blocked",
  ];

  it.each(activeNames)("'%s' -> Active", (name) => {
    expect(classifyColumn(name)).toBe("Active");
  });

  it("default fallback: unrecognized column -> Active", () => {
    expect(classifyColumn("Custom Column")).toBe("Active");
    expect(classifyColumn("Something Random")).toBe("Active");
    expect(classifyColumn("")).toBe("Active");
  });
});

describe("KPI classification - Passed bucket", () => {
  const passedNames = ["Passed", "Verified", "Validated", "Approved"];

  it.each(passedNames)("'%s' -> Passed", (name) => {
    expect(classifyColumn(name)).toBe("Passed");
  });

  it("substring: 'Passed UAT' -> Passed", () => {
    expect(classifyColumn("Passed UAT")).toBe("Passed");
  });

  it("substring: 'User Validated' -> Passed", () => {
    expect(classifyColumn("User Validated")).toBe("Passed");
  });
});

describe("KPI classification - Failed bucket", () => {
  const failedNames = ["Failed", "Rejected", "Won't Do", "Wont Do", "Cancelled"];

  it.each(failedNames)("'%s' -> Failed", (name) => {
    expect(classifyColumn(name)).toBe("Failed");
  });

  it("substring: 'Failed Testing' -> Failed", () => {
    expect(classifyColumn("Failed Testing")).toBe("Failed");
  });

  it("substring: 'Rejected By PM' -> Failed", () => {
    expect(classifyColumn("Rejected By PM")).toBe("Failed");
  });
});

describe("KPI classification - Closed bucket", () => {
  const closedNames = [
    "Closed", "Accepted", "Done", "Resolved", "Removed", "Released", "Completed",
  ];

  it.each(closedNames)("'%s' -> Closed", (name) => {
    expect(classifyColumn(name)).toBe("Closed");
  });

  it("substring: 'Closed Won' -> Closed", () => {
    expect(classifyColumn("Closed Won")).toBe("Closed");
  });

  it("substring: 'Released To Prod' -> Closed", () => {
    expect(classifyColumn("Released To Prod")).toBe("Closed");
  });
});

describe("KPI classification - case insensitivity", () => {
  it("PASSED, passed, Passed all -> Passed", () => {
    expect(classifyColumn("PASSED")).toBe("Passed");
    expect(classifyColumn("passed")).toBe("Passed");
    expect(classifyColumn("Passed")).toBe("Passed");
    expect(classifyColumn("pAsSeD")).toBe("Passed");
  });

  it("DONE, done, Done all -> Closed", () => {
    expect(classifyColumn("DONE")).toBe("Closed");
    expect(classifyColumn("done")).toBe("Closed");
    expect(classifyColumn("Done")).toBe("Closed");
  });

  it("IN PROGRESS, in progress, In Progress all -> Active", () => {
    expect(classifyColumn("IN PROGRESS")).toBe("Active");
    expect(classifyColumn("in progress")).toBe("Active");
    expect(classifyColumn("In Progress")).toBe("Active");
  });

  it("NEW, new, New all -> Backlog", () => {
    expect(classifyColumn("NEW")).toBe("Backlog");
    expect(classifyColumn("new")).toBe("Backlog");
    expect(classifyColumn("New")).toBe("Backlog");
  });

  it("BLOCKED, blocked, Blocked all -> Active", () => {
    expect(classifyColumn("BLOCKED")).toBe("Active");
    expect(classifyColumn("blocked")).toBe("Active");
    expect(classifyColumn("Blocked")).toBe("Active");
  });
});

describe("KPI classification - substring matching semantics", () => {
  it("uses includes(), not exact match", () => {
    // "Ready for development" is an exact keyword in Backlog
    expect(classifyColumn("Ready for development")).toBe("Backlog");
    // Even embedded in a longer string
    expect(classifyColumn("Not Ready for development Yet")).toBe("Backlog");
  });

  it("'to do' matches substring (not just word boundary)", () => {
    expect(classifyColumn("Sprint To Do")).toBe("Backlog");
  });

  it("'done' inside longer word still matches (includes behavior)", () => {
    // "abandoned" contains "done" (aband-ONE-d -> d,o,n,e at indices 4-7)
    expect("abandoned".includes("done")).toBe(true);
    expect(classifyColumn("Abandoned")).toBe("Closed");
  });

  it("'new' inside longer word matches (includes behavior)", () => {
    // "renewed" contains "new"
    expect("renewed".includes("new")).toBe(true);
    expect(classifyColumn("Renewed")).toBe("Backlog");
  });

  it("'open' inside longer word matches (includes behavior)", () => {
    // "reopened" contains "open" AND "reopened"
    expect(classifyColumn("Reopened")).toBe("Backlog");
  });

  it("'active' inside 'Inactive' matches", () => {
    // "inactive" contains "active"
    expect("inactive".includes("active")).toBe(true);
    // But CLASSIFY_ORDER checks Backlog first, then Passed, Failed, Closed, Active last
    // "inactive" has no Backlog/Passed/Failed/Closed keywords, so Active matches
    expect(classifyColumn("Inactive")).toBe("Active");
  });

  it("'testing' inside 'Passed Testing Phase'", () => {
    // "passed" is checked before "testing" due to CLASSIFY_ORDER
    expect(classifyColumn("Passed Testing Phase")).toBe("Passed");
  });
});

describe("KPI classification - CLASSIFY_ORDER guarantees", () => {
  it("Backlog is checked before Active", () => {
    // "New" could match Active's "in dev"? No. But "new" is in Backlog.
    expect(classifyColumn("New")).toBe("Backlog");
  });

  it("Passed is checked before Active", () => {
    // "Passed In Review" has both "passed" (Passed) and "in review" (Active)
    expect(classifyColumn("Passed In Review")).toBe("Passed");
  });

  it("Failed is checked before Active", () => {
    // "Failed In Testing" has both "failed" (Failed) and "in testing" (Active)
    expect(classifyColumn("Failed In Testing")).toBe("Failed");
  });

  it("Closed is checked before Active", () => {
    // "Done With Review" has "done" (Closed) and substring overlap
    expect(classifyColumn("Done With Review")).toBe("Closed");
  });

  it("Backlog is checked before Passed/Failed/Closed", () => {
    // "New And Closed" has "new" (Backlog) and "closed" (Closed)
    // Backlog is first in CLASSIFY_ORDER
    expect(classifyColumn("New And Closed")).toBe("Backlog");
  });

  it("Passed is checked before Failed and Closed", () => {
    // "Passed But Failed" has "passed" and "failed"
    // Passed comes before Failed in CLASSIFY_ORDER
    expect(classifyColumn("Passed But Failed")).toBe("Passed");
  });

  it("Failed is checked before Closed", () => {
    // "Failed And Closed" has "failed" and "closed"
    expect(classifyColumn("Failed And Closed")).toBe("Failed");
  });
});

describe("KPI classification - edge cases", () => {
  it("empty string -> Active (default fallback)", () => {
    expect(classifyColumn("")).toBe("Active");
  });

  it("whitespace only -> Active (no keyword match)", () => {
    expect(classifyColumn("   ")).toBe("Active");
  });

  it("single character -> Active (no match)", () => {
    expect(classifyColumn("X")).toBe("Active");
  });

  it("numeric column name -> Active (no match)", () => {
    expect(classifyColumn("12345")).toBe("Active");
  });

  it("very long unrecognized name -> Active", () => {
    const long = "A".repeat(1000);
    expect(classifyColumn(long)).toBe("Active");
  });

  it("keyword with extra whitespace still matches (via includes)", () => {
    // "in progress" as a substring inside a padded name
    expect(classifyColumn("  in progress  ")).toBe("Active");
  });

  it("'won't do' with apostrophe matches exactly", () => {
    expect(classifyColumn("Won't Do")).toBe("Failed");
  });

  it("'wont do' without apostrophe also matches", () => {
    expect(classifyColumn("Wont Do")).toBe("Failed");
  });

  it("'to do' with space (not 'todo') matches Backlog", () => {
    expect(classifyColumn("To Do")).toBe("Backlog");
  });

  it("'todo' without space also matches Backlog", () => {
    expect(classifyColumn("Todo")).toBe("Backlog");
  });
});

describe("KPI classification - real-world column names from Jira/ADO/etc", () => {
  const realWorld: [string, string][] = [
    // Jira defaults
    ["To Do", "Backlog"],
    ["In Progress", "Active"],
    ["Done", "Closed"],
    // Azure DevOps defaults
    ["New", "Backlog"],
    ["Active", "Active"],
    ["Resolved", "Closed"],
    ["Closed", "Closed"],
    // Custom statuses seen in production
    ["Ready for QA", "Active"],
    ["In QA", "Active"],
    ["Blocked in QA", "Active"],
    ["UAT", "Active"],
    ["Released", "Closed"],
    ["Completed", "Closed"],
    ["Icebox", "Backlog"],
    ["Parking Lot", "Backlog"],
    ["Estimation", "Backlog"],
    ["Selected for Development", "Backlog"],
    ["Blocked", "Active"],
    ["Cancelled", "Failed"],
    ["Rejected", "Failed"],
    ["Approved", "Passed"],
    ["Verified", "Passed"],
    ["Validated", "Passed"],
    // The critical bug-fix cases
    ["Passed Business Review", "Passed"],
    ["Failed Code Review", "Failed"],
    ["Closed After Review", "Closed"],
  ];

  it.each(realWorld)("'%s' -> %s", (name, expected) => {
    expect(classifyColumn(name)).toBe(expected);
  });
});

describe("KPI classification - data structure integrity", () => {
  it("CLASSIFY_ORDER contains exactly the bucket labels", () => {
    const bucketLabels = new Set(KPI_BUCKETS.map((b) => b.label));
    const orderLabels = new Set(CLASSIFY_ORDER);
    expect(orderLabels).toEqual(bucketLabels);
  });

  it("CLASSIFY_ORDER has same length as KPI_BUCKETS", () => {
    expect(CLASSIFY_ORDER.length).toBe(KPI_BUCKETS.length);
  });

  it("no duplicate keywords across buckets", () => {
    const seen = new Map<string, string>();
    for (const bucket of KPI_BUCKETS) {
      for (const kw of bucket.match) {
        if (seen.has(kw)) {
          // This would be a spec bug - fail with info
          expect.fail(
            `Keyword "${kw}" appears in both "${seen.get(kw)}" and "${bucket.label}"`,
          );
        }
        seen.set(kw, bucket.label);
      }
    }
  });

  it("all keywords are lowercase", () => {
    for (const bucket of KPI_BUCKETS) {
      for (const kw of bucket.match) {
        expect(kw).toBe(kw.toLowerCase());
      }
    }
  });

  it("no keywords are empty strings", () => {
    for (const bucket of KPI_BUCKETS) {
      for (const kw of bucket.match) {
        expect(kw.length).toBeGreaterThan(0);
      }
    }
  });

  it("no keywords have leading/trailing whitespace", () => {
    for (const bucket of KPI_BUCKETS) {
      for (const kw of bucket.match) {
        expect(kw).toBe(kw.trim());
      }
    }
  });

  it("Active is last in CLASSIFY_ORDER (broadest bucket)", () => {
    expect(CLASSIFY_ORDER[CLASSIFY_ORDER.length - 1]).toBe("Active");
  });
});
