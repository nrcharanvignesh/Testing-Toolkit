import { describe, it, expect, vi, afterEach } from "vitest";
import {
  sortWiIds,
  splitWiIds,
  agentLogLevel,
  mergeCommentsHtml,
  agent,
} from "../agent-client";
import type { WorkItemRow } from "../agent-client";
import { coveredWorkItemIds, userStoryIds } from "../board-utils";
import { humanSize } from "../../components/ui/download-links";
import { ErrorBoundary } from "../../components/ui/error-boundary";
import { toPayload } from "../../components/dialogs/ConnectionFields";
import { buildWindowsInstaller } from "../installer-template";
import { REQUIRED_AGENT_VERSION } from "../agent-version";

describe("centrally managed AI configuration", () => {
  it("never forwards AI secrets, endpoints, or model IDs from browser state", () => {
    const payload = toPayload({
      pat: "ado-pat",
      organization: "org",
      project_prefix: "",
      tls_mode: "system",
      jira_url: "",
      jira_user: "",
      jira_pat: "",
      jira_project_prefix: "",
      // Simulate stale or malicious browser state from an older client.
      api_key: "must-not-leave-browser",
      base_url: "https://wrong.example",
      model: "wrong-primary",
      fast_model: "wrong-fast",
      fallback_model: "wrong-fallback",
    } as Parameters<typeof toPayload>[0] & Record<string, string>);

    expect(payload).toMatchObject({ organization: "org", pat: "ado-pat" });
    expect(payload).not.toHaveProperty("api_key");
    expect(payload).not.toHaveProperty("base_url");
    expect(payload).not.toHaveProperty("model");
    expect(payload).not.toHaveProperty("fast_model");
    expect(payload).not.toHaveProperty("fallback_model");
    expect(payload).not.toHaveProperty("tls_mode");
  });
});

describe("Windows installer console contract", () => {
  const payload = buildWindowsInstaller("owner/repo", "parts", "token", true);

  it("prints no zero-percent bars for atomic milestones", () => {
    expect(payload).not.toContain("Show-StepBar 0");
    expect(payload).toContain('Show-StepBar 100 "Agent verified"');
  });

  it("redirects nested installer output unless verbose mode is enabled", () => {
    expect(payload).toContain("*> $pythonLog");
    expect(payload).toContain("if ($Verbose)");
  });

  it("requires and atomically stages the complete MCP payload", () => {
    expect(payload).toContain("overlay manifest is missing the MCP payload");
    expect(payload).toContain("MCP payload checksum mismatch");
    expect(payload).toContain("Copy-Item -LiteralPath (Join-Path $stage 'mcp_servers')");
  });

  it("fails closed when staged source and manifest versions differ", () => {
    expect(payload).toContain("overlay version mismatch: manifest=");
    expect(payload).toContain("latest agent code could not be staged safely");
    expect(payload).not.toContain("coherent bundled version retained");
  });

  it("generates control-character-free PowerShell paths", () => {
    const worker = payload.slice(payload.indexOf("#PSBEGIN") + 9);
    expect(worker).not.toMatch(/[\u0000-\u0008\u000b\u000c\u000e-\u001f]/);
    expect(worker).toContain(
      "Join-Path (Join-Path (Join-Path $stage 'src') 'agent') 'version.py'"
    );
    expect(worker).not.toContain("srcagent");
  });
});

describe("board coverage traceability", () => {
  const rows = [
    { wi_id: 1536939 },
    { wi_id: 1536942 },
  ] as WorkItemRow[];

  it("does not mark a work item covered when its test case has no steps", () => {
    const covered = coveredWorkItemIds(rows, [
      { wi_id: "1536939", step_count: 0 },
    ] as never[]);
    expect([...covered]).toEqual([]);
  });

  it("ignores stale test cases for work items absent from the current board", () => {
    const covered = coveredWorkItemIds(rows, [
      { wi_id: "9999999", step_count: 3 },
      { wi_id: "1536942", step_count: 2 },
    ] as never[]);
    expect([...covered]).toEqual(["1536942"]);
  });
});

describe("release version contract", () => {
  it("requires a non-legacy agent release", () => {
    expect(REQUIRED_AGENT_VERSION).not.toBe("1.0.0");
  });
});

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

describe("agent update policy is detection-only", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("checks status with GET and exposes no mutation methods", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({
        current: "2.10.6",
        latest: "2.10.7",
        update_available: true,
        configured: true,
        reachable: true,
        install_dir: "",
      }),
      text: async () => "",
      headers: new Headers({ "content-type": "application/json" }),
    } as unknown as Response);
    vi.stubGlobal("fetch", fetchMock);

    await expect(agent.updateStatus()).resolves.toMatchObject({
      update_available: true,
    });
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(fetchMock.mock.calls[0][0]).toBe(
      "http://127.0.0.1:7842/update/status"
    );
    expect(fetchMock.mock.calls[0][1]?.method).toBeUndefined();
    expect("applyUpdate" in agent).toBe(false);
    expect("configureUpdate" in agent).toBe(false);
    expect("updateProgress" in agent).toBe(false);
  });
});

describe("ErrorBoundary derives error state from a thrown error", () => {
  // Regression: a component fault (e.g. the TT-002 credentials crash) must be
  // captured into boundary state instead of white-screening the whole app.
  it("maps a thrown error into { error }", () => {
    const err = new Error("boom");
    expect(ErrorBoundary.getDerivedStateFromError(err)).toEqual({ error: err });
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
