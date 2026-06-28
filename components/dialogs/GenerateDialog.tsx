"use client";

import { useState } from "react";
import { ChevronDown, ChevronRight } from "lucide-react";
import { Modal } from "@/components/ui/modal";
import {
  agent,
  agentLogLevel,
  TC_DISPLAY_NAME,
  type GenerationResult,
  type JobProgress,
  type TcType,
} from "@/lib/agent-client";
import { useAppState } from "@/lib/app-state";

const MAX_ITERATIONS = 10;

export function GenerateDialog({ onClose }: { onClose: () => void }) {
  const {
    selected,
    boardView,
    currentProject,
    displayName,
    generateCtx,
    settings,
    pushLog,
  } = useAppState();
  const [mode, setMode] = useState<"auto" | "manual">(
    settings?.has_api_key ? "auto" : "manual"
  );
  const [busy, setBusy] = useState(false);
  const [feedback, setFeedback] = useState("");
  const [iteration, setIteration] = useState(0);
  const [result, setResult] = useState<GenerationResult | null>(null);
  const [manualJson, setManualJson] = useState("");
  const [status, setStatus] = useState("");
  const [progress, setProgress] = useState<JobProgress | null>(null);
  const [pushed, setPushed] = useState<string>("");
  const [runLog, setRunLog] = useState<string[]>([]);

  // Custom options (ADO target fields) — desktop parity (I03).
  const [optsOpen, setOptsOpen] = useState(true);
  const [areaPath, setAreaPath] = useState("");
  const [iterationPath, setIterationPath] = useState("");
  const [testCategory, setTestCategory] = useState("");
  const [inherit, setInherit] = useState(true);
  const [fastModel, setFastModel] = useState(false);

  const tcType = generateCtx.tcType as TcType | "";
  const phase = tcType ? TC_DISPLAY_NAME[tcType] : "Test case";
  const projectLabel = currentProject ? displayName(currentProject) : "";
  const titleText = `Generate ${phase} TC - ${projectLabel}`;
  const ids = [...selected].sort((a, b) => a - b);

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
    setBusy(true);
    setPushed("");
    setProgress(null);
    if (!isRegen) setRunLog([]);
    setStatus(
      isRegen ? "Regenerating with feedback..." : "Generating test cases..."
    );
    pushLog(
      "INFO",
      `Generating ${phase} test cases for ${ids.length} work item(s)...`
    );
    try {
      const res = await agent.generate(
        {
          project: currentProject,
          wi_ids: ids,
          tc_type: tcType,
          regen_feedback: isRegen ? feedback : "",
          base_payload: isRegen ? result?.payload ?? null : null,
          fast_model: fastModel,
        },
        handlers
      );
      setResult(res);
      if (isRegen) setIteration((i) => i + 1);
      setFeedback("");
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

  const runManual = async (payload: Record<string, unknown>) => {
    if (!currentProject) return;
    setBusy(true);
    setPushed("");
    setStatus("Validating pasted JSON...");
    try {
      const res = await agent.generate(
        {
          project: currentProject,
          wi_ids: ids,
          tc_type: tcType,
          manual_payload: payload,
        },
        handlers
      );
      setResult(res);
      setStatus(`Loaded ${res.n_test_cases} test case(s). Review, then push.`);
      pushLog("SUCCESS", `Manual payload accepted: ${res.n_test_cases} TC(s).`);
    } catch (e) {
      setStatus(`Validation failed: ${(e as Error).message}`);
      pushLog("ERROR", `Manual payload failed: ${(e as Error).message}`);
    } finally {
      setBusy(false);
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
          {mode === "auto" && (
            <button
              className="tt-btn-success"
              onClick={() => run(false)}
              disabled={busy || !ids.length}
            >
              {busy ? "Generating..." : "AI Generate"}
            </button>
          )}
          <button
            className="tt-btn-ghost"
            data-active={mode === "manual"}
            onClick={() => setMode((m) => (m === "manual" ? "auto" : "manual"))}
            disabled={busy}
          >
            Manual mode
          </button>
          <button className="tt-btn-ghost" disabled title="Stop (desktop only)">
            Stop
          </button>
          <label className="flex items-center gap-1.5 px-1 text-xs text-[#bfc4cc]">
            <input
              type="checkbox"
              className="tt-check"
              checked={fastModel}
              onChange={(e) => setFastModel(e.target.checked)}
              disabled={busy}
            />
            Fast model
          </label>
          <button
            className="tt-btn-ghost"
            onClick={storeExcel}
            disabled={!result}
            title="Open the review Excel workbook"
          >
            Store Excel
          </button>
          <button
            className="tt-btn-success"
            onClick={push}
            disabled={busy || !result}
            title="Create the reviewed test cases in Azure DevOps"
          >
            {busy ? "Working..." : "Push to ADO"}
          </button>
          <button className="tt-btn-ghost" onClick={onClose} disabled={busy}>
            Close
          </button>
        </>
      }
    >
      <div className="flex flex-col gap-4">
        {/* Content heading */}
        <h3 className="text-[15px] font-bold text-[#edf0f5]">
          {titleText}{" "}
          <span className="font-normal text-[#8a8f99]">
            ({ids.length} work item(s))
          </span>
        </h3>

        {/* Custom options (ADO target fields) */}
        <div className="rounded-lg border border-[#2d313c] bg-[#13161d]">
          <button
            className="flex w-full items-center gap-1.5 px-3 py-2 text-left text-sm font-semibold text-[#7abaff]"
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
                <label className="flex items-center gap-2 text-sm text-[#bfc4cc]">
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
        <div className="rounded-lg border border-[#2d313c] bg-[#13161d] p-3">
          <div className="mb-1.5 text-xs text-[#bfc4cc]">
            {busy
              ? progress?.stage || `Fetching ${ids.length} work item(s)...`
              : result
                ? "Done."
                : "Idle."}
          </div>
          <div className="tt-progress">
            <div
              className="tt-progress-chunk"
              style={{ width: `${progressPct ?? (busy ? 8 : 0)}%` }}
            />
          </div>
        </div>

        {mode === "manual" ? (
          <ManualMode
            project={currentProject}
            ids={ids}
            tcType={tcType}
            manualJson={manualJson}
            setManualJson={setManualJson}
            busy={busy}
            onValidate={runManual}
            pushLog={pushLog}
          />
        ) : (
          <>
            {/* Generation log pane */}
            <div className="min-h-40 max-h-72 overflow-auto rounded-lg border border-[#2d313c] bg-[#0d1017] p-3 font-mono text-xs leading-relaxed">
              {runLog.length === 0 ? (
                <p className="text-[#5a5f6a]">
                  Generation log will appear here.
                </p>
              ) : (
                runLog.map((l, i) => (
                  <div key={i} className="whitespace-pre-wrap text-[#bfc4cc]">
                    {l}
                  </div>
                ))
              )}
            </div>

            {result && (
              <RegenerateSection
                pushed={pushed}
                feedback={feedback}
                setFeedback={setFeedback}
                iteration={iteration}
                busy={busy}
                onRegenerate={() => run(true)}
              />
            )}
          </>
        )}
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
      <label className="text-right text-sm text-[#bfc4cc]">{label}</label>
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
  onRegenerate,
}: {
  pushed: string;
  feedback: string;
  setFeedback: (v: string) => void;
  iteration: number;
  busy: boolean;
  onRegenerate: () => void;
}) {
  return (
    <div className="flex flex-col gap-2 rounded-lg border border-[#2d313c] bg-[#13161d] p-3">
      <h4 className="text-sm font-bold text-[#edf0f5]">Regenerate with feedback</h4>
      <p className="text-xs text-[#8a8f99]">
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
      {pushed && <p className="text-sm text-[#22c46a]">{pushed}</p>}
      <div className="flex items-center justify-between">
        <button
          className="tt-btn-ghost !px-3 !py-1.5 text-xs"
          disabled
          title="Attach files (desktop only)"
        >
          Attach files...
        </button>
        <button
          className="tt-btn-primary !px-4 !py-1.5 text-sm"
          onClick={onRegenerate}
          disabled={busy || !feedback.trim() || iteration >= MAX_ITERATIONS}
        >
          Regenerate
        </button>
      </div>
    </div>
  );
}

function ManualMode({
  project,
  ids,
  tcType,
  manualJson,
  setManualJson,
  busy,
  onValidate,
  pushLog,
}: {
  project: string;
  ids: number[];
  tcType: TcType | "";
  manualJson: string;
  setManualJson: (v: string) => void;
  busy: boolean;
  onValidate: (payload: Record<string, unknown>) => void;
  pushLog: (level: "INFO" | "SUCCESS" | "WARN" | "ERROR", t: string) => void;
}) {
  const [prompt, setPrompt] = useState("");
  const [dump, setDump] = useState("");
  const [loading, setLoading] = useState(false);
  const [jsonError, setJsonError] = useState("");

  const loadContext = async () => {
    if (!project) return;
    setLoading(true);
    try {
      const res = await agent.buildDump(project, ids, tcType);
      setPrompt(res.system_prompt || "");
      setDump(res.dump || "");
      pushLog("SUCCESS", `Loaded prompt + dump for ${res.n_items} item(s).`);
    } catch (e) {
      pushLog("WARN", `Could not load manual context: ${(e as Error).message}`);
    } finally {
      setLoading(false);
    }
  };

  const validate = () => {
    setJsonError("");
    let parsed: Record<string, unknown>;
    try {
      parsed = JSON.parse(manualJson);
    } catch (e) {
      setJsonError(`Invalid JSON: ${(e as Error).message}`);
      return;
    }
    onValidate(parsed);
  };

  const copy = (text: string) => navigator.clipboard?.writeText(text);

  return (
    <div className="flex flex-col gap-3">
      <p className="text-xs leading-relaxed text-[#8a8f99]">
        Manual mode: copy the system prompt and work-item dump into any LLM
        session, then paste the returned JSON below and validate it. The review
        and push steps are identical to AI Generate.
      </p>

      <button
        className="tt-btn-ghost self-start !px-3 !py-1.5 text-xs"
        onClick={loadContext}
        disabled={loading || !project}
      >
        {loading ? "Loading..." : "Load prompt & work-item dump"}
      </button>

      {prompt && (
        <CopyBlock label="System prompt" text={prompt} onCopy={() => copy(prompt)} />
      )}
      {dump && (
        <CopyBlock label="Work-item dump" text={dump} onCopy={() => copy(dump)} />
      )}

      <div className="flex flex-col gap-1.5">
        <label className="text-sm font-bold text-[#edf0f5]">
          Paste JSON response
        </label>
        <textarea
          className="tt-input min-h-28 resize-y font-mono text-xs"
          placeholder='{"stories": [...]}'
          value={manualJson}
          onChange={(e) => setManualJson(e.target.value)}
        />
        {jsonError && <p className="text-xs text-[#ef4444]">{jsonError}</p>}
        <div className="flex justify-end">
          <button
            className="tt-btn-primary !px-4 !py-1.5 text-sm"
            onClick={validate}
            disabled={busy || !manualJson.trim()}
          >
            {busy ? "Validating..." : "Validate & Load"}
          </button>
        </div>
      </div>
    </div>
  );
}

function CopyBlock({
  label,
  text,
  onCopy,
}: {
  label: string;
  text: string;
  onCopy: () => void;
}) {
  return (
    <div className="flex flex-col gap-1">
      <div className="flex items-center justify-between">
        <span className="text-sm font-bold text-[#edf0f5]">{label}</span>
        <button className="tt-btn-ghost !px-2 !py-1 text-xs" onClick={onCopy}>
          Copy
        </button>
      </div>
      <pre className="max-h-40 overflow-auto rounded-lg border border-[#2d313c] bg-[#0d1017] p-2 font-mono text-xs text-[#bfc4cc]">
        {text}
      </pre>
    </div>
  );
}
