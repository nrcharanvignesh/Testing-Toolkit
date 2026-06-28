"use client";

import { useState } from "react";
import { Modal } from "@/components/ui/modal";
import { agent } from "@/lib/agent-client";
import { useAppState } from "@/lib/app-state";

export function PackageDialog({ onClose }: { onClose: () => void }) {
  const { selected, currentProject, displayName, pushLog } = useAppState();
  const selectedIds = [...selected].sort((a, b) => a - b);
  const [manualIds, setManualIds] = useState("");
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState("");
  const [result, setResult] = useState<{ dir: string; n: number } | null>(null);

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
    setStatus(`Packaging ${ids.length} work item(s)...`);
    pushLog("INFO", `Packaging ${ids.length} work item(s) into PDFs...`);
    try {
      const res = await agent.packagePdfs({ project: currentProject, wi_ids: ids });
      setResult({ dir: res.output_dir, n: res.n_pdfs });
      setStatus(`Packaged ${res.n_pdfs} PDF(s).`);
      pushLog("SUCCESS", `Packaged ${res.n_pdfs} PDF(s): ${res.output_dir}`);
    } catch (e) {
      setStatus(`Packaging failed: ${(e as Error).message}`);
      pushLog("ERROR", `Packaging failed: ${(e as Error).message}`);
    } finally {
      setBusy(false);
    }
  };

  return (
    <Modal
      open
      onClose={onClose}
      title="Package PDFs"
      subtitle={currentProject ? displayName(currentProject) : "Select a project first"}
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
            <h4 className="text-xs font-bold uppercase tracking-wide text-[#7abaff]">
              Selected work items ({selectedIds.length})
            </h4>
            <div className="max-h-32 overflow-auto rounded-lg border border-[#2d313c] bg-[#13161d] p-2 text-sm text-[#bfc4cc]">
              {selectedIds.join(", ")}
            </div>
          </div>
        ) : (
          <div className="flex flex-col gap-1.5">
            <h4 className="text-xs font-bold uppercase tracking-wide text-[#7abaff]">
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

        {result && (
          <div className="rounded-lg border border-[#1aab5c]/40 bg-[#0d2a1c] p-3 text-sm">
            <p className="text-[#22c46a]">Packaged {result.n} PDF(s).</p>
            <code className="break-all text-xs text-[#bfc4cc]">{result.dir}</code>
          </div>
        )}
      </div>
    </Modal>
  );
}
