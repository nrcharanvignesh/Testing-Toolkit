"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { ChevronDown, ChevronRight, FileText, Loader2, X } from "lucide-react";
import { Modal } from "@/components/ui/modal";
import { DownloadLinks } from "@/components/ui/download-links";
import {
  agent,
  agentLogLevel,
  sortWiIds,
  TC_DISPLAY_NAME,
  type GenerationResult,
  type JobProgress,
  type TcType,
  type WiId,
} from "@/lib/agent-client";
import { userStoryIds } from "@/lib/board-utils";
import { useAppState } from "@/lib/app-state";

const MAX_ITERATIONS = 10;

/** A reference file attached to a regeneration; `text` is the extracted body
 *  the agent reads from the uploaded document. */
type Attachment = {
  name: string;
  chars: number;
  text: string;
  truncated?: boolean;
};

export function GenerateDialog({ onClose }: { onClose: () => void }) {
  const {
    selected,
    boardView,
    currentBoard,
    currentProject,
    displayName,
    generateCtx,
    pushLog,
  } = useAppState();
  const [busy, setBusy] = useState(false);
  const [feedback, setFeedback] = useState("");
  const [iteration, setIteration] = useState(0);
  const [result, setResult] = useState<GenerationResult | null>(null);
  const [status, setStatus] = useState("");
  const [progress, setProgress] = useState<JobProgress | null>(null);
  const [pushed, setPushed] = useState<string>("");
  const [runLog, setRunLog] = useState<string[]>([]);

  // Custom options (ADO target fields).
  const [optsOpen, setOptsOpen] = useState(true);
  const [areaPath, setAreaPath] = useState("");
  const [iterationPath, setIterationPath] = useState("");
  const [testCategory, setTestCategory] = useState("");
  const [inherit, setInherit] = useState(true);
  // Test-data augmentation is always enabled; the toggle was removed from the
  // UI but the behaviour (pattern-based positive/negative data on data-entry
  // steps) is kept on for every generation.
  const testData = true;

  // Files attached to a regeneration. Their extracted text is folded into the
  // feedback prompt at regen time (see run()). Persisted at dialog scope so it
  // survives RegenerateSection re-renders.
  const [attachments, setAttachments] = useState<Attachment[]>([]);

  // When opened from "Load and Regenerate with feedback", the dialog loads an
  // existing artifact's payload up front and recovers its work item ids so the
  // regeneration can re-fetch detail (the board selection may be empty).
  const [loadedIds, setLoadedIds] = useState<WiId[]>([]);
  const [loadingArtifact, setLoadingArtifact] = useState(
    !!generateCtx.loadArtifactPath
  );

  const tcType = generateCtx.tcType as TcType | "";
  const phase = tcType ? TC_DISPLAY_NAME[tcType] : "Test case";
  const projectLabel = currentProject ? displayName(currentProject) : "";
  const titleText = `Generate ${phase} TC - ${projectLabel}`;

  // Desktop parity (main_window._on_generate): use ticked items when present;
  // otherwise SIT/UAT auto-select every User Story / Story on the board so
  // they can generate without an explicit selection. Implementation always
  // requires an explicit selection.
  const autoSelectedStoryIds = useMemo<WiId[]>(() => {
    if (loadedIds.length || selected.size) return [];
    if (tcType !== "sit" && tcType !== "uat") return [];
    return userStoryIds(boardView?.rows ?? []);
  }, [loadedIds.length, selected.size, tcType, boardView?.rows]);

  const usedAutoSelect = autoSelectedStoryIds.length > 0;
  const ids = loadedIds.length
    ? loadedIds
    : selected.size
      ? sortWiIds([...selected])
      : autoSelectedStoryIds;

  // Load the artifact payload once on open (regeneration entry point).
  useEffect(() => {
    const path = generateCtx.loadArtifactPath;
    if (!path) return;
    let cancelled = false;
    setLoadingArtifact(true);
    setStatus("Loading artifact...");
    agent
      .loadArtifact(path)
      .then((res) => {
        if (cancelled) return;
        setResult(res);
        // ADO ids and JIRA keys are recovered separately; merge both so the
        // regeneration re-fetches detail from whichever source this came from.
        setLoadedIds([...(res.wi_ids ?? []), ...(res.wi_keys ?? [])]);
        setStatus(
          `Loaded ${res.n_test_cases} test case(s) from ${res.xlsx_name}. ` +
            "Add feedback below and Regenerate."
        );
        pushLog(
          "SUCCESS",
          `Loaded artifact ${res.xlsx_name} (${res.n_test_cases} TC).`
        );
      })
      .catch((e) => {
        if (cancelled) return;
        setStatus(`Could not load artifact: ${(e as Error).message}`);
        pushLog("ERROR", `Load artifact failed: ${(e as Error).message}`);
      })
      .finally(() => !cancelled && setLoadingArtifact(false));
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [generateCtx.loadArtifactPath]);

  const appendLog = (line: string) => setRunLog((prev) => [...prev, line]);
  const handlers = {
    onLog: (line: string) => {
      appendLog(line);
      pushLog(agentLogLevel(line), line);
    },
    onProgress: (p: JobProgress) => setProgress(p),
  };

  const run = async (isRegen: boolean) => {
    if (!currentProject) return;
    if (!isRegen && ids.length === 0) {
      // SIT/UAT with no selection and no User Stories on the board.
      setStatus("No User Stories found on the current board to generate from.");
      pushLog("WARN", "No work items to generate — select items or add User Stories.");
      return;
    }
    setBusy(true);
    setPushed("");
    setProgress(null);
    if (!isRegen) setRunLog([]);
    setStatus(
      isRegen ? "Regenerating with feedback..." : "Generating test cases..."
    );
    if (!isRegen && usedAutoSelect) {
      pushLog(
        "INFO",
        `No selection — auto-selected ${ids.length} User Story item(s) for ${phase} generation.`
      );
    }
    pushLog(
      "INFO",
      `Generating ${phase} test cases for ${ids.length} work item(s)...`
    );
    // Fold any attached files' extracted text into the regen feedback so the
    // model sees real document context alongside the typed instructions.
    const regenFeedback =
      isRegen && attachments.length
        ? feedback.trim() +
          "\n\n=== ATTACHED REFERENCE FILES ===\n" +
          attachments
            .map((a) => `--- FILE: ${a.name} ---\n${a.text}`)
            .join("\n\n")
        : feedback;
    try {
      const res = await agent.generate(
        {
          project: currentProject,
          wi_ids: ids,
          tc_type: tcType,
          board: currentBoard?.label ?? "",
          regen_feedback: isRegen ? regenFeedback : "",
          base_payload: isRegen ? result?.payload ?? null : null,
          test_data: testData,
        },
        handlers
      );
      setResult(res);
      if (isRegen) setIteration((i) => i + 1);
      setFeedback("");
      if (isRegen) setAttachments([]);
      setStatus(`Generated ${res.n_test_cases} test case(s). Review, then push.`);
      pushLog("SUCCESS", `Generated ${res.n_test_cases} test case(s).`);
    } catch (e) {
      setStatus(`Generation failed: ${(e as Error).message}`);
      pushLog("ERROR", `Generation failed: ${(e as Error).message}`);
    } finally {
      setBusy(false);
      setProgress(null);
    }
  };

  const push = async () => {
    if (!currentProject || !result) return;
    setBusy(true);
    setStatus("Creating test cases in Azure DevOps...");
    try {
      const res = await agent.pushPayload(
        {
          project: currentProject,
          payload: result.payload,
          area_override: areaPath.trim(),
          iteration_override: iterationPath.trim(),
          inherit_paths: inherit,
          test_category_field: testCategory.trim() || "Custom.TestCategory",
        },
        handlers
      );
      setPushed(`Created ${res.n_ok} test case(s), ${res.n_failed} failed.`);
      setStatus(`Created ${res.n_ok} test case(s) in ADO.`);
      pushLog("SUCCESS", `Created ${res.n_ok} test case(s) in ADO.`);
    } catch (e) {
      setStatus(`Push failed: ${(e as Error).message}`);
      pushLog("ERROR", `Push failed: ${(e as Error).message}`);
    } finally {
      setBusy(false);
    }
  };

  const storeExcel = () => {
    if (!result) return;
    window.open(agent.artifactDownloadUrl(result.xlsx_path), "_blank", "noopener");
  };

  const progressPct =
    progress && progress.total > 0
      ? Math.round((progress.current / progress.total) * 100)
      : null;

  return (
    <Modal
      open
      onClose={onClose}
      title={titleText}
      width={820}
      footer={
        <>
          {status && (
            <span className="mr-auto text-xs text-muted-foreground">
              {status}
            </span>
          )}
          <button
            className="tt-btn-success"
            onClick={() => run(false)}
            disabled={busy || loadingArtifact || !ids.length}
          >
            {busy
              ? "Generating..."
              : loadingArtifact
                ? "Loading..."
                : "AI Generate"}
          </button>
          <button
            className="tt-btn-ghost"
            onClick={storeExcel}
            disabled={!result}
            title="Open the review Excel workbook"
          >
            Store Excel
          </button>
          <button
            className="tt-btn-success inline-flex items-center gap-1.5"
            onClick={push}
            disabled={busy || !result}
            title="Create the reviewed test cases in Azure DevOps"
          >
            {busy && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
            {busy ? "Pushing..." : "Push to ADO"}
          </button>
          <button className="tt-btn-ghost" onClick={onClose} disabled={busy}>
            Close
          </button>
        </>
      }
    >
      <div className="flex flex-col gap-4">
        {/* Content heading */}
        <h3 className="text-[15px] font-bold text-[var(--tt-text-primary)]">
          {titleText}{" "}
          <span className="font-normal text-[var(--tt-text-muted)]">
            ({ids.length} work item(s))
          </span>
        </h3>

        {/* Custom options (ADO target fields) */}
        <div className="rounded-lg border border-[var(--tt-outline)] bg-[var(--tt-surface-base)]">
          <button
            className="flex w-full items-center gap-1.5 px-3 py-2 text-left text-sm font-semibold text-[var(--tt-primary-soft)]"
            onClick={() => setOptsOpen((o) => !o)}
          >
            {optsOpen ? (
              <ChevronDown className="h-4 w-4" />
            ) : (
              <ChevronRight className="h-4 w-4" />
            )}
            Custom options (ADO target fields)
          </button>
          {optsOpen && (
            <div className="flex flex-col gap-2.5 px-3 pb-3">
              <OptRow label="Area Path">
                <input
                  className="tt-input"
                  placeholder="Leave blank to inherit from each parent work item."
                  value={areaPath}
                  onChange={(e) => setAreaPath(e.target.value)}
                />
              </OptRow>
              <OptRow label="Iteration Path">
                <input
                  className="tt-input"
                  placeholder="Leave blank to inherit from each parent work item."
                  value={iterationPath}
                  onChange={(e) => setIterationPath(e.target.value)}
                />
              </OptRow>
              <OptRow label="Test Category field">
                <input
                  className="tt-input"
                  placeholder="Custom.TestCategory"
                  value={testCategory}
                  onChange={(e) => setTestCategory(e.target.value)}
                />
              </OptRow>
              <OptRow label="Inheritance">
                <label className="flex items-center gap-2 text-sm text-[var(--tt-text-secondary)]">
                  <input
                    type="checkbox"
                    className="tt-check"
                    checked={inherit}
                    onChange={(e) => setInherit(e.target.checked)}
                  />
                  Inherit Area/Iteration from parent when not overridden
                </label>
              </OptRow>
            </div>
          )}
        </div>

        {/* Progress */}
        <div className="rounded-lg border border-[var(--tt-outline)] bg-[var(--tt-surface-base)] p-3">
          <div className="mb-2 flex items-center justify-between gap-2">
            <span className="text-xs text-[var(--tt-text-secondary)]">
              {busy ? (
                <span className="flex items-center gap-1.5">
                  <span className="tt-animate-pulse-dot inline-block h-2 w-2 rounded-full bg-[var(--tt-warn)]" aria-hidden />
                  {progress?.stage || `Fetching ${ids.length} work item(s)...`}
                </span>
              ) : result ? (
                <span className="text-[var(--tt-success)]">Done.</span>
              ) : "Idle — click AI Generate to start."}
            </span>
            {result && (
              <span className="tt-badge tt-badge-info tt-animate-badge-pop">
                {result.n_test_cases} TC generated
              </span>
            )}
          </div>
          <div className="tt-progress">
            <div
              className="tt-progress-chunk"
              style={{ width: `${progressPct ?? (busy ? 8 : 0)}%` }}
            />
          </div>
          {progressPct != null && (
            <div className="mt-1 text-right text-[10px] tabular-nums text-[var(--tt-text-muted)]">
              {progressPct}%
            </div>
          )}
        </div>

        {/* Inline download link for the generated reviewer workbook. */}
        {result && (
          <DownloadLinks
            title="Generated file"
            items={[
              {
                name: result.xlsx_name,
                url: agent.artifactDownloadUrl(result.xlsx_path),
                note: `${result.n_test_cases} test case(s)`,
              },
            ]}
          />
        )}

        {/* Quality + coverage summary (fresh runs only). */}
        {result && (result.quality || result.coverage) && (
          <div className="flex flex-wrap items-center gap-2">
            {result.quality && (() => {
              const ok = result.quality.avg_score >= 60;
              return (
                <span className={`tt-badge ${ok ? "tt-badge-success" : "tt-badge-warn"} !text-xs !px-3 !py-1`}>
                  Quality {Math.round(result.quality.avg_score)}/100
                  {result.quality.below_threshold > 0 &&
                    ` · ${result.quality.below_threshold} below threshold`}
                </span>
              );
            })()}
            {result.coverage && (() => {
              const ok = result.coverage.uncovered === 0;
              return (
                <span className={`tt-badge ${ok ? "tt-badge-success" : "tt-badge-warn"} !text-xs !px-3 !py-1`}>
                  Coverage {result.coverage.covered}/{result.coverage.total_work_items}{" "}
                  ({Math.round(result.coverage.coverage_pct)}%)
                </span>
              );
            })()}
          </div>
        )}

        <>
            {/* Generation log pane */}
            <div className="min-h-40 max-h-72 overflow-auto rounded-lg border border-[var(--tt-outline)] bg-[var(--tt-surface-deepest)] font-mono text-xs leading-relaxed">
              {runLog.length === 0 ? (
                <p className="px-3 py-3 text-[var(--tt-text-faint)]">
                  Generation log will appear here.
                </p>
              ) : (
                runLog.map((l, i) => {
                  const lvl = agentLogLevel(l);
                  const color =
                    lvl === "ERROR"
                      ? "var(--tt-danger)"
                      : lvl === "WARN"
                        ? "var(--tt-warn)"
                        : lvl === "SUCCESS"
                          ? "var(--tt-success)"
                          : "var(--tt-text-secondary)";
                  const border =
                    lvl === "ERROR"
                      ? "var(--tt-danger)"
                      : lvl === "WARN"
                        ? "var(--tt-warn)"
                        : lvl === "SUCCESS"
                          ? "var(--tt-success)"
                          : "transparent";
                  return (
                    <div
                      key={i}
                      className="whitespace-pre-wrap border-l-2 px-3 py-0.5"
                      style={{ color, borderLeftColor: border }}
                    >
                      {l}
                    </div>
                  );
                })
              )}
            </div>

            {result && (
              <RegenerateSection
                pushed={pushed}
                feedback={feedback}
                setFeedback={setFeedback}
                iteration={iteration}
                busy={busy}
                attachments={attachments}
                setAttachments={setAttachments}
                pushLog={pushLog}
                onRegenerate={() => run(true)}
              />
            )}
          </>
      </div>
    </Modal>
  );
}

function OptRow({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <div className="grid grid-cols-[140px_1fr] items-center gap-3">
      <label className="text-right text-sm text-[var(--tt-text-secondary)]">{label}</label>
      {children}
    </div>
  );
}

function RegenerateSection({
  pushed,
  feedback,
  setFeedback,
  iteration,
  busy,
  attachments,
  setAttachments,
  pushLog,
  onRegenerate,
}: {
  pushed: string;
  feedback: string;
  setFeedback: (v: string) => void;
  iteration: number;
  busy: boolean;
  attachments: Attachment[];
  setAttachments: React.Dispatch<React.SetStateAction<Attachment[]>>;
  pushLog: (level: "INFO" | "SUCCESS" | "WARN" | "ERROR", t: string) => void;
  onRegenerate: () => void;
}) {
  const atLimit = iteration >= MAX_ITERATIONS;
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [extracting, setExtracting] = useState(false);

  const onPickFiles = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const picked = Array.from(e.target.files ?? []);
    e.target.value = ""; // allow re-selecting the same file later
    if (!picked.length) return;
    setExtracting(true);
    try {
      const results = await agent.extractAttachments(picked);
      const good: Attachment[] = [];
      for (const r of results) {
        if (r.error || !r.text) {
          pushLog(
            "WARN",
            `Attachment skipped: ${r.name}${r.error ? ` (${r.error})` : " (no readable text)"}`
          );
          continue;
        }
        good.push({
          name: r.name,
          chars: r.chars,
          text: r.text,
          truncated: r.truncated,
        });
        pushLog(
          "INFO",
          `Attached ${r.name} (${r.chars.toLocaleString()} chars${r.truncated ? ", truncated" : ""}).`
        );
      }
      if (good.length) {
        // De-dupe by name: a re-attached file replaces the previous version.
        setAttachments((prev) => {
          const names = new Set(good.map((g) => g.name));
          return [...prev.filter((p) => !names.has(p.name)), ...good];
        });
      }
    } catch (err) {
      pushLog("ERROR", `Attachment extraction failed: ${(err as Error).message}`);
    } finally {
      setExtracting(false);
    }
  };

  const removeAttachment = (name: string) =>
    setAttachments((prev) => prev.filter((a) => a.name !== name));

  const canRegen =
    !busy && !atLimit && (!!feedback.trim() || attachments.length > 0);

  return (
    <div className="flex flex-col gap-2 rounded-lg border border-[var(--tt-outline)] bg-[var(--tt-surface-base)] p-3">
      <h4 className="text-sm font-bold text-[var(--tt-text-primary)]">Regenerate with feedback</h4>
      <p className="text-xs text-[var(--tt-text-muted)]">
        Describe the changes you want applied ({iteration}/{MAX_ITERATIONS}{" "}
        iterations used).
      </p>
      <textarea
        className="tt-input min-h-20 resize-y"
        placeholder="e.g. Add more negative test cases for field validation, include boundary values for the date picker, merge steps 3 and 4 into a single step..."
        value={feedback}
        onChange={(e) => setFeedback(e.target.value)}
        disabled={iteration >= MAX_ITERATIONS || busy}
      />
      {attachments.length > 0 && (
        <div className="flex flex-wrap gap-2">
          {attachments.map((a) => (
            <span
              key={a.name}
              className="flex items-center gap-1.5 rounded-md border border-[var(--tt-outline)] bg-[var(--tt-surface-base)] px-2 py-1 text-xs text-[var(--tt-text-secondary)]"
              title={`${a.chars.toLocaleString()} characters${a.truncated ? " (truncated)" : ""}`}
            >
              <FileText className="h-3.5 w-3.5 shrink-0 text-[var(--tt-primary)]" />
              <span className="max-w-48 truncate">{a.name}</span>
              <button
                type="button"
                className="text-[var(--tt-text-muted)] hover:text-[var(--tt-text-primary)]"
                onClick={() => removeAttachment(a.name)}
                disabled={busy}
                aria-label={`Remove ${a.name}`}
              >
                <X className="h-3.5 w-3.5" />
              </button>
            </span>
          ))}
        </div>
      )}
      {pushed && <p className="text-sm text-[var(--tt-success-hover)]">{pushed}</p>}
      <div className="flex items-center justify-between">
        <input
          ref={fileInputRef}
          type="file"
          multiple
          className="hidden"
          onChange={onPickFiles}
        />
        <button
          className="tt-btn-ghost !px-3 !py-1.5 text-xs"
          onClick={() => fileInputRef.current?.click()}
          disabled={busy || atLimit || extracting}
          title="Attach reference files (PDF, DOCX, XLSX, PPTX, images, text). Their text is added to your feedback."
        >
          {extracting ? "Reading files..." : "Attach files..."}
        </button>
        <div className="flex items-center gap-3">
          <button
            className="tt-btn-primary !px-4 !py-1.5 text-sm"
            onClick={onRegenerate}
            disabled={!canRegen}
          >
            Regenerate
          </button>
        </div>
      </div>
    </div>
  );
}
