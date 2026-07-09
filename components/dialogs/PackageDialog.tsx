"use client";

import { useState } from "react";
import { Modal } from "@/components/ui/modal";
import {
  DownloadLinks,
  humanSize,
  type DownloadItem,
} from "@/components/ui/download-links";
import {
  agent,
  agentLogLevel,
  sortWiIds,
  type JobProgress,
} from "@/lib/agent-client";
import { useAppState } from "@/lib/app-state";

export function PackageDialog({ onClose }: { onClose: () => void }) {
  const { selected, currentProject, displayName, pushLog } = useAppState();
  // PDF packaging is ADO-only; keep numeric ADO ids and drop any JIRA keys.
  const selectedIds = sortWiIds([...selected]).filter(
    (id): id is number => typeof id === "number"
  );
  const [manualIds, setManualIds] = useState("");
  const [paperSize, setPaperSize] = useState<"A4" | "Letter">("A4");
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState("");
  const [progress, setProgress] = useState<JobProgress | null>(null);
  const [result, setResult] = useState<{ dir: string; n: number } | null>(null);
  const [downloads, setDownloads] = useState<DownloadItem[]>([]);

  const usingSelection = selectedIds.length > 0;

  const parseIds = (): number[] => {
    if (usingSelection) return selectedIds;
    return manualIds
      .split(/[\s,]+/)
      .map((s) => parseInt(s.trim(), 10))
      .filter((n) => !Number.isNaN(n));
  };

  const run = async () => {
    const ids = parseIds();
    if (!currentProject || ids.length === 0) return;
    setBusy(true);
    setProgress(null);
    setDownloads([]);
    // Epoch seconds just before the run, so we can list only the PDFs this
    // packaging produced (artifact.modified is epoch seconds).
    const startedAt = Date.now() / 1000 - 5;
    setStatus(`Packaging ${ids.length} work item(s)...`);
    pushLog("INFO", `Packaging ${ids.length} work item(s) into PDFs...`);
    try {
      const res = await agent.packagePdfs(
        { project: currentProject, wi_ids: ids, paper_size: paperSize },
        {
          onLog: (line) => pushLog(agentLogLevel(line), line),
          onProgress: (p) => setProgress(p),
        }
      );
      setResult({ dir: res.output_dir, n: res.n_package_ok });
      setStatus(`Packaged ${res.n_package_ok} work item(s).`);
      pushLog(
        "SUCCESS",
        `Packaged ${res.n_package_ok} work item(s): ${res.output_dir}`
      );
      // Surface on-screen download links for the freshly packaged PDFs.
      try {
        const arts = await agent.listArtifacts(currentProject);
        const fresh = arts
          .filter((a) => a.kind === "packets" && a.modified >= startedAt)
          .sort((a, b) => b.modified - a.modified)
          .map((a) => ({
            name: a.name,
            url: agent.artifactDownloadUrl(a.path),
            note: humanSize(a.size),
          }));
        setDownloads(fresh);
      } catch {
        // Non-fatal: the Outputs tab still lists everything.
      }
    } catch (e) {
      setStatus(`Packaging failed: ${(e as Error).message}`);
      pushLog("ERROR", `Packaging failed: ${(e as Error).message}`);
    } finally {
      setBusy(false);
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
      title={`Package PDFs${
        currentProject ? ` - ${displayName(currentProject)}` : ""
      }`}
      width={700}
      footer={
        <>
          {status && (
            <span className="mr-auto text-xs text-muted-foreground">
              {status}
              {progressPct != null && ` · ${progress?.stage} ${progressPct}%`}
            </span>
          )}
          <button className="tt-btn-ghost" onClick={onClose} disabled={busy}>
            Close
          </button>
          <button
            className="tt-btn-primary"
            onClick={run}
            disabled={busy || !currentProject || parseIds().length === 0}
          >
            {busy ? "Packaging..." : "Package PDFs"}
          </button>
        </>
      }
    >
      <div className="flex flex-col gap-4">
        <div className="tt-help p-3 text-xs leading-relaxed">
          <div className="tt-help-header mb-1">What you get</div>
          <div className="tt-help-body">
            Per-WI PDFs (cover page, inline images, acceptance criteria, comments,
            attachments converted to PDF), a combined All_WIs_Combined.pdf, and a
            KB-ready chunk folder (Upload to KB/).
          </div>
        </div>

        {usingSelection ? (
          <div className="flex flex-col gap-1.5">
            <h4 className="text-xs font-bold uppercase tracking-wide text-[var(--tt-primary-soft)]">
              Selected work items ({selectedIds.length})
            </h4>
            <div className="max-h-32 overflow-auto rounded-lg border border-[var(--tt-outline)] bg-[var(--tt-surface-base)] p-2 text-sm text-[var(--tt-text-secondary)]">
              {selectedIds.join(", ")}
            </div>
          </div>
        ) : (
          <div className="flex flex-col gap-1.5">
            <h4 className="text-xs font-bold uppercase tracking-wide text-[var(--tt-primary-soft)]">
              Work item IDs
            </h4>
            <textarea
              className="tt-input min-h-24 resize-y font-mono text-sm"
              placeholder="Enter work item IDs separated by spaces or commas, e.g. 1234 1235 1236"
              value={manualIds}
              onChange={(e) => setManualIds(e.target.value)}
            />
          </div>
        )}

        <div className="flex items-center gap-3">
          <h4 className="text-xs font-bold uppercase tracking-wide text-[var(--tt-primary-soft)]">
            Paper size
          </h4>
          <div className="flex gap-1.5">
            {(["A4", "Letter"] as const).map((size) => (
              <button
                key={size}
                type="button"
                onClick={() => setPaperSize(size)}
                disabled={busy}
                className={
                  paperSize === size ? "tt-btn-primary !px-3 !py-1.5 text-xs" : "tt-btn-ghost !px-3 !py-1.5 text-xs"
                }
                aria-pressed={paperSize === size}
              >
                {size}
              </button>
            ))}
          </div>
        </div>

        {result && (
          <div className="rounded-lg border border-[var(--tt-success)]/40 bg-[var(--tt-success-bg)] p-3 text-sm">
            <p className="text-[var(--tt-success-hover)]">
              Packaged {result.n} work item(s). PDFs written to:
            </p>
            <code className="break-all text-xs text-[var(--tt-text-secondary)]">{result.dir}</code>
            {downloads.length === 0 && (
              <p className="mt-1 text-xs text-muted-foreground">
                Open the Outputs tab on a work item to download the packets.
              </p>
            )}
          </div>
        )}

        {downloads.length > 0 && (
          <DownloadLinks
            title={`Download packaged PDFs (${downloads.length})`}
            items={downloads}
          />
        )}
      </div>
    </Modal>
  );
}
