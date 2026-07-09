import { describe, it, expect, vi, afterEach } from "vitest";
import {
  sortWiIds,
  splitWiIds,
  agentLogLevel,
  mergeCommentsHtml,
  agent,
} from "../agent-client";
import type { WorkItemRow } from "../agent-client";
import { userStoryIds } from "../board-utils";
import { humanSize } from "../../components/ui/download-links";

describe("userStoryIds (SIT/UAT auto-select parity)", () => {
  const row = (wi_id: number | string, wi_type: string): WorkItemRow =>
    ({ wi_id, wi_type, title: "", state: "" } as WorkItemRow);

  it("returns only User Story / Story rows, sorted", () => {
    const rows = [
      row(3, "Bug"),
      row(2, "User Story"),
      row(1, "Story"),
      row(4, "Task"),
    ];
    expect(userStoryIds(rows)).toEqual([1, 2]);
  });
  it("is case-insensitive on the type label", () => {
    expect(userStoryIds([row(5, "user story"), row(6, "STORY")])).toEqual([
      5, 6,
    ]);
  });
  it("returns empty when the board has no stories", () => {
    expect(userStoryIds([row(1, "Bug"), row(2, "Task")])).toEqual([]);
  });
});

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

describe("mergeCommentsHtml (detail-pane comment fallthrough)", () => {
  const html = [{ when: "t1", author: "A", html: "<p>hi</p>" }];
  const text = [{ when: "t2", author: "B", text: "plain\nline" }];

  it("prefers rendered HTML comments when present", () => {
    expect(mergeCommentsHtml(html, text)).toEqual(html);
  });
  it("falls through to plain-text comments when comments_html is EMPTY", () => {
    // Regression: `[] ?? text` would wrongly yield [] and drop comments.
    const out = mergeCommentsHtml([], text);
    expect(out).toHaveLength(1);
    expect(out[0].author).toBe("B");
    expect(out[0].html).toBe("plain<br/>line");
  });
  it("falls through when comments_html is undefined/null", () => {
    expect(mergeCommentsHtml(undefined, text)).toHaveLength(1);
    expect(mergeCommentsHtml(null, text)[0].author).toBe("B");
  });
  it("returns empty when both are empty/missing", () => {
    expect(mergeCommentsHtml([], [])).toEqual([]);
    expect(mergeCommentsHtml(undefined, undefined)).toEqual([]);
  });
});

describe("list endpoints never crash consumers on malformed payloads", () => {
  // Regression: CredentialsDialog crashed with `creds.length` when the agent
  // returned a body missing the expected array field (older agent / partial
  // response). The client must always resolve to an array.
  afterEach(() => vi.unstubAllGlobals());

  const stubFetch = (body: unknown) => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: true,
        status: 200,
        json: async () => body,
        text: async () => JSON.stringify(body),
        headers: new Headers({ "content-type": "application/json" }),
      } as unknown as Response)
    );
  };

  it("listCredentials returns [] when 'credentials' is missing", async () => {
    stubFetch({});
    await expect(agent.listCredentials("Proj")).resolves.toEqual([]);
  });
  it("e2eTestCases returns [] when 'test_cases' is missing", async () => {
    stubFetch({ ok: true });
    await expect(agent.e2eTestCases("Proj")).resolves.toEqual([]);
  });
  it("e2eEnvironments returns [] when 'environments' is missing", async () => {
    stubFetch({});
    await expect(agent.e2eEnvironments("Proj")).resolves.toEqual([]);
  });
  it("still returns the array when present", async () => {
    stubFetch({ credentials: [{ environment: "SIT" }] });
    const out = await agent.listCredentials("Proj");
    expect(out).toHaveLength(1);
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
