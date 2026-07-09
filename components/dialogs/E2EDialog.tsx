"use client";

import { useEffect, useMemo, useRef, useState, type ReactElement } from "react";
import {
  Play,
  Square,
  RotateCcw,
  CheckCircle2,
  XCircle,
  MinusCircle,
  Loader2,
} from "lucide-react";
import { Modal } from "@/components/ui/modal";
import { DownloadLinks, type DownloadItem } from "@/components/ui/download-links";
import {
  agent,
  agentLogLevel,
  type E2ETestCase,
  type E2EEnvironment,
  type E2ERunResult,
  type E2ELastRun,
  type JobProgress,
} from "@/lib/agent-client";
import { useAppState } from "@/lib/app-state";

type RowStatus = "pending" | "running" | "pass" | "fail" | "skip" | "error";

const STATUS_ICON: Record<RowStatus, ReactElement> = {
  pending: <MinusCircle className="h-4 w-4 text-[var(--tt-text-muted)]" />,
  running: (
    <Loader2 className="h-4 w-4 animate-spin text-[var(--tt-warn)]" />
  ),
  pass: <CheckCircle2 className="h-4 w-4 text-[var(--tt-success)]" />,
  fail: <XCircle className="h-4 w-4 text-[var(--tt-danger)]" />,
  error: <XCircle className="h-4 w-4 text-[var(--tt-danger)]" />,
  skip: <MinusCircle className="h-4 w-4 text-[var(--tt-text-muted)]" />,
};

/**
 * E2E Automation - web port of the desktop E2EDialog.
 * Pick an environment (from the vault) + generated test cases, then run them
 * through Playwright on the agent (CDP attach to the real browser for SSO).
 * Progress + logs stream live; the password never leaves the agent host.
 */
export function E2EDialog({ onClose }: { onClose: () => void }) {
  const {
    currentProject,
    displayName,
    pushLog,
    selected: boardSelection,
  } = useAppState();

  // STRICT scoping (user directive): the E2E dialog shows ONLY test cases whose
  // parent work item is ticked on the board. There is NO fallback to "all test
  // cases" -- if the user ticks nothing, or ticks work items that have no
  // generated test cases, the list is empty by design. This is what makes a
  // work item appear here "only if it actually has test cases". wi_id is
  // compared as a string (WiId is string|number; E2ETestCase.wi_id is string).
  const wiScope = useMemo(
    () => new Set([...boardSelection].map((id) => String(id))),
    [boardSelection]
  );

  const [envs, setEnvs] = useState<E2EEnvironment[]>([]);
  const [selectedEnv, setSelectedEnv] = useState("");
  const [testCases, setTestCases] = useState<E2ETestCase[]>([]);
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [rowStatus, setRowStatus] = useState<Record<string, RowStatus>>({});
  const [lastRun, setLastRun] = useState<E2ELastRun | null>(null);
  const [loading, setLoading] = useState(true);
  const [running, setRunning] = useState(false);
  const [logs, setLogs] = useState<string[]>([]);
  const [progress, setProgress] = useState<JobProgress | null>(null);
  const [result, setResult] = useState<E2ERunResult | null>(null);
  const [error, setError] = useState("");
  const jobIdRef = useRef<string>("");
  const logEndRef = useRef<HTMLDivElement>(null);

  // Load environments, test cases, and last-run summary on open.
  useEffect(() => {
    let cancelled = false;
    if (!currentProject) {
      setLoading(false);
      return;
    }
    (async () => {
      try {
        const [e, tc, lr] = await Promise.all([
          agent.e2eEnvironments(currentProject),
          agent.e2eTestCases(currentProject),
          agent.e2eLastRun(currentProject),
        ]);
        if (cancelled) return;
        setEnvs(e);
        setTestCases(tc);
        setLastRun(lr);
        // Default to the first runnable environment.
        const firstRunnable = e.find((x) => x.has_password) || e[0];
        if (firstRunnable) setSelectedEnv(firstRunnable.env);
        // Pre-select ONLY the test cases whose parent WI is in the board
        // selection. No board selection -> nothing pre-selected (no fallback).
        const scoped = tc.filter((t) => wiScope.has(String(t.wi_id)));
        setSelected(new Set(scoped.map((t) => t.index)));
      } catch (err) {
        if (!cancelled)
          setError(err instanceof Error ? err.message : "Failed to load E2E data");
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [currentProject, wiScope]);

  useEffect(() => {
    logEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [logs]);

  const envRunnable = useMemo(
    () => envs.find((e) => e.env === selectedEnv)?.has_password ?? false,
    [envs, selectedEnv]
  );

  // Test cases in scope for this run: STRICTLY the ones whose parent WI is
  // ticked on the board. Empty when nothing is ticked (no fallback to all).
  const visibleTestCases = useMemo(
    () => testCases.filter((t) => wiScope.has(String(t.wi_id))),
    [testCases, wiScope]
  );

  const canRun =
    !running && !!currentProject && envRunnable && selected.size > 0;

  const toggle = (index: number) =>
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(index)) next.delete(index);
      else next.add(index);
      return next;
    });

  const selectAll = () =>
    setSelected(new Set(visibleTestCases.map((t) => t.index)));
  const selectNone = () => setSelected(new Set());

  const run = async (indices: number[]) => {
    if (!currentProject || !selectedEnv) return;
    setRunning(true);
    setError("");
    setLogs([]);
    setProgress(null);
    setResult(null);
    jobIdRef.current = ""; // reset before new run
    // Reset row status: chosen -> running, rest -> pending.
    const chosen = new Set(indices);
    setRowStatus(() => {
      const next: Record<string, RowStatus> = {};
      for (const tc of testCases) {
        const key = String(tc.index);
        next[key] = chosen.has(tc.index) ? "running" : "pending";
      }
      return next;
    });
    pushLog("INFO", `Starting E2E run against "${selectedEnv}"...`);
    try {
      const res = await agent.runE2E(
        { project: currentProject, env: selectedEnv, indices },
        {
          onJobId: (id) => { jobIdRef.current = id; },
          onLog: (line) => {
            pushLog(agentLogLevel(line), line);
            setLogs((prev) => [...prev, line]);
          },
          onProgress: (p) => setProgress(p),
        }
      );
      setResult(res);
      // Map returned per-TC status onto rows (results are in index order of
      // the selected subset).
      setRowStatus((prev) => {
        const next = { ...prev };
        indices.forEach((idx, i) => {
          const r = res.results[i];
          if (r) next[String(idx)] = (r.status as RowStatus) || "skip";
        });
        return next;
      });
      // Refresh last-run summary.
      const lr = await agent.e2eLastRun(currentProject);
      setLastRun(lr);
    } catch (err) {
      setError(err instanceof Error ? err.message : "E2E run failed");
    } finally {
      setRunning(false);
    }
  };

  const stop = async () => {
    if (jobIdRef.current) {
      try {
        await agent.stopJob?.(jobIdRef.current);
      } catch {
        /* best-effort */
      }
    }
  };

  const rerunFailed = () => {
    if (!lastRun) return;
    // E2ELastRun results have both tc_id and tc_title; match against tc_title
    // so the lookup aligns with the E2ETestCase.title field in the current list.
    const failedTitles = new Set(
      lastRun.results
        .filter((r) => r.status === "fail" || r.status === "error")
        .map((r) => r.tc_title)
    );
    const indices = testCases
      .filter((t) => failedTitles.has(t.title))
      .map((t) => t.index);
    if (indices.length === 0) {
      setError("No matching failed test cases from the last run.");
      return;
    }
    setSelected(new Set(indices));
    run(indices);
  };

  const downloadItems: DownloadItem[] = useMemo(() => {
    if (!result) return [];
    const items: DownloadItem[] = [];
    if (result.report_path) {
      items.push({
        name: result.report_path.split(/[\\/]/).pop() || "e2e_report.xlsx",
        url: agent.artifactDownloadUrl(result.report_path),
        note: "Excel report",
      });
    }
    for (const r of result.results) {
      if (r.video_path) {
        items.push({
          name: `${r.tc_id || r.title}.webm`,
          url: agent.artifactDownloadUrl(r.video_path),
          note: "recording",
        });
      }
    }
    return items;
  }, [result]);

  const progressPct =
    progress && progress.total > 0
      ? Math.round((100 * progress.current) / progress.total)
      : null;

  const footer = (
    <div className="flex w-full items-center gap-2">
      {progressPct != null && (
        <span className="mr-auto text-xs text-[var(--tt-text-muted)]">
          Running {progress?.current}/{progress?.total} ({progressPct}%)
        </span>
      )}
      {progressPct == null && result && (
        <span className="mr-auto text-xs text-[var(--tt-text-secondary)]">
          {result.passed} passed, {result.failed} failed of {result.total}
        </span>
      )}
      <button
        className="tt-btn-primary inline-flex items-center gap-1.5"
        disabled={!canRun}
        onClick={() => run([...selected].sort((a, b) => a - b))}
      >
        {running ? (
          <Loader2 className="h-4 w-4 animate-spin" />
        ) : (
          <Play className="h-4 w-4" strokeWidth={2} />
        )}
        {running ? "Running..." : "Run E2E Tests"}
      </button>
      <button
        className="tt-btn-ghost inline-flex items-center gap-1.5"
        disabled={running || !lastRun}
        onClick={rerunFailed}
        title="Re-run only the test cases that failed in the last run"
      >
        <RotateCcw className="h-4 w-4" strokeWidth={2} />
        Re-run Failed
      </button>
      <button
        className="tt-btn-ghost inline-flex items-center gap-1.5"
        disabled={!running}
        onClick={stop}
        title="Stop after the current test case finishes"
      >
        <Square className="h-4 w-4" strokeWidth={2} />
        Stop
      </button>
      <button className="tt-btn-ghost" onClick={onClose} disabled={running}>
        Close
      </button>
    </div>
  );

  return (
    <Modal
      open
      title={`E2E Automation${
        currentProject ? ` - ${displayName(currentProject)}` : ""
      }`}
      onClose={running ? () => {} : onClose}
      maximized
      footer={footer}
    >
      <div className="flex h-full flex-col gap-4">
        {/* ── Progress bar — shown at TOP during an active run ──────── */}
        {running && (
          <div className="flex flex-col gap-1">
            <div className="flex items-center justify-between text-xs text-[var(--tt-text-muted)]">
              <span className="flex items-center gap-1.5">
                <Loader2 className="h-3 w-3 animate-spin text-[var(--tt-primary)]" />
                Running {progress?.current ?? 0} / {progress?.total ?? (selected.size > 0 ? selected.size : visibleTestCases.length)}
              </span>
              <span className="tabular-nums">{progressPct != null ? `${progressPct}%` : "—"}</span>
            </div>
            <div className="h-1.5 w-full overflow-hidden rounded-full bg-[var(--tt-outline)]">
              <div
                className="h-full rounded-full bg-[var(--tt-primary)] transition-[width] duration-300 ease-out"
                style={{ width: `${progressPct ?? 0}%` }}
              />
            </div>
          </div>
        )}

        {/* Last-run summary bar */}
        {lastRun && (
          <div className="flex items-center gap-2 rounded-lg border border-[var(--tt-outline)] bg-[var(--tt-surface-base)] px-3 py-2">
            <span className="text-xs font-semibold text-[var(--tt-text-muted)]">Last run:</span>
            <span className="tt-badge tt-badge-success">
              <CheckCircle2 className="h-3 w-3" />
              {lastRun.passed} passed
            </span>
            {lastRun.failed > 0 && (
              <span className="tt-badge tt-badge-danger">
                <XCircle className="h-3 w-3" />
                {lastRun.failed} failed
              </span>
            )}
            {lastRun.skipped > 0 && (
              <span className="tt-badge tt-badge-neutral">
                <MinusCircle className="h-3 w-3" />
                {lastRun.skipped} skipped
              </span>
            )}
            <span className="ml-auto text-[10px] text-[var(--tt-text-faint)]">
              {new Date(lastRun.finished_at * 1000).toLocaleString()}
            </span>
          </div>
        )}

        {error && (
          <div
            className="rounded-md border px-3 py-2 text-sm"
            style={{
              borderColor: "var(--tt-danger)",
              color: "var(--tt-danger-hover)",
              background: "var(--tt-surface-high)",
            }}
          >
            {error}
          </div>
        )}

        {/* Environment selector */}
        <label className="flex flex-col gap-1 text-xs text-[var(--tt-text-secondary)]">
          Target environment
          <select
            className="tt-input w-auto min-w-64"
            value={selectedEnv}
            disabled={running || envs.length === 0}
            onChange={(e) => setSelectedEnv(e.target.value)}
          >
            {envs.length === 0 && <option value="">(No credentials configured)</option>}
            {envs.map((e) => (
              <option key={e.env} value={e.env} disabled={!e.has_password}>
                {e.env} - {e.login_url}
                {e.has_password ? "" : " (no password)"}
              </option>
            ))}
          </select>
        </label>

        <div className="grid min-h-0 flex-1 grid-cols-1 gap-4 lg:grid-cols-2">
          {/* Test case list */}
          <div className="flex flex-col rounded-lg border border-[var(--tt-outline)]">
            <div className="flex items-center justify-between border-b border-[var(--tt-outline)] px-3 py-2">
              <span className="text-xs font-semibold uppercase tracking-wide text-[var(--tt-text-muted)]">
                Test cases ({selected.size}/{visibleTestCases.length})
                {wiScope.size > 0 && (
                  <span className="ml-1.5 normal-case text-[var(--tt-text-faint)]">
                    · scoped to {wiScope.size} selected work item
                    {wiScope.size === 1 ? "" : "s"}
                  </span>
                )}
              </span>
              <div className="flex gap-2">
                <button
                  className="text-xs text-[var(--tt-primary-soft)] hover:underline disabled:opacity-50"
                  onClick={selectAll}
                  disabled={running}
                >
                  All
                </button>
                <button
                  className="text-xs text-[var(--tt-primary-soft)] hover:underline disabled:opacity-50"
                  onClick={selectNone}
                  disabled={running}
                >
                  None
                </button>
              </div>
            </div>
            <div className="min-h-0 flex-1 overflow-auto">
              {loading ? (
                <p className="px-3 py-6 text-center text-sm text-[var(--tt-text-muted)]">
                  Loading...
                </p>
              ) : testCases.length === 0 ? (
                <p className="px-3 py-6 text-center text-sm text-[var(--tt-text-muted)]">
                  No generated test cases. Generate test cases first.
                </p>
              ) : wiScope.size === 0 ? (
                <p className="px-3 py-6 text-center text-sm leading-relaxed text-[var(--tt-text-muted)]">
                  Tick one or more work items on the board, then reopen this
                  dialog. Only test cases belonging to the selected work item(s)
                  are shown here.
                </p>
              ) : visibleTestCases.length === 0 ? (
                <p className="px-3 py-6 text-center text-sm leading-relaxed text-[var(--tt-text-muted)]">
                  The selected work item{wiScope.size === 1 ? "" : "s"} ha
                  {wiScope.size === 1 ? "s" : "ve"} no generated E2E test cases.
                  Pick work item(s) that have test cases, or generate test cases
                  for this selection first.
                </p>
              ) : (
                <ul className="divide-y divide-[var(--tt-outline)]">
                  {visibleTestCases.map((tc) => {
                    const st = rowStatus[String(tc.index)] || "pending";
                    // Look up last-run result for this TC by title
                    const lastTc = lastRun?.results.find(
                      (r) => r.tc_title === tc.title
                    );
                    const lastStatus = lastTc?.status;
                    const lastBadgeClass =
                      lastStatus === "pass"
                        ? "tt-badge-success"
                        : lastStatus === "fail" || lastStatus === "error"
                          ? "tt-badge-danger"
                          : lastStatus === "skip"
                            ? "tt-badge-neutral"
                            : null;
                    return (
                      <li key={tc.index}>
                        <label className="flex cursor-pointer items-center gap-2 px-3 py-2 text-sm hover:bg-[var(--tt-surface-high)]">
                          <input
                            type="checkbox"
                            checked={selected.has(tc.index)}
                            disabled={running}
                            onChange={() => toggle(tc.index)}
                          />
                          <span className="shrink-0">{STATUS_ICON[st]}</span>
                          <span className="min-w-0 flex-1 truncate text-[var(--tt-text-secondary)]">
                            {tc.wi_id && (
                              <span className="text-[var(--tt-text-muted)]">
                                [{tc.wi_id}]{" "}
                              </span>
                            )}
                            {tc.title}
                          </span>
                          {lastBadgeClass && (
                            <span className={`tt-badge ${lastBadgeClass} shrink-0`}>
                              {lastStatus}
                            </span>
                          )}
                          <span className="shrink-0 text-xs text-[var(--tt-text-muted)]">
                            {tc.step_count}s
                          </span>
                        </label>
                      </li>
                    );
                  })}
                </ul>
              )}
            </div>
          </div>

          {/* Live log */}
          <div className="flex flex-col rounded-lg border border-[var(--tt-outline)]">
            <div className="border-b border-[var(--tt-outline)] px-3 py-2">
              <span className="text-xs font-semibold uppercase tracking-wide text-[var(--tt-text-muted)]">
                Progress log
              </span>
            </div>
            {progressPct != null && (
              <div className="h-1 w-full bg-[var(--tt-surface-high)]">
                <div
                  className="h-full bg-[var(--tt-primary)] transition-all"
                  style={{ width: `${progressPct}%` }}
                />
              </div>
            )}
            <div className="min-h-0 flex-1 overflow-auto bg-[var(--tt-surface-deepest)] font-mono text-xs leading-relaxed">
              {logs.length === 0 ? (
                <p className="px-3 py-3 text-[var(--tt-text-muted)]">
                  Run output will appear here.
                </p>
              ) : (
                logs.map((line, i) => {
                  const lvl = agentLogLevel(line);
                  const color =
                    lvl === "ERROR"
                      ? "var(--tt-danger)"
                      : lvl === "WARN"
                        ? "var(--tt-warn)"
                        : lvl === "SUCCESS"
                          ? "var(--tt-success)"
                          : "var(--tt-text-secondary)";
                  return (
                    <div
                      key={i}
                      className="whitespace-pre-wrap border-l-2 px-3 py-0.5"
                      style={{
                        color,
                        borderLeftColor:
                          lvl !== "INFO" ? color : "transparent",
                      }}
                    >
                      {line}
                    </div>
                  );
                })
              )}
              <div ref={logEndRef} />
            </div>
          </div>
        </div>

        {downloadItems.length > 0 && (
          <DownloadLinks
            title={`Download E2E artifacts (${downloadItems.length})`}
            items={downloadItems}
          />
        )}
      </div>
    </Modal>
  );
}
