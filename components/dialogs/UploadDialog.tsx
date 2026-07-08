"use client";

import { useEffect, useState } from "react";
import { FileText, RefreshCw } from "lucide-react";
import { Modal } from "@/components/ui/modal";
import { agent, agentLogLevel, type ArtifactFile } from "@/lib/agent-client";
import { useAppState } from "@/lib/app-state";

export function UploadDialog({ onClose }: { onClose: () => void }) {
  const { currentProject, displayName, pushLog, generateCtx } = useAppState();
  const [files, setFiles] = useState<ArtifactFile[]>([]);
  const [selected, setSelected] = useState<string>(generateCtx.xlsxPath ?? "");
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState("");

  useEffect(() => {
    if (!currentProject) return;
    setLoading(true);
    agent
      .listArtifacts(currentProject)
      .then((all) => {
        const reviews = all.filter(
          (f) => f.kind === "testcases" || f.name.toLowerCase().includes("review")
        );
        setFiles(reviews);
        if (!selected && reviews.length) setSelected(reviews[0].path);
      })
      .catch(() => setFiles([]))
      .finally(() => setLoading(false));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [currentProject]);

  const upload = async () => {
    if (!currentProject || !selected) return;
    setBusy(true);
    setStatus("Creating Test Cases in ADO...");
    pushLog("INFO", `Uploading reviewed test cases to ADO...`);
    try {
      const res = await agent.pushReviewedXlsx(
        { project: currentProject, xlsx_path: selected },
        { onLog: (line) => pushLog(agentLogLevel(line), line) }
      );
      setStatus(`Created ${res.n_ok} Test Case(s), ${res.n_failed} failed.`);
      pushLog("SUCCESS", `Created ${res.n_ok} Test Case(s) in ADO.`);
    } catch (e) {
      setStatus(`Upload failed: ${(e as Error).message}`);
      pushLog("ERROR", `Upload failed: ${(e as Error).message}`);
    } finally {
      setBusy(false);
    }
  };

  return (
    <Modal
      open
      onClose={onClose}
      title={`Upload to ADO${
        currentProject ? ` - ${displayName(currentProject)}` : ""
      }`}
      width={700}
      footer={
        <>
          {status && (
            <span className="mr-auto text-xs text-muted-foreground">{status}</span>
          )}
          <button className="tt-btn-ghost" onClick={onClose} disabled={busy}>
            Close
          </button>
          <button
            className="tt-btn-primary"
            onClick={upload}
            disabled={busy || !selected}
          >
            {busy ? "Uploading..." : "Push to ADO"}
          </button>
        </>
      }
    >
      <div className="flex flex-col gap-3">
        <p className="text-sm leading-relaxed text-muted-foreground">
          Creates Test Cases as children of the parent stories using ADO-compliant
          Steps XML. The reviewed Excel is re-read so Skip=Yes edits are honored.
        </p>
        <div className="flex items-center justify-between">
          <h4 className="text-xs font-bold uppercase tracking-wide text-[var(--tt-primary-soft)]">
            Reviewed test-case files
          </h4>
          {loading && <RefreshCw className="h-3.5 w-3.5 animate-spin text-muted-foreground" />}
        </div>
        <div className="max-h-64 overflow-auto rounded-lg border border-[var(--tt-outline)] bg-[var(--tt-surface-base)] p-2">
          {files.length === 0 ? (
            <p className="px-2 py-1.5 text-sm text-muted-foreground">
              No reviewed Excel files found. Generate test cases first.
            </p>
          ) : (
            files.map((f) => (
              <label
                key={f.path}
                className="flex cursor-pointer items-center gap-2 rounded-md px-2 py-1.5 text-sm hover:bg-[var(--tt-surface-container)]"
              >
                <input
                  type="radio"
                  name="upload-file"
                  className="tt-check !rounded-full"
                  checked={selected === f.path}
                  onChange={() => setSelected(f.path)}
                />
                <FileText className="h-3.5 w-3.5 text-[var(--tt-primary)]" />
                <span className="truncate text-[var(--tt-text-secondary)]">{f.name}</span>
              </label>
            ))
          )}
        </div>
      </div>
    </Modal>
  );
}
