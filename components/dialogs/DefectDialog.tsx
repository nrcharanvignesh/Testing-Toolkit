"use client";

import { useRef, useState } from "react";
import { Upload, FileText, X } from "lucide-react";
import { Modal } from "@/components/ui/modal";
import { agent } from "@/lib/agent-client";
import { useAppState } from "@/lib/app-state";

export function DefectDialog({ onClose }: { onClose: () => void }) {
  const { currentProject, displayName, pushLog } = useAppState();
  const [files, setFiles] = useState<File[]>([]);
  const [busy, setBusy] = useState(false);
  const [review, setReview] = useState<{ path: string; n: number } | null>(null);
  const [status, setStatus] = useState("");
  const fileRef = useRef<HTMLInputElement>(null);

  const addFiles = (list: FileList | null) => {
    if (!list) return;
    setFiles((prev) => [...prev, ...Array.from(list)]);
  };

  const parse = async () => {
    if (!currentProject || files.length === 0) return;
    setBusy(true);
    setStatus("Parsing defect documents...");
    pushLog("INFO", `Parsing ${files.length} defect document(s)...`);
    try {
      const res = await agent.uploadDefects({
        project: currentProject,
        files: files.map((f) => f.name),
      });
      setReview({ path: res.review_xlsx, n: res.n_defects });
      setStatus(`Parsed ${res.n_defects} defect(s). Review the Excel, then upload.`);
      pushLog("SUCCESS", `Parsed ${res.n_defects} defect(s): ${res.review_xlsx}`);
    } catch (e) {
      setStatus(`Parse failed: ${(e as Error).message}`);
      pushLog("ERROR", `Defect parse failed: ${(e as Error).message}`);
    } finally {
      setBusy(false);
    }
  };

  const upload = async () => {
    if (!currentProject || !review) return;
    setBusy(true);
    setStatus("Creating Bug work items in ADO...");
    try {
      const res = await agent.uploadToAdo({
        project: currentProject,
        xlsx_path: review.path,
      });
      setStatus(`Created ${res.created} Bug(s), skipped ${res.skipped}.`);
      pushLog("SUCCESS", `Created ${res.created} Bug work item(s) in ADO.`);
    } catch (e) {
      setStatus(`Upload failed: ${(e as Error).message}`);
      pushLog("ERROR", `Defect upload failed: ${(e as Error).message}`);
    } finally {
      setBusy(false);
    }
  };

  return (
    <Modal
      open
      onClose={onClose}
      title="Bulk Defect Upload"
      subtitle={currentProject ? displayName(currentProject) : "Select a project first"}
      width={720}
      footer={
        <>
          {status && (
            <span className="mr-auto text-xs text-muted-foreground">{status}</span>
          )}
          <button className="tt-btn-ghost" onClick={onClose} disabled={busy}>
            Close
          </button>
          {!review ? (
            <button
              className="tt-btn-primary"
              onClick={parse}
              disabled={busy || !files.length || !currentProject}
            >
              {busy ? "Parsing..." : "Parse & Review"}
            </button>
          ) : (
            <button className="tt-btn-success" onClick={upload} disabled={busy}>
              {busy ? "Uploading..." : "Upload to ADO"}
            </button>
          )}
        </>
      }
    >
      <div className="flex flex-col gap-4">
        <div className="tt-help p-3 text-xs leading-relaxed">
          <div className="tt-help-header mb-1">Flow</div>
          <div className="tt-help-body">
            Select documents (Word, Excel, PowerPoint, PDF) → adaptive parsing
            (LLM fallback) → review Excel with embedded images → upload Bugs. Area
            Path, Iteration, and Board Column are inherited from the parent work
            item.
          </div>
        </div>

        <button
          className="tt-btn-primary self-start !px-4 !py-1.5 text-sm"
          onClick={() => fileRef.current?.click()}
        >
          <Upload className="h-4 w-4" /> Select documents
        </button>
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
                <span className="flex-1 truncate text-[#bfc4cc]">{f.name}</span>
                <button
                  className="tt-btn-ghost h-6 w-6 !border-transparent !p-0"
                  onClick={() => setFiles((p) => p.filter((_, j) => j !== i))}
                >
                  <X className="h-3.5 w-3.5" />
                </button>
              </div>
            ))
          )}
        </div>

        {review && (
          <div className="rounded-lg border border-[#1aab5c]/40 bg-[#0d2a1c] p-3 text-sm">
            <p className="text-[#22c46a]">Review Excel ready ({review.n} defects):</p>
            <code className="break-all text-xs text-[#bfc4cc]">{review.path}</code>
          </div>
        )}
      </div>
    </Modal>
  );
}
