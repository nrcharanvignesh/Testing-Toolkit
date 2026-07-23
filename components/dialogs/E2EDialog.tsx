"use client";

import { useCallback, useEffect, useMemo, useRef, useState, type ReactElement } from "react";
import {
  Play,
  Square,
  RotateCcw,
  CheckCircle2,
  XCircle,
  MinusCircle,
  Loader2,
  Clipboard,
  Check,
  Trash2,
  Paperclip,
  FileText,
  X,
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
type Attachment = { name: string; chars: number; text: string; truncated?: boolean };

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
  const [stopping, setStopping] = useState(false);
  const [logs, setLogs] = useState<string[]>([]);
  const [progress, setProgress] = useState<JobProgress | null>(null);
  const [result, setResult] = useState<E2ERunResult | null>(null);
  const [error, setError] = useState("");
  const [notes, setNotes] = useState("");
  const [userMsg, setUserMsg] = useState("");
  const [logCopied, setLogCopied] = useState(false);
  const [guidanceAttachments, setGuidanceAttachments] = useState<Attachment[]>([]);
  const [notesAttachments, setNotesAttachments] = useState<Attachment[]>([]);
  const jobIdRef = useRef<string>("");
  const logEndRef = useRef<HTMLDivElement>(null);
  const guidanceFileRef = useRef<HTMLInputElement>(null);
  const notesFileRef = useRef<HTMLInputElement>(null);

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
          // Scope tracker-linked discovery to the work items ticked on the
          // board so we fetch only the relevant linked test cases.
          agent.e2eTestCases(currentProject, [...wiScope]),
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
    const active = document.activeElement;
    const isTyping = active?.tagName === "TEXTAREA" || active?.tagName === "INPUT";
    if (!isTyping) {
      logEndRef.current?.scrollIntoView({ behavior: "smooth" });
    }
  }, [logs]);

  const copyLogs = useCallback(() => {
    void navigator.clipboard.writeText(logs.join("\n")).then(() => {
      setLogCopied(true);
      setTimeout(() => setLogCopied(false), 1800);
    });
  }, [logs]);

  const pickFiles = async (
    inputRef: React.RefObject<HTMLInputElement | null>,
    setter: React.Dispatch<React.SetStateAction<Attachment[]>>,
  ) => {
    inputRef.current?.click();
  };

  const handleFilesPicked = async (
    files: FileList | null,
    setter: React.Dispatch<React.SetStateAction<Attachment[]>>,
  ) => {
    const picked = Array.from(files || []);
    if (!picked.length) return;
    const extracted = await agent.extractAttachments(picked);
    setter((prev) => [...prev, ...extracted]);
  };

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

  const run = async (indices: number[], mode: "ai" | "script" = "ai") => {
    if (!currentProject || !selectedEnv) return;
    setRunning(true);
    setStopping(false);
    setError("");
    setLogs([]);
    setProgress(null);
    setResult(null);
    jobIdRef.current = "";
    const chosen = new Set(indices);
    setRowStatus(() => {
      const next: Record<string, RowStatus> = {};
      for (const tc of testCases) {
        const key = String(tc.index);
        next[key] = chosen.has(tc.index) ? "running" : "pending";
      }
      return next;
    });
    let finalNotes = notes.trim();
    if (notesAttachments.length > 0) {
      finalNotes += "\n\n=== ATTACHED REFERENCE FILES ===\n";
      finalNotes += notesAttachments.map((a) => `--- ${a.name} ---\n${a.text}`).join("\n\n");
    }
    pushLog("INFO", `Starting E2E run against "${selectedEnv}" (mode: ${mode})...`);
    try {
      const res = await agent.runE2E(
        {
          project: currentProject,
          env: selectedEnv,
          indices,
          wiIds: [...wiScope],
          notes: finalNotes || undefined,
          mode,
        },
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
      const message = err instanceof Error ? err.message : "E2E run failed";
      if (stopping || /run stopped/i.test(message)) {
        pushLog("WARN", "E2E run stopped by user.");
        setError("");
        setRowStatus((prev) =>
          Object.fromEntries(
            Object.entries(prev).map(([key, value]) => [
              key,
              value === "running" ? "pending" : value,
            ])
          )
        );
      } else {
        setError(message);
      }
    } finally {
      setRunning(false);
      setStopping(false);
    }
  };

  const stop = async () => {
    if (!jobIdRef.current || stopping) return;
    setStopping(true);
    pushLog("WARN", "Stop requested; waiting for the current browser action to finish...");
    try {
      await agent.stopJob(jobIdRef.current);
    } catch (err) {
      setStopping(false);
      setError(err instanceof Error ? err.message : "Could not stop E2E run");
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
    const failedResults = lastRun.results.filter(
      (r) => r.status === "fail" || r.status === "error"
    );
    const hasScripts = failedResults.every((r) => r.script_path);
    setSelected(new Set(indices));
    run(indices, hasScripts ? "script" : "ai");
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
        const ext = r.video_path.endsWith(".mkv") ? ".mkv" : ".webm";
        const safeName = (r.title || r.tc_id).replace(/[^\w\s-]/g, "").replace(/\s+/g, "_").slice(0, 80);
        items.push({
          name: `${safeName}${ext}`,
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
        disabled={!running || stopping}
        onClick={stop}
        title="Stop after the current browser action finishes"
      >
        {stopping ? (
          <Loader2 className="h-4 w-4 animate-spin" />
        ) : (
          <Square className="h-4 w-4" strokeWidth={2} />
        )}
        {stopping ? "Stopping..." : "Stop"}
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
      <div className="flex min-h-0 flex-1 flex-col gap-4">
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

        {/* Pre-run notes / During-run message input */}
        {!running ? (
          <div className="flex flex-col gap-1">
            <span className="text-xs text-[var(--tt-text-secondary)]">
              Notes / Instructions (optional - passed to planner)
            </span>
            <textarea
              className="tt-input min-h-[3rem] resize-y text-xs"
              placeholder="E.g. Focus on navigation flow, skip profile tests..."
              value={notes}
              onChange={(e) => setNotes(e.target.value)}
              rows={2}
            />
            <div className="flex items-center gap-2">
              <button
                className="tt-btn-ghost !px-2 !py-1 !text-[10px] inline-flex items-center gap-1"
                onClick={() => notesFileRef.current?.click()}
              >
                <Paperclip className="h-3 w-3" /> Attach files
              </button>
              <input
                type="file"
                multiple
                hidden
                ref={notesFileRef}
                onChange={(e) => {
                  void handleFilesPicked(e.target.files, setNotesAttachments);
                  e.target.value = "";
                }}
              />
              {notesAttachments.length > 0 && (
                <span className="text-[10px] text-[var(--tt-text-muted)]">
                  {notesAttachments.length} file{notesAttachments.length > 1 ? "s" : ""} attached
                </span>
              )}
            </div>
            {notesAttachments.length > 0 && (
              <div className="flex flex-wrap gap-1.5">
                {notesAttachments.map((a, i) => (
                  <span
                    key={i}
                    className="inline-flex items-center gap-1 rounded bg-[var(--tt-surface-high)] px-2 py-0.5 text-[10px] text-[var(--tt-text-secondary)]"
                  >
                    <FileText className="h-3 w-3" />
                    {a.name}
                    <button
                      className="ml-0.5 hover:text-[var(--tt-danger)]"
                      onClick={() => setNotesAttachments((prev) => prev.filter((_, j) => j !== i))}
                    >
                      <X className="h-3 w-3" />
                    </button>
                  </span>
                ))}
              </div>
            )}
          </div>
        ) : (
          <div className="flex flex-col gap-1.5">
            {guidanceAttachments.length > 0 && (
              <div className="flex flex-wrap gap-1.5">
                {guidanceAttachments.map((a, i) => (
                  <span
                    key={i}
                    className="inline-flex items-center gap-1 rounded bg-[var(--tt-surface-high)] px-2 py-0.5 text-[10px] text-[var(--tt-text-secondary)]"
                  >
                    <FileText className="h-3 w-3" />
                    {a.name}
                    <button
                      className="ml-0.5 hover:text-[var(--tt-danger)]"
                      onClick={() => setGuidanceAttachments((prev) => prev.filter((_, j) => j !== i))}
                    >
                      <X className="h-3 w-3" />
                    </button>
                  </span>
                ))}
              </div>
            )}
            <div className="flex gap-2 items-end">
              <textarea
                className="tt-input flex-1 resize-none text-xs"
                placeholder="Send guidance to the running agent..."
                value={userMsg}
                rows={2}
                onChange={(e) => setUserMsg(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && !e.shiftKey && (userMsg.trim() || guidanceAttachments.length > 0) && jobIdRef.current) {
                    e.preventDefault();
                    let msg = userMsg.trim();
                    if (guidanceAttachments.length > 0) {
                      msg += "\n\n=== ATTACHED REFERENCE FILES ===\n";
                      msg += guidanceAttachments.map((a) => `--- ${a.name} ---\n${a.text}`).join("\n\n");
                    }
                    agent.sendJobMessage(jobIdRef.current, msg);
                    pushLog("INFO", `You: ${userMsg.trim()}${guidanceAttachments.length > 0 ? ` [+${guidanceAttachments.length} files]` : ""}`);
                    setUserMsg("");
                    setGuidanceAttachments([]);
                  }
                }}
              />
              <button
                className="tt-btn-ghost !px-2 !py-1.5"
                onClick={() => guidanceFileRef.current?.click()}
                title="Attach files"
              >
                <Paperclip className="h-4 w-4" />
              </button>
              <input
                type="file"
                multiple
                hidden
                ref={guidanceFileRef}
                onChange={(e) => {
                  void handleFilesPicked(e.target.files, setGuidanceAttachments);
                  e.target.value = "";
                }}
              />
              <button
                className="tt-btn-primary !px-3 !py-1.5 text-xs"
                disabled={(!userMsg.trim() && guidanceAttachments.length === 0) || !jobIdRef.current}
                onClick={() => {
                  if ((userMsg.trim() || guidanceAttachments.length > 0) && jobIdRef.current) {
                    let msg = userMsg.trim();
                    if (guidanceAttachments.length > 0) {
                      msg += "\n\n=== ATTACHED REFERENCE FILES ===\n";
                      msg += guidanceAttachments.map((a) => `--- ${a.name} ---\n${a.text}`).join("\n\n");
                    }
                    agent.sendJobMessage(jobIdRef.current, msg);
                    pushLog("INFO", `You: ${userMsg.trim()}${guidanceAttachments.length > 0 ? ` [+${guidanceAttachments.length} files]` : ""}`);
                    setUserMsg("");
                    setGuidanceAttachments([]);
                  }
                }}
              >
                Send
              </button>
            </div>
          </div>
        )}

        <div className="grid min-h-0 flex-1 grid-cols-1 gap-4 overflow-hidden lg:grid-cols-2">
          {/* Test case list */}
          <div className="flex min-h-0 flex-col rounded-lg border border-[var(--tt-outline)]">
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
                  {wiScope.size === 1 ? "s" : "ve"} no E2E test cases. Pick work
                  item(s) that already have test cases linked in the tracker, or
                  generate test cases for this selection first.
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
                          {tc.source && tc.source !== "generated" && (
                            <span
                              className="tt-badge tt-badge-info shrink-0"
                              title={`Linked ${tc.source.toUpperCase()} test case${tc.tc_id ? ` #${tc.tc_id}` : ""}`}
                            >
                              linked
                            </span>
                          )}
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
          <div className="flex min-h-0 flex-col rounded-lg border border-[var(--tt-outline)]">
            <div className="flex items-center justify-between border-b border-[var(--tt-outline)] px-3 py-2">
              <span className="text-xs font-semibold uppercase tracking-wide text-[var(--tt-text-muted)]">
                Progress log
                {logs.length > 0 && (
                  <span className="ml-1.5 normal-case opacity-60">({logs.length})</span>
                )}
              </span>
              <div className="flex items-center gap-1">
                <button
                  className="tt-btn-ghost !px-1.5 !py-0.5 !text-[10px] !gap-1"
                  onClick={copyLogs}
                  disabled={logs.length === 0}
                  title="Copy all logs to clipboard"
                  aria-label="Copy logs"
                >
                  {logCopied ? (
                    <Check className="h-3 w-3 text-[var(--tt-success)]" />
                  ) : (
                    <Clipboard className="h-3 w-3" />
                  )}
                </button>
                <button
                  className="tt-btn-ghost !px-1.5 !py-0.5 !text-[10px] !gap-1"
                  onClick={() => setLogs([])}
                  disabled={logs.length === 0}
                  title="Clear log"
                  aria-label="Clear log"
                >
                  <Trash2 className="h-3 w-3" />
                </button>
              </div>
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

        {/* Human Review Panel — shown after run completes */}
        {result && !running && (
          <ReviewPanel result={result} onClose={onClose} />
        )}
      </div>
    </Modal>
  );
}

/* ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
   Human Review Panel (Phase 6)
   Displays per-TC results with video links and approve/reject actions.
   ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ */

type ReviewVerdict = "approved" | "rejected" | "pending";

function ReviewPanel({
  result,
  onClose,
}: {
  result: E2ERunResult;
  onClose: () => void;
}) {
  const [verdicts, setVerdicts] = useState<Record<string, ReviewVerdict>>({});
  const [signedOff, setSignedOff] = useState(false);

  const allReviewed =
    result.results.length > 0 &&
    result.results.every((r) => verdicts[r.tc_id] != null);

  const setVerdict = (tcId: string, v: ReviewVerdict) =>
    setVerdicts((prev) => ({ ...prev, [tcId]: v }));

  const handleSignOff = () => {
    setSignedOff(true);
    // Future: persist sign-off to execution_store
  };

  if (signedOff) {
    return (
      <div className="rounded-lg border border-[var(--tt-success)] bg-[var(--tt-surface-high)] p-4 text-center">
        <CheckCircle2 className="mx-auto mb-2 h-8 w-8 text-[var(--tt-success)]" />
        <p className="text-sm font-medium text-[var(--tt-success)]">
          Review signed off. {Object.values(verdicts).filter((v) => v === "approved").length} approved,{" "}
          {Object.values(verdicts).filter((v) => v === "rejected").length} rejected.
        </p>
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-3 rounded-lg border border-[var(--tt-outline)] p-3">
      <div className="flex items-center justify-between">
        <span className="text-xs font-semibold uppercase tracking-wide text-[var(--tt-text-muted)]">
          Human Review
        </span>
        <span className="text-xs text-[var(--tt-text-faint)]">
          {Object.keys(verdicts).length}/{result.results.length} reviewed
        </span>
      </div>

      <div className="max-h-48 overflow-auto divide-y divide-[var(--tt-outline)]">
        {result.results.map((r) => {
          const verdict = verdicts[r.tc_id] || "pending";
          return (
            <div
              key={r.tc_id}
              className="flex items-center gap-2 py-2 text-sm"
            >
              <span className="shrink-0">
                {r.status === "pass" ? (
                  <CheckCircle2 className="h-4 w-4 text-[var(--tt-success)]" />
                ) : (
                  <XCircle className="h-4 w-4 text-[var(--tt-danger)]" />
                )}
              </span>
              <span className="min-w-0 flex-1 truncate text-[var(--tt-text-secondary)]">
                {r.title}
              </span>
              {r.video_path && (
                <a
                  href={`/api/artifact?path=${encodeURIComponent(r.video_path)}`}
                  target="_blank"
                  rel="noreferrer"
                  className="shrink-0 text-xs text-[var(--tt-primary-soft)] hover:underline"
                >
                  Video
                </a>
              )}
              <div className="flex shrink-0 gap-1">
                <button
                  className={`rounded px-2 py-0.5 text-xs transition-colors ${
                    verdict === "approved"
                      ? "bg-[var(--tt-success)] text-white"
                      : "bg-[var(--tt-surface-high)] text-[var(--tt-text-muted)] hover:bg-[var(--tt-success)] hover:text-white"
                  }`}
                  onClick={() => setVerdict(r.tc_id, "approved")}
                >
                  Approve
                </button>
                <button
                  className={`rounded px-2 py-0.5 text-xs transition-colors ${
                    verdict === "rejected"
                      ? "bg-[var(--tt-danger)] text-white"
                      : "bg-[var(--tt-surface-high)] text-[var(--tt-text-muted)] hover:bg-[var(--tt-danger)] hover:text-white"
                  }`}
                  onClick={() => setVerdict(r.tc_id, "rejected")}
                >
                  Reject
                </button>
              </div>
            </div>
          );
        })}
      </div>

      <div className="flex justify-end">
        <button
          className="tt-btn-primary text-xs"
          disabled={!allReviewed}
          onClick={handleSignOff}
          title={allReviewed ? "Sign off on this review" : "Review all test cases first"}
        >
          Sign Off Review
        </button>
      </div>
    </div>
  );
}
