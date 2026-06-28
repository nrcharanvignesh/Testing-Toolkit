"use client";

import { useState } from "react";
import { Modal } from "@/components/ui/modal";
import { agent, TC_DISPLAY_NAME, type TcType } from "@/lib/agent-client";
import { useAppState } from "@/lib/app-state";

const MAX_ITERATIONS = 10;

export function GenerateDialog({ onClose }: { onClose: () => void }) {
  const { selected, boardView, currentProject, displayName, generateCtx, settings, pushLog } =
    useAppState();
  const [mode, setMode] = useState<"auto" | "manual">(
    settings?.has_api_key ? "auto" : "manual"
  );
  const [busy, setBusy] = useState(false);
  const [feedback, setFeedback] = useState("");
  const [iteration, setIteration] = useState(0);
  const [result, setResult] = useState<{ xlsx_path: string; n: number } | null>(
    null
  );
  const [manualJson, setManualJson] = useState("");
  const [status, setStatus] = useState("");

  const tcType = generateCtx.tcType as TcType | "";
  const phase = tcType ? TC_DISPLAY_NAME[tcType] : "test case";
  const ids = [...selected].sort((a, b) => a - b);
  const rows = (boardView?.rows ?? []).filter((r) => selected.has(r.wi_id));

  const run = async (isRegen: boolean) => {
    if (!currentProject) return;
    setBusy(true);
    setStatus(isRegen ? "Regenerating with feedback..." : "Generating test cases...");
    pushLog("INFO", `Generating ${phase} test cases for ${ids.length} work item(s)...`);
    try {
      const res = await agent.generate({
        project: currentProject,
        wi_ids: ids,
        tc_type: tcType,
        feedback: isRegen ? feedback : undefined,
      });
      setResult({ xlsx_path: res.xlsx_path, n: res.n_test_cases });
      setIteration((i) => i + 1);
      setFeedback("");
      setStatus(`Generated ${res.n_test_cases} test case(s).`);
      pushLog("SUCCESS", `Generated ${res.n_test_cases} test case(s): ${res.xlsx_path}`);
    } catch (e) {
      setStatus(`Generation failed: ${(e as Error).message}`);
      pushLog("ERROR", `Generation failed: ${(e as Error).message}`);
    } finally {
      setBusy(false);
    }
  };

  return (
    <Modal
      open
      onClose={onClose}
      title={`Generate ${phase} Test Cases`}
      subtitle={
        currentProject
          ? `${displayName(currentProject)} · ${ids.length} work item(s) selected`
          : undefined
      }
      width={760}
      footer={
        <>
          {status && (
            <span className="mr-auto text-xs text-muted-foreground">{status}</span>
          )}
          <button className="tt-btn-ghost" onClick={onClose} disabled={busy}>
            Close
          </button>
          {mode === "auto" && !result && (
            <button className="tt-btn-success" onClick={() => run(false)} disabled={busy || !ids.length}>
              {busy ? "Generating..." : "Generate"}
            </button>
          )}
        </>
      }
    >
      <div className="flex flex-col gap-4">
        {/* Mode toggle */}
        <div className="flex items-center gap-2">
          <button
            className="tt-btn-ghost !px-3 !py-1 text-xs"
            data-active={mode === "auto"}
            onClick={() => setMode("auto")}
          >
            Automatic (API)
          </button>
          <button
            className="tt-btn-ghost !px-3 !py-1 text-xs"
            data-active={mode === "manual"}
            onClick={() => setMode("manual")}
          >
            Manual Mode
          </button>
          {!settings?.has_api_key && (
            <span className="text-xs text-[#f59e0b]">
              No API key configured — Manual Mode recommended.
            </span>
          )}
        </div>

        {/* Selected work items */}
        <div className="flex flex-col gap-1.5">
          <h4 className="text-xs font-bold uppercase tracking-wide text-[#7abaff]">
            Selected work items
          </h4>
          <div className="max-h-32 overflow-auto rounded-lg border border-[#2d313c] bg-[#13161d] p-2">
            {rows.length === 0 ? (
              <p className="text-sm text-muted-foreground">No work items selected.</p>
            ) : (
              rows.map((r) => (
                <div key={r.wi_id} className="truncate py-0.5 text-sm">
                  <span className="font-semibold text-[#5ba8ff]">#{r.wi_id}</span>{" "}
                  <span className="text-[#bfc4cc]">{r.title}</span>
                </div>
              ))
            )}
          </div>
        </div>

        {mode === "auto" ? (
          <div className="flex flex-col gap-3">
            <div className="tt-help p-3 text-xs leading-relaxed">
              <div className="tt-help-header mb-1">Recursive Language Model pipeline</div>
              <div className="tt-help-body">
                Navigate → Map → Decompose → Generate (extended thinking) → Verify
                + Gap-Fill. Coverage verification and gap-fill are always on. KBs
                up to ~375 pages are passed whole for full coverage.
              </div>
            </div>

            {result && (
              <div className="flex flex-col gap-2 rounded-lg border border-[#1aab5c]/40 bg-[#0d2a1c] p-3">
                <p className="text-sm text-[#22c46a]">
                  Generated {result.n} test case(s). Review Excel:
                </p>
                <code className="break-all text-xs text-[#bfc4cc]">
                  {result.xlsx_path}
                </code>
                <div className="mt-1 flex flex-col gap-1.5">
                  <label className="text-xs text-[#bfc4cc]">
                    Regeneration feedback (iteration {iteration}/{MAX_ITERATIONS})
                  </label>
                  <textarea
                    className="tt-input min-h-20 resize-y"
                    placeholder="Describe changes to apply, then Regenerate..."
                    value={feedback}
                    onChange={(e) => setFeedback(e.target.value)}
                    disabled={iteration >= MAX_ITERATIONS}
                  />
                  <div className="flex justify-end">
                    <button
                      className="tt-btn-success !px-4 !py-1.5 text-sm"
                      onClick={() => run(true)}
                      disabled={busy || !feedback.trim() || iteration >= MAX_ITERATIONS}
                    >
                      Regenerate
                    </button>
                  </div>
                </div>
              </div>
            )}
          </div>
        ) : (
          <ManualMode
            project={currentProject}
            ids={ids}
            tcType={tcType}
            manualJson={manualJson}
            setManualJson={setManualJson}
            pushLog={pushLog}
          />
        )}
      </div>
    </Modal>
  );
}

function ManualMode({
  project,
  ids,
  tcType,
  manualJson,
  setManualJson,
  pushLog,
}: {
  project: string;
  ids: number[];
  tcType: TcType | "";
  manualJson: string;
  setManualJson: (v: string) => void;
  pushLog: (level: "INFO" | "SUCCESS" | "WARN" | "ERROR", t: string) => void;
}) {
  const [prompt, setPrompt] = useState("");
  const [dump, setDump] = useState("");
  const [loading, setLoading] = useState(false);

  const loadContext = async () => {
    if (!project) return;
    setLoading(true);
    try {
      // The agent assembles the system prompt + work-item dump for manual use.
      const res = await agent.complete({
        system: "__manual_context__",
        user: JSON.stringify({ project, wi_ids: ids, tc_type: tcType }),
        max_tokens: 1,
      });
      // Convention: agent returns the prompt+dump joined; fall back gracefully.
      const parts = (res.text || "").split("\n---DUMP---\n");
      setPrompt(parts[0] || "");
      setDump(parts[1] || "");
    } catch (e) {
      pushLog("WARN", `Could not load manual context: ${(e as Error).message}`);
    } finally {
      setLoading(false);
    }
  };

  const copy = (text: string) => navigator.clipboard?.writeText(text);

  return (
    <div className="flex flex-col gap-3">
      <div className="tt-help p-3 text-xs leading-relaxed">
        <div className="tt-help-header mb-1">Manual Mode</div>
        <div className="tt-help-body">
          Copy the system prompt and work-item dump into any LLM session, then
          paste the returned JSON below. The review and push steps are identical
          to automatic mode.
        </div>
      </div>

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
        <label className="text-xs font-bold uppercase tracking-wide text-[#7abaff]">
          Paste JSON response
        </label>
        <textarea
          className="tt-input min-h-28 resize-y font-mono text-xs"
          placeholder='{"test_cases": [...]}'
          value={manualJson}
          onChange={(e) => setManualJson(e.target.value)}
        />
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
        <span className="text-xs font-bold uppercase tracking-wide text-[#7abaff]">
          {label}
        </span>
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
