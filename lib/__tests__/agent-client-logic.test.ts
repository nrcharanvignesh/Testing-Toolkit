import { describe, it, expect, vi, afterEach } from "vitest";
import {
  sortWiIds,
  splitWiIds,
  agentLogLevel,
  mergeCommentsHtml,
  sanitizeWorkItemHtml,
  displayProjectName,
  TC_TYPES,
  TC_DISPLAY_NAME,
  TC_BUTTON_LABEL,
  agent,
} from "../agent-client";
import type { WorkItemRow } from "../agent-client";
  import { coveredWorkItemIds, testCaseCountsByWorkItem, userStoryIds } from "../board-utils";
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
    expect(payload).toContain("Show-StepBar 100 'Agent installed and verified'");
  });

  it("shows a single progress bar during the long install/verify step instead of a silent blocking call", () => {
    // The step that looked "frozen" (offline pip install + agent self-test) must
    // run asynchronously and show ONE progress bar in the same style as every
    // other step - not a flickering line of tailed nested installer output.
    expect(payload).toContain('Write-Step "Installing and verifying the agent"');
    expect(payload).toContain("Start-Process -FilePath 'cmd.exe'");
    expect(payload).toContain("-RedirectStandardOutput $pythonLog");
    expect(payload).toContain("while (-not $proc.HasExited)");
    // Uses the shared Show-StepBar renderer with a stable label + ETA fill, and
    // completes at 100% on success - identical look to the other milestones.
    expect(payload).toContain("Show-StepBar $pct 'Installing and verifying the agent'");
    expect(payload).toContain("Show-StepBar 100 'Agent installed and verified'");
    // Verbose mode still streams raw output; a defensive fallback still exists.
    expect(payload).toContain("if ($Verbose)");
    expect(payload).toContain("*> $pythonLog");
  });

  it("shows ONE consolidated progress bar for the overlay milestone (no stacked bars)", () => {
    // The whole "Applying latest agent code" milestone must advance a single
    // bar across source + wheels + MCP payload, capped at 99% until staged.
    expect(payload).toContain("$ovTotal = [Math]::Max(1, $srcFiles.Count + $wheels.Count + $mcpFiles.Count)");
    expect(payload).toContain("Show-StepBar $p 'Applying latest agent code'");
    // The old per-group bar labels must be gone (they produced 3 stacked bars).
    expect(payload).not.toContain("Fetching agent code ");
    expect(payload).not.toContain("Fetching dependencies ");
    expect(payload).not.toContain("Fetching automation payload ");
  });

  it("prints the logs folder location and reliably reads the installer exit code", () => {
    expect(payload).toContain('("  Logs folder: " + $LogDir)');
    // Touch .Handle + WaitForExit so ExitCode is populated (no blank "code .").
    expect(payload).toContain("$null = $proc.Handle");
    expect(payload).toContain("if ($null -eq $code) { $code = 0 }");
  });

  it("generates a structurally balanced PowerShell worker (braces and parentheses)", () => {
    // No pwsh in CI: validate bracket balance of the worker body as a proxy for
    // a syntax check. A mismatched brace/paren from a bad escape fails here.
    const worker = payload.slice(payload.indexOf("#PSBEGIN") + "#PSBEGIN".length);
    const pairs: Record<string, string> = { ")": "(", "}": "{", "]": "[" };
    const opens = new Set(["(", "{", "["]);
    let inSingle = false;
    let inDouble = false;
    const stack: string[] = [];
    for (let i = 0; i < worker.length; i++) {
      const ch = worker[i];
      if (inSingle) {
        if (ch === "'") inSingle = false;
        continue;
      }
      if (inDouble) {
        if (ch === '"') inDouble = false;
        continue;
      }
      if (ch === "'") { inSingle = true; continue; }
      if (ch === '"') { inDouble = true; continue; }
      if (ch === "#") { // skip to end of line (line comment)
        while (i < worker.length && worker[i] !== "\n") i++;
        continue;
      }
      if (opens.has(ch)) stack.push(ch);
      else if (pairs[ch]) {
        expect(stack.pop()).toBe(pairs[ch]);
      }
    }
    expect(stack.length).toBe(0);
    expect(inSingle).toBe(false);
    expect(inDouble).toBe(false);
  });

  it("requires and atomically stages the complete MCP payload", () => {
    expect(payload).toContain("overlay manifest has no MCP payload for");
    expect(payload).toContain("MCP payload ");
    expect(payload).toContain("Copy-Item -LiteralPath (Join-Path $stage 'mcp_servers')");
  });

  it("streams every overlay artifact as bytes and verifies it before promotion", () => {
    expect(payload).toContain("System.Net.Http.HttpClient");
    expect(payload).toContain("ResponseHeadersRead");
    expect(payload).toContain("overlay manifest is missing a checksum for");
    expect(payload).toContain("Get-FileHash -Algorithm SHA256");
    expect(payload).not.toContain("Invoke-RestMethod -Uri $uri -Headers $headers -UseBasicParsing -OutFile $outFile");
  });

  it("explicitly promotes the hidden AI credential envelope", () => {
    expect(payload).toContain("Get-ChildItem -LiteralPath (Join-Path $stage 'src') -Force");
    expect(payload).toContain("Copy-Item -LiteralPath $stageCredential -Destination $promotedCredential -Force");
    expect(payload).toContain("authenticated AI credential envelope was not promoted");
  });

  it("selects MCP binary payloads for the native Windows architecture", () => {
    expect(payload).toContain("RuntimeInformation]::OSArchitecture");
    expect(payload).toContain("$platformKey = 'win32-' + $archKey");
    expect(payload).toContain("Where-Object { -not $_.platforms -or @($_.platforms) -contains $platformKey }");
  });

  it("does not emit case-insensitive duplicate PowerShell hashtable keys", () => {
    const aliases = payload.match(/\$platformAliases = @\{([\s\S]*?)\n    \}/)?.[1] ?? "";
    const keys = [...aliases.matchAll(/'([^']+)'\s*=/g)].map((match) =>
      match[1].toLowerCase()
    );
    expect(keys.length).toBeGreaterThan(0);
    expect(new Set(keys).size).toBe(keys.length);
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

describe("testCaseCountsByWorkItem (Generated Tests column)", () => {
  it("counts only sidecar-generated test cases, ignores linked", () => {
    const rows = [
      { wi_id: 1536952, linked_test_case_count: 6 },
      { wi_id: 1536939, linked_test_case_count: 0 },
      { wi_id: 1536942 },
    ] as WorkItemRow[];
    const counts = testCaseCountsByWorkItem(rows, [
      // Two tool-generated with steps for 1536952 -> 2 (linked ignored).
      { wi_id: "1536952", step_count: 3 },
      { wi_id: "1536952", step_count: 5 },
      // Stepless generated is ignored.
      { wi_id: "1536939", step_count: 0 },
      // Generated-only for 1536942 -> 1.
      { wi_id: "1536942", step_count: 4 },
    ] as never[]);
    expect(counts.get("1536952")).toBe(2);
    expect(counts.get("1536942")).toBe(1);
    expect(counts.has("1536939")).toBe(false);
  });

  it("ignores tracker-linked test cases (sidecar-only column)", () => {
    const rows = [{ wi_id: "PROJ-1", linked_test_case_count: 3 }] as WorkItemRow[];
    const counts = testCaseCountsByWorkItem(rows, [] as never[]);
    expect(counts.has("PROJ-1")).toBe(false);
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

describe("sanitizeWorkItemHtml", () => {
  it("removes executable markup and event handlers from source-system HTML", () => {
    const out = sanitizeWorkItemHtml(
      '<p onclick="alert(1)">safe<script>alert(2)</script></p>' +
        '<img src="javascript:alert(3)" onerror="alert(4)">' +
        '<a href="javascript:alert(5)">bad link</a>'
    );

    expect(out).toContain("<p>safe</p>");
    expect(out).not.toMatch(/script|onclick|onerror|javascript:/i);
  });

  it("retains supported formatting and hardens safe links", () => {
    const out = sanitizeWorkItemHtml(
      '<table><tr><td colspan="2"><strong>ok</strong></td></tr></table>' +
        '<a href="https://example.com">docs</a>'
    );

    expect(out).toContain('<td colspan="2"><strong>ok</strong></td>');
    expect(out).toContain('href="https://example.com"');
    expect(out).toContain('rel="noopener noreferrer"');
    expect(out).toContain('target="_blank"');
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

describe("displayProjectName", () => {
  it("strips a matching prefix (case-insensitive) and leading separators", () => {
    expect(displayProjectName("ACME_ProjectAlpha", "ACME_")).toBe("ProjectAlpha");
    expect(displayProjectName("acme-Beta", "ACME-")).toBe("Beta");
    expect(displayProjectName("PREFIX - Gamma", "PREFIX")).toBe("Gamma");
  });

  it("returns the full name when prefix does not match", () => {
    expect(displayProjectName("ProjectAlpha", "OTHER")).toBe("ProjectAlpha");
    expect(displayProjectName("Short", "LongerPrefix")).toBe("Short");
  });

  it("returns the full name when stripping would leave empty", () => {
    expect(displayProjectName("PREFIX", "PREFIX")).toBe("PREFIX");
  });

  it("returns the full name when prefix is empty", () => {
    expect(displayProjectName("MyProject", "")).toBe("MyProject");
  });
});

describe("TC_TYPES and display constants", () => {
  it("defines exactly three test-case types", () => {
    expect(TC_TYPES).toHaveLength(3);
    expect(TC_TYPES).toContain("implementation");
    expect(TC_TYPES).toContain("sit");
    expect(TC_TYPES).toContain("uat");
  });

  it("has a display name for every type", () => {
    for (const t of TC_TYPES) {
      expect(TC_DISPLAY_NAME[t]).toBeTruthy();
      expect(typeof TC_DISPLAY_NAME[t]).toBe("string");
    }
  });

  it("has a button label for every type", () => {
    for (const t of TC_TYPES) {
      expect(TC_BUTTON_LABEL[t]).toBeTruthy();
      expect(typeof TC_BUTTON_LABEL[t]).toBe("string");
    }
  });
});
