"use client";

import { useRef, useState } from "react";
import { FileText, X, Upload, Plus } from "lucide-react";
import { Modal } from "@/components/ui/modal";
import {
  agent,
  agentLogLevel,
  type ParsedDefect,
  type JobProgress,
} from "@/lib/agent-client";
import { useAppState } from "@/lib/app-state";

const SEVERITIES = ["Low", "Medium", "High", "Critical"];

const ACCEPT =
  ".docx,.doc,.xlsx,.xls,.pptx,.ppt,.pdf,application/pdf," +
  "application/vnd.openxmlformats-officedocument.wordprocessingml.document";

/**
 * Bulk Defect Upload — web port of the desktop BulkDefectUploadDialog.
 * Flow: select docs -> parse (programmatic, optional LLM fallback) -> review
 * inline -> optionally download the reviewer .xlsx -> create Bug work items.
 */
export function DefectDialog({ onClose }: { onClose: () => void }) {
  const { currentProject, displayName, pushLog } = useAppState();

  const [files, setFiles] = useState<File[]>([]);
  const [useLlm, setUseLlm] = useState(true);
  const [defects, setDefects] = useState<ParsedDefect[]>([]);
  const [parsing, setParsing] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [status, setStatus] = useState("");
  const [progress, setProgress] = useState<JobProgress | null>(null);
  const fileInput = useRef<HTMLInputElement>(null);

  const busy = parsing || uploading;
  const keptCount = defects.filter((d) => !d.skip && d.title.trim()).length;

  const addFiles = (list: FileList | null) => {
    if (!list) return;
    const incoming = Array.from(list);
    setFiles((prev) => {
      const seen = new Set(prev.map((f) => f.name));
      return [...prev, ...incoming.filter((f) => !seen.has(f.name))];
    });
  };

  const removeFile = (name: string) =>
    setFiles((prev) => prev.filter((f) => f.name !== name));

  const parse = async () => {
    if (files.length === 0) return;
    setParsing(true);
    setDefects([]);
    setStatus(`Parsing ${files.length} file(s)...`);
    pushLog("INFO", `Parsing ${files.length} defect document(s)...`);
    try {
      const res = await agent.parseDefects(files, useLlm);
      for (const line of res.logs) pushLog(agentLogLevel(line), line);
      if (res.n_defects === 0) {
        setStatus("No defects found.");
        pushLog("WARN", "No defects could be parsed from the documents.");
      } else {
        setDefects(res.defects.map((d) => ({ ...d, skip: false })));
        setStatus(`Parsed ${res.n_defects} defect(s). Review below.`);
        pushLog("SUCCESS", `Parsed ${res.n_defects} defect(s).`);
      }
    } catch (e) {
      setStatus(`Parse failed: ${(e as Error).message}`);
      pushLog("ERROR", `Defect parse failed: ${(e as Error).message}`);
    } finally {
      setParsing(false);
    }
  };

  const patch = (i: number, k: keyof ParsedDefect, v: unknown) =>
    setDefects((prev) =>
      prev.map((d, idx) => (idx === i ? { ...d, [k]: v } : d))
    );

  const addBlank = () =>
    setDefects((prev) => [
      ...prev,
      {
        parent_id: 0,
        title: "",
        description: "",
        repro_steps: "",
        severity: "Medium",
        expected_result: "",
        actual_result: "",
        skip: false,
      },
    ]);

  const downloadExcel = async () => {
    try {
      await agent.downloadDefectsExcel(defects);
      pushLog("SUCCESS", "Reviewer Excel downloaded.");
    } catch (e) {
      pushLog("ERROR", `Excel export failed: ${(e as Error).message}`);
    }
  };

  const upload = async () => {
    if (!currentProject || keptCount === 0) return;
    const ok = window.confirm(
      `This will create ${keptCount} Bug work item(s) in Azure DevOps.\n\nContinue?`
    );
    if (!ok) return;
    setUploading(true);
    setProgress(null);
    setStatus(`Uploading ${keptCount} bug(s)...`);
    pushLog("INFO", `Uploading ${keptCount} Bug(s) to Azure DevOps...`);
    try {
      const res = await agent.uploadDefects(currentProject, defects, {
        onLog: (line) => pushLog(agentLogLevel(line), line),
        onProgress: (p) => setProgress(p),
      });
      setStatus(`Uploaded: ${res.n_ok} created, ${res.n_failed} failed.`);
      pushLog(
        "SUCCESS",
        `Defect upload complete: ${res.n_ok} created, ${res.n_failed} failed.`
      );
    } catch (e) {
      setStatus(`Upload failed: ${(e as Error).message}`);
      pushLog("ERROR", `Defect upload failed: ${(e as Error).message}`);
    } finally {
      setUploading(false);
      setProgress(null);
    }
  };

  const progressPct =
    progress && progress.total > 0
      ? Math.round((progress.current / progress.total) * 100)
      : null;

  return (
    <Modal
      open
      onClose={onClose}
      title={`Bulk Defect Upload${
        currentProject ? ` - ${displayName(currentProject)}` : ""
      }`}
      width={860}
      footer={
        <>
          {status && (
            <span className="mr-auto text-xs text-muted-foreground">
              {status}
              {progressPct != null && ` · ${progressPct}%`}
            </span>
          )}
          {defects.length > 0 && (
            <button
              className="tt-btn-ghost"
              onClick={downloadExcel}
              disabled={busy}
            >
              Download Excel
            </button>
          )}
          <button className="tt-btn-ghost" onClick={onClose} disabled={busy}>
            Close
          </button>
          <button
            className="tt-btn-success"
            onClick={upload}
            disabled={busy || !currentProject || keptCount === 0}
          >
            {uploading ? "Uploading..." : `Upload to ADO (${keptCount})`}
          </button>
        </>
      }
    >
      <div className="flex flex-col gap-4">
        <div className="tt-help p-3 text-xs leading-relaxed">
          <div className="tt-help-header mb-1">How it works</div>
          <div className="tt-help-body">
            Select defect document(s) (Word, Excel, PowerPoint, or PDF). The
            agent parses them programmatically, falling back to AI when needed.
            Review and edit below (untick a row to exclude it), then create Bug
            work items. Area/Iteration are inherited from each parent work item.
          </div>
        </div>

        {/* File picker */}
        <div className="flex flex-col gap-2">
          <div className="flex items-center gap-2">
            <button
              className="tt-btn-primary !px-4 !py-1.5 text-sm"
              onClick={() => fileInput.current?.click()}
              disabled={busy}
            >
              <Upload className="mr-1.5 inline h-4 w-4" />
              Select files
            </button>
            <label className="flex items-center gap-1.5 text-xs text-[var(--tt-text-secondary)]">
              <input
                type="checkbox"
                checked={useLlm}
                onChange={(e) => setUseLlm(e.target.checked)}
                disabled={busy}
              />
              Use AI fallback when programmatic parsing finds nothing
            </label>
            <div className="flex-1" />
            <button
              className="tt-btn-success !px-4 !py-1.5 text-sm"
              onClick={parse}
              disabled={busy || files.length === 0}
            >
              {parsing ? "Parsing..." : "Parse defects"}
            </button>
          </div>
          <input
            ref={fileInput}
            type="file"
            multiple
            accept={ACCEPT}
            className="hidden"
            onChange={(e) => {
              addFiles(e.target.files);
              e.target.value = "";
            }}
          />
          {files.length > 0 && (
            <div className="flex flex-wrap gap-1.5">
              {files.map((f) => (
                <span
                  key={f.name}
                  className="inline-flex items-center gap-1.5 rounded-md border border-[var(--tt-outline)] bg-[var(--tt-surface-base)] px-2 py-1 text-xs text-[var(--tt-text-secondary)]"
                >
                  <FileText className="h-3.5 w-3.5" />
                  {f.name}
                  <button
                    onClick={() => removeFile(f.name)}
                    disabled={busy}
                    aria-label={`Remove ${f.name}`}
                    className="text-[var(--tt-text-muted)] hover:text-[var(--tt-danger)]"
                  >
                    <X className="h-3.5 w-3.5" />
                  </button>
                </span>
              ))}
            </div>
          )}
        </div>

        {/* Review table */}
        {defects.length > 0 && (
          <div className="flex flex-col gap-2">
            <div className="flex items-center justify-between">
              <h4 className="text-xs font-bold uppercase tracking-wide text-[var(--tt-primary-soft)]">
                Review defects ({keptCount} of {defects.length} selected)
              </h4>
              <button
                className="tt-btn-ghost !px-2.5 !py-1 text-xs"
                onClick={addBlank}
                disabled={busy}
              >
                <Plus className="mr-1 inline h-3.5 w-3.5" />
                Add row
              </button>
            </div>
            <div className="max-h-[42vh] overflow-auto rounded-lg border border-[var(--tt-outline)]">
              <table className="w-full border-collapse text-xs">
                <thead className="sticky top-0 bg-[var(--tt-surface-high)] text-[var(--tt-text-secondary)]">
                  <tr>
                    <th className="px-2 py-2 text-left font-semibold">Use</th>
                    <th className="px-2 py-2 text-left font-semibold">Parent</th>
                    <th className="px-2 py-2 text-left font-semibold">Title</th>
                    <th className="px-2 py-2 text-left font-semibold">
                      Severity
                    </th>
                    <th className="px-2 py-2 text-left font-semibold">
                      Repro steps
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {defects.map((d, i) => (
                    <tr
                      key={i}
                      className="border-t border-[var(--tt-outline-soft)] align-top"
                      style={{ opacity: d.skip ? 0.45 : 1 }}
                    >
                      <td className="px-2 py-1.5">
                        <input
                          type="checkbox"
                          checked={!d.skip}
                          onChange={(e) => patch(i, "skip", !e.target.checked)}
                          disabled={busy}
                          aria-label="Include this defect"
                        />
                      </td>
                      <td className="px-2 py-1.5">
                        <input
                          className="tt-input w-20 !py-1 text-xs"
                          type="number"
                          value={d.parent_id || ""}
                          onChange={(e) =>
                            patch(i, "parent_id", parseInt(e.target.value, 10) || 0)
                          }
                          disabled={busy}
                        />
                      </td>
                      <td className="px-2 py-1.5">
                        <input
                          className="tt-input w-full !py-1 text-xs"
                          value={d.title}
                          onChange={(e) => patch(i, "title", e.target.value)}
                          disabled={busy}
                          placeholder="Defect title"
                        />
                      </td>
                      <td className="px-2 py-1.5">
                        <select
                          className="tt-input w-24 !py-1 text-xs"
                          value={d.severity || "Medium"}
                          onChange={(e) => patch(i, "severity", e.target.value)}
                          disabled={busy}
                        >
                          {SEVERITIES.map((s) => (
                            <option key={s} value={s}>
                              {s}
                            </option>
                          ))}
                        </select>
                      </td>
                      <td className="px-2 py-1.5">
                        <textarea
                          className="tt-input min-h-8 w-full resize-y !py-1 text-xs"
                          value={d.repro_steps}
                          onChange={(e) =>
                            patch(i, "repro_steps", e.target.value)
                          }
                          disabled={busy}
                          rows={1}
                        />
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )}
      </div>
    </Modal>
  );
}
