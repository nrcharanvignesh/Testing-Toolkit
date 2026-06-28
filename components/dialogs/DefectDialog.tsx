"use client";

import { useRef, useState } from "react";
import { Upload, FileText, X } from "lucide-react";
import { Modal } from "@/components/ui/modal";
import {
  agent,
  agentLogLevel,
  type JobProgress,
  type ParsedDefect,
} from "@/lib/agent-client";
import { useAppState } from "@/lib/app-state";

export function DefectDialog({ onClose }: { onClose: () => void }) {
  const { currentProject, displayName, pushLog } = useAppState();
  const [files, setFiles] = useState<File[]>([]);
  const [useLlm, setUseLlm] = useState(true);
  const [busy, setBusy] = useState(false);
  const [defects, setDefects] = useState<ParsedDefect[] | null>(null);
  const [status, setStatus] = useState("");
  const [progress, setProgress] = useState<JobProgress | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  const addFiles = (list: FileList | null) => {
    if (!list) return;
    setFiles((prev) => [...prev, ...Array.from(list)]);
  };

  const handlers = {
    onLog: (line: string) => pushLog(agentLogLevel(line), line),
    onProgress: (p: JobProgress) => setProgress(p),
  };

  const parse = async () => {
    if (!currentProject || files.length === 0) return;
    setBusy(true);
    setStatus("Parsing defect documents...");
    pushLog("INFO", `Parsing ${files.length} defect document(s)...`);
    try {
      const res = await agent.parseDefects(files, useLlm);
      res.logs?.forEach((l) => pushLog(agentLogLevel(l), l));
      setDefects(res.defects.map((d) => ({ ...d, skip: false })));
      setStatus(
        `Parsed ${res.n_defects} defect(s). Review below, then upload.`
      );
      pushLog("SUCCESS", `Parsed ${res.n_defects} defect(s).`);
    } catch (e) {
      setStatus(`Parse failed: ${(e as Error).message}`);
      pushLog("ERROR", `Defect parse failed: ${(e as Error).message}`);
    } finally {
      setBusy(false);
    }
  };

  const upload = async () => {
    if (!currentProject || !defects) return;
    const kept = defects.filter((d) => !d.skip && d.title.trim());
    if (kept.length === 0) {
      setStatus("Nothing to upload — all defects skipped or empty.");
      return;
    }
    setBusy(true);
    setProgress(null);
    setStatus("Creating Bug work items in ADO...");
    try {
      const res = await agent.uploadDefects(currentProject, kept, handlers);
      setStatus(`Created ${res.n_ok} Bug(s), ${res.n_failed} failed.`);
      pushLog("SUCCESS", `Created ${res.n_ok} Bug work item(s) in ADO.`);
    } catch (e) {
      setStatus(`Upload failed: ${(e as Error).message}`);
      pushLog("ERROR", `Defect upload failed: ${(e as Error).message}`);
    } finally {
      setBusy(false);
      setProgress(null);
    }
  };

  const exportExcel = async () => {
    if (!defects) return;
    setBusy(true);
    setStatus("Building reviewer Excel...");
    try {
      await agent.downloadDefectsExcel(defects);
      setStatus("Excel downloaded.");
      pushLog("SUCCESS", "Exported reviewed defects to Excel.");
    } catch (e) {
      setStatus(`Export failed: ${(e as Error).message}`);
      pushLog("ERROR", `Defect Excel export failed: ${(e as Error).message}`);
    } finally {
      setBusy(false);
    }
  };

  const setField = (idx: number, field: keyof ParsedDefect, value: unknown) => {
    setDefects((prev) =>
      prev
        ? prev.map((d, i) => (i === idx ? { ...d, [field]: value } : d))
        : prev
    );
  };

  const keptCount = defects?.filter((d) => !d.skip && d.title.trim()).length ?? 0;
  const progressPct =
    progress && progress.total > 0
      ? Math.round((progress.current / progress.total) * 100)
      : null;

  return (
    <Modal
      open
      onClose={onClose}
      title="Bulk Defect Upload"
      subtitle={
        currentProject ? displayName(currentProject) : "Select a project first"
      }
      width={760}
      footer={
        <>
          {status && (
            <span className="mr-auto text-xs text-muted-foreground">
              {status}
              {progressPct != null && ` · ${progressPct}%`}
            </span>
          )}
          <button className="tt-btn-ghost" onClick={onClose} disabled={busy}>
            Close
          </button>
          {!defects ? (
            <button
              className="tt-btn-primary"
              onClick={parse}
              disabled={busy || !files.length || !currentProject}
            >
              {busy ? "Parsing..." : "Parse & Review"}
            </button>
          ) : (
            <>
              <button
                className="tt-btn-ghost"
                onClick={exportExcel}
                disabled={busy || keptCount === 0}
              >
                Export to Excel
              </button>
              <button
                className="tt-btn-success"
                onClick={upload}
                disabled={busy || keptCount === 0}
              >
                {busy ? "Uploading..." : `Upload ${keptCount} to ADO`}
              </button>
            </>
          )}
        </>
      }
    >
      <div className="flex flex-col gap-4">
        <div className="tt-help p-3 text-xs leading-relaxed">
          <div className="tt-help-header mb-1">Flow</div>
          <div className="tt-help-body">
            Select documents (Word, Excel, PowerPoint, PDF) → adaptive parsing
            (LLM fallback) → review &amp; edit below → upload Bugs. Area Path,
            Iteration, and Board Column are inherited from the parent work item.
          </div>
        </div>

        {!defects && (
          <>
            <div className="flex items-center gap-3">
              <button
                className="tt-btn-primary !px-4 !py-1.5 text-sm"
                onClick={() => fileRef.current?.click()}
              >
                <Upload className="h-4 w-4" /> Select documents
              </button>
              <label className="flex items-center gap-2 text-xs text-[#bfc4cc]">
                <input
                  type="checkbox"
                  className="tt-check"
                  checked={useLlm}
                  onChange={(e) => setUseLlm(e.target.checked)}
                />
                Use LLM fallback when parsing finds nothing
              </label>
            </div>
            <input
              ref={fileRef}
              type="file"
              multiple
              hidden
              accept=".doc,.docx,.xls,.xlsx,.ppt,.pptx,.pdf"
              onChange={(e) => addFiles(e.target.files)}
            />

            <div className="max-h-48 overflow-auto rounded-lg border border-[#2d313c] bg-[#13161d] p-2">
              {files.length === 0 ? (
                <p className="px-2 py-1.5 text-sm text-muted-foreground">
                  No documents selected.
                </p>
              ) : (
                files.map((f, i) => (
                  <div
                    key={`${f.name}-${i}`}
                    className="flex items-center gap-2 px-2 py-1 text-sm"
                  >
                    <FileText className="h-3.5 w-3.5 text-[#5ba8ff]" />
                    <span className="flex-1 truncate text-[#bfc4cc]">
                      {f.name}
                    </span>
                    <button
                      className="tt-btn-ghost h-6 w-6 !border-transparent !p-0"
                      onClick={() =>
                        setFiles((p) => p.filter((_, j) => j !== i))
                      }
                    >
                      <X className="h-3.5 w-3.5" />
                    </button>
                  </div>
                ))
              )}
            </div>
          </>
        )}

        {defects && (
          <div className="flex flex-col gap-2">
            <div className="flex items-center justify-between">
              <h4 className="text-xs font-bold uppercase tracking-wide text-[#7abaff]">
                Review defects ({keptCount} of {defects.length} will upload)
              </h4>
              <button
                className="tt-btn-ghost !px-2 !py-1 text-xs"
                onClick={() => setDefects(null)}
                disabled={busy}
              >
                Back to files
              </button>
            </div>
            <div className="flex max-h-[46vh] flex-col gap-2 overflow-auto">
              {defects.map((d, i) => (
                <div
                  key={i}
                  className={`rounded-lg border p-3 ${
                    d.skip
                      ? "border-[#2d313c] bg-[#13161d] opacity-60"
                      : "border-[#2d313c] bg-[#1a1d26]"
                  }`}
                >
                  <div className="flex items-center gap-2">
                    <label className="flex items-center gap-1.5 text-xs text-[#bfc4cc]">
                      <input
                        type="checkbox"
                        className="tt-check"
                        checked={!d.skip}
                        onChange={(e) => setField(i, "skip", !e.target.checked)}
                      />
                      Include
                    </label>
                    <input
                      className="tt-input flex-1 text-sm"
                      value={d.title}
                      placeholder="Defect title"
                      onChange={(e) => setField(i, "title", e.target.value)}
                    />
                    <select
                      className="tt-input w-28 cursor-pointer text-sm"
                      value={d.severity}
                      onChange={(e) => setField(i, "severity", e.target.value)}
                      aria-label="Severity"
                    >
                      {["Critical", "High", "Medium", "Low"].map((s) => (
                        <option key={s} value={s}>
                          {s}
                        </option>
                      ))}
                    </select>
                  </div>
                  <div className="mt-1 flex flex-wrap gap-x-4 text-xs text-muted-foreground">
                    {d.parent_id > 0 && <span>Parent: #{d.parent_id}</span>}
                    {d.images && d.images.length > 0 && (
                      <span>{d.images.length} image(s)</span>
                    )}
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}
      </div>
    </Modal>
  );
}
