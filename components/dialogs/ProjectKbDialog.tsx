"use client";

import { useEffect, useRef, useState } from "react";
  import { FileText, RefreshCw, Download, Trash2, Sparkles, Eye, EyeOff, Pencil, Copy, Check, X } from "lucide-react";
import { Modal } from "@/components/ui/modal";
import { agent, type KbStatus, type TcType, TC_TYPES, TC_DISPLAY_NAME, type TemplateStatus, type ProjectContextSummary } from "@/lib/agent-client";
import { useAppState } from "@/lib/app-state";

type UploadStatus = "queued" | "uploading" | "processing" | "done" | "error";

interface UploadItem {
  id: string;
  name: string;
  size: number;
  /** 0..1 transfer fraction. */
  progress: number;
  status: UploadStatus;
  error?: string;
}

/** Thin determinate/indeterminate progress bar. value=null => indeterminate. */
function ProgressBar({
  value,
  color = "var(--tt-primary)",
}: {
  value: number | null;
  color?: string;
}) {
  const indeterminate = value === null;
  const pct = Math.round((value ?? 0) * 100);
  return (
    <div className="h-1.5 w-full overflow-hidden rounded-full bg-[var(--tt-outline)]">
      <div
        className={`h-full rounded-full transition-[width] duration-200 ease-out ${
          indeterminate ? "tt-progress-indeterminate w-2/5" : ""
        }`}
        style={{
          width: indeterminate ? undefined : `${pct}%`,
          backgroundColor: color,
        }}
      />
    </div>
  );
}

function UploadRow({ item }: { item: UploadItem }) {
  const labelByStatus: Record<UploadStatus, string> = {
    queued: "Queued",
    uploading: `${Math.round(item.progress * 100)}%`,
    processing: "Processing...",
    done: "Done",
    error: "Failed",
  };
  const color =
    item.status === "error"
      ? "var(--tt-danger)"
      : item.status === "done"
        ? "var(--tt-success)"
        : "var(--tt-primary)";
  // Indeterminate while the agent processes the fully-uploaded bytes.
  const value =
    item.status === "queued"
      ? 0
      : item.status === "processing"
        ? null
        : item.status === "done"
          ? 1
          : item.progress;
  return (
    <div className="flex flex-col gap-1 px-0.5 py-0.5">
      <div className="flex items-center gap-2 text-xs">
        <FileText className="h-3.5 w-3.5 shrink-0 text-[var(--tt-primary)]" />
        <span className="truncate text-[var(--tt-text-secondary)]" title={item.name}>
          {item.name}
        </span>
        <span
          className="ml-auto shrink-0 font-mono"
          style={{ color }}
          title={item.error}
        >
          {labelByStatus[item.status]}
        </span>
      </div>
      <ProgressBar value={value} color={color} />
    </div>
  );
}

export function ProjectKbDialog({ onClose }: { onClose: () => void }) {
  const { currentProject, displayName, pushLog, kbDirty, kbState, indexKb } =
    useAppState();
  const projectLabel = currentProject ? displayName(currentProject) : "";

  // When the window is closed after documents were added/removed, kick off
  // indexing automatically. It runs at app level (see indexKb) so it keeps
  // going with the window gone — progress shows in the status bar.
  const handleClose = () => {
    if (kbDirty && currentProject && kbState !== "indexing") {
      pushLog("INFO", "Documents changed — indexing knowledge base...");
      void indexKb(currentProject);
    }
    onClose();
  };

  return (
    <Modal
      open
      onClose={handleClose}
      title={`Project Knowledge Base${projectLabel ? ` - ${projectLabel}` : ""}`}
      width={780}
      footer={
        <button className="tt-btn-ghost" onClick={handleClose}>
          Close
        </button>
      }
    >
      <div className="flex flex-col gap-5">
        <h3 className="text-sm font-bold text-[var(--tt-text-primary)]">
          Project Knowledge Base{projectLabel ? ` - ${projectLabel}` : ""}
        </h3>
        <DocumentsSection project={currentProject} pushLog={pushLog} />
        <TemplatesSection project={currentProject} pushLog={pushLog} />
        <ProjectContextSection project={currentProject} pushLog={pushLog} />
        <PromptsSection project={currentProject} pushLog={pushLog} />
      </div>
    </Modal>
  );
}

function DocumentsSection({
  project,
  pushLog,
}: {
  project: string;
  pushLog: (l: "INFO" | "SUCCESS" | "WARN" | "ERROR", t: string) => void;
}) {
  const {
    markKbDirty,
    clearKbDirty,
    indexKb,
    kbUploads,
    kbUploading,
    kbUploadProject,
    uploadKbFiles,
    clearKbUploads,
  } = useAppState();
  const [status, setStatus] = useState<KbStatus | null>(null);
  const [busy, setBusy] = useState(false);
  const [indexing, setIndexing] = useState(false);
  const [indexProgress, setIndexProgress] = useState("");
  const [indexPct, setIndexPct] = useState<number | null>(null);
  const [contextRunning, setContextRunning] = useState(false);
  const [contextProgress, setContextProgress] = useState("");
  const [contextPct, setContextPct] = useState<number | null>(null);
  const [selectedDocs, setSelectedDocs] = useState<Set<string>>(new Set());
  const fileRef = useRef<HTMLInputElement>(null);

  // The upload batch is stored at app level so it survives this dialog being
  // closed/reopened mid-upload — only show the batch that belongs to this
  // project.
  const uploads: UploadItem[] =
    kbUploadProject === project ? kbUploads : [];
  const uploadingHere = kbUploading && kbUploadProject === project;

  const refresh = () => {
    if (!project) return;
    agent
      .kbStatus(project)
      .then(setStatus)
      .catch(() => setStatus(null));
  };

  useEffect(refresh, [project]);

  // Detect an already-running context job on mount so progress bars show even
  // if the dialog was opened after indexing (or a regenerate) already started.
  useEffect(() => {
    if (!project || contextRunning) return;
    agent
      .activeContextJob(project)
      .then((r) => {
        if (r.job_id) setContextRunning(true);
      })
      .catch(() => {});
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [project]);

  useEffect(() => {
    if (!project || !contextRunning) return;
    let cancelled = false;
    let pollCount = 0;
    let lastProgress = -1;
    let staleTicks = 0;
    const MAX_POLLS = 90; // 90 * 2s = 3 min hard cap
    const MAX_STALE = 30; // 30 * 2s = 60s stale cap
    const poll = async () => {
      try {
        const active = await agent.activeContextJob(project);
        if (cancelled) return;
        const progress = active.progress;
        const current = Number(progress?.current ?? 0);
        const total = Number(progress?.total ?? 0);
        if (active.job_id) {
          pollCount++;
          if (current === lastProgress) staleTicks++;
          else { staleTicks = 0; lastProgress = current; }
          if (pollCount >= MAX_POLLS || staleTicks >= MAX_STALE) {
            setContextRunning(false);
            setContextProgress("Timed out — retry from KB panel");
            setContextPct(null);
            return;
          }
          setContextProgress(
            total > 0
              ? `${current}/${total} (${Math.round((100 * current) / total)}%)`
              : "Preparing document maps..."
          );
          setContextPct(total > 0 ? current / total : null);
        } else if (!indexing) {
          setContextRunning(false);
          setContextProgress("");
          setContextPct(null);
        }
      } catch {
        if (!cancelled && !indexing) setContextRunning(false);
      }
    };
    void poll();
    const timer = window.setInterval(poll, 2000);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [project, contextRunning, indexing]);

  // Coerce to an array defensively: `?? []` only guards null/undefined, so an
  // unexpected shape (e.g. a numeric count from an older/partial agent) would
  // otherwise make `docs.map` throw and crash the whole dialog — hiding the
  // Documents list AND the Project Context Edit/Copy/Clear controls below it.
  const docs = Array.isArray(status?.documents) ? status.documents : [];

  // Refresh the persisted document list as each upload completes so files show
  // up ASAP (instead of only after the whole batch finishes). Debounced so rapid
  // consecutive completions do not fire N separate GETs.
  const doneCount = uploads.filter((u) => u.status === "done").length;
  useEffect(() => {
    if (doneCount <= 0) return;
    const t = window.setTimeout(refresh, 300);
    return () => window.clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [doneCount]);

  // Drop selections that no longer exist after a refresh/removal.
  useEffect(() => {
    setSelectedDocs((prev) => {
      const valid = new Set(docs.filter((d) => prev.has(d)));
      return valid.size === prev.size ? prev : valid;
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [status]);

  const allSelected = docs.length > 0 && selectedDocs.size === docs.length;
  const someSelected = selectedDocs.size > 0 && !allSelected;

  const setAll = (on: boolean) =>
    setSelectedDocs(on ? new Set(docs) : new Set());

  const toggleDoc = (name: string, on: boolean) =>
    setSelectedDocs((prev) => {
      const next = new Set(prev);
      if (on) next.add(name);
      else next.delete(name);
      return next;
    });

  const removeSelected = async () => {
    if (!project || selectedDocs.size === 0) return;
    const names = [...selectedDocs];
    const ok = window.confirm(
      `Remove ${names.length} document(s) from the knowledge base? ` +
        "The index will need to be rebuilt afterwards."
    );
    if (!ok) return;
    setBusy(true);
    // Concurrent deletion with concurrency limit of 6
    const CONCURRENCY = 6;
    let okCount = 0;
    let aborted = false;
    for (let i = 0; i < names.length && !aborted; i += CONCURRENCY) {
      const batch = names.slice(i, i + CONCURRENCY);
      const results = await Promise.allSettled(
        batch.map((name) => agent.deleteKbDocument(project, name))
      );
      for (let j = 0; j < results.length; j++) {
        const r = results[j];
        if (r.status === "fulfilled") {
          okCount += 1;
        } else {
          const msg = (r.reason as Error)?.message || "";
          if (/40[34]/.test(msg) && /not found/i.test(msg)) {
            pushLog(
              "ERROR",
              "Remove failed: your local Testing Toolkit agent is out of date " +
                "and doesn't support removing KB documents yet. Update/restart " +
                "the agent (pull the latest build, then relaunch) and try again."
            );
            aborted = true;
            break;
          }
          pushLog("ERROR", `Could not remove ${batch[j]}: ${msg}`);
        }
      }
    }
    setSelectedDocs(new Set());
    pushLog(
      okCount ? "SUCCESS" : "WARN",
      `Removed ${okCount}/${names.length} KB document(s). Re-indexing knowledge base...`
    );
    if (okCount > 0) {
      markKbDirty();
      void indexKb(project);
    }
    refresh();
    setBusy(false);
  };

  const forceClear = async () => {
    if (!project) return;
    const wipeDocs = window.confirm(
      "Force-clear the knowledge base?\n\n" +
        "This immediately stops any in-progress indexing or context mapping and " +
        "deletes the index, vectors, and project context.\n\n" +
        "Click OK to ALSO delete all uploaded documents (full wipe), or Cancel " +
        "to keep the documents and clear only the derived index."
    );
    // OK -> full wipe (remove documents); Cancel -> keep documents, clear index.
    // Either branch is still a force-clear — the user explicitly asked to be
    // able to terminate at any time, so there is no "do nothing" path here.
    const keepDocuments = !wipeDocs;
    setBusy(true);
    // Optimistically stop local progress UI so it doesn't look stuck.
    setIndexing(false);
    setIndexProgress("");
    setIndexPct(null);
    setContextRunning(false);
    setContextProgress("");
    setContextPct(null);
    pushLog("WARN", "Clearing knowledge base...");
    try {
      const r = await agent.clearKb(project, keepDocuments);
      // Belt-and-suspenders: explicitly delete context so stale job refs
      // don't cause the polling loop to restart after reload.
      await agent.clearContext(project).catch(() => {});
      clearKbDirty();
      setSelectedDocs(new Set());
      if (!keepDocuments) clearKbUploads();
      pushLog(
        "SUCCESS",
        `Knowledge base cleared${
          r.stopped_jobs.length ? ` (stopped ${r.stopped_jobs.length} job(s))` : ""
        }${keepDocuments ? " — documents kept" : ""}.`
      );
      refresh();
      window.location.reload();
    } catch (e) {
      pushLog("ERROR", `Clear KB failed: ${(e as Error).message}`);
    } finally {
      setBusy(false);
    }
  };

  const upload = async (files: FileList | null) => {
    if (!files || !project || files.length === 0) return;
    // Hand the batch to app-level state: it shows the files immediately, keeps
    // running (and visible) if this window is closed/reopened, drives the
    // status-bar "Uploading X/Y" indicator, and auto-starts indexing when the
    // batch finishes. Reset the file input so the same files can be re-picked.
    const fileArr = Array.from(files);
    if (fileRef.current) fileRef.current.value = "";
    await uploadKbFiles(project, fileArr);
    refresh();
  };

  const reindex = async () => {
    if (!project) return;
    setIndexing(true);
    setIndexProgress("Starting...");
    setIndexPct(null);
    setContextRunning(true);
    setContextProgress("Starting alongside indexing...");
    setContextPct(null);
    pushLog("INFO", "Rebuilding KB index...");
    const start = Date.now();
    const fmt = (s: number) => {
      const v = Math.max(0, Math.floor(s));
      return v < 60 ? `${v}s` : `${Math.floor(v / 60)}m ${String(v % 60).padStart(2, "0")}s`;
    };
    try {
      const r = await agent.kbIndex(
        project,
        {
        onProgress: (p) => {
          const { current: done, total, stage } = p;
          if (!total || total <= 0) {
            setIndexProgress("Scanning...");
            setIndexPct(null);
            return;
          }
          if (done >= total) {
            setIndexProgress("Finalizing...");
            setIndexPct(1);
            return;
          }
          const elapsed = (Date.now() - start) / 1000;
          const pct = Math.round((100 * done) / Math.max(total, 1));
          const remaining = done > 0 ? (elapsed / done) * (total - done) : 0;
          const name = stage && stage !== "indexing" ? ` (${stage})` : "";
          setIndexPct(done / total);
          setIndexProgress(
            `${done}/${total}${name} | ${fmt(elapsed)} / ${fmt(remaining)} - ${pct}%`
          );
        },
        },
        true // explicit "Rebuild KB index": always do a full rebuild
      );
      setIndexProgress("");
      setIndexPct(null);
      clearKbDirty(); // freshly rebuilt — no auto-index needed on close
      pushLog("SUCCESS", `Indexed ${r.n_documents} doc(s), ${r.n_chunks} chunk(s).`);
      refresh();
    } catch (e) {
      setIndexProgress("");
      setIndexPct(null);
      pushLog("ERROR", `Index failed: ${(e as Error).message}`);
    } finally {
      setIndexing(false);
      // If context polling is still running after index completes, give
      // the agent one final check: if no active job remains, stop polling.
      if (contextRunning) {
        agent.activeContextJob(project).then((r) => {
          if (!r.job_id) {
            setContextRunning(false);
            setContextProgress("");
            setContextPct(null);
          }
        }).catch(() => {
          setContextRunning(false);
        });
      }
    }
  };

  return (
    <section className="flex flex-col gap-2">
      <div className="flex items-center gap-2">
        <h4 className="mr-auto text-sm font-bold text-[var(--tt-text-primary)]">Documents</h4>
        <button
          className="tt-btn-primary !px-3 !py-1.5 text-xs"
          disabled={busy || uploadingHere || !project}
          onClick={() => fileRef.current?.click()}
        >
          {uploadingHere ? "Uploading..." : "Add files"}
        </button>
        <button
          className="tt-btn-ghost !px-3 !py-1.5 text-xs"
          disabled={busy || selectedDocs.size === 0}
          onClick={removeSelected}
          title="Remove the checked documents from the knowledge base"
        >
          Remove selected
          {selectedDocs.size > 0 ? ` (${selectedDocs.size})` : ""}
        </button>
        <button
          className="tt-btn-ghost !px-3 !py-1.5 text-xs"
          disabled={indexing || !project}
          onClick={reindex}
        >
          <RefreshCw className={`h-3.5 w-3.5 ${indexing ? "animate-spin" : ""}`} />{" "}
          Rebuild index
        </button>
        {/* Force-clear stays enabled even mid-index so it can terminate a
            stuck/running job and wipe the KB at any point. */}
        <button
          className="tt-btn-ghost !px-3 !py-1.5 text-xs !text-[var(--tt-danger)]"
          disabled={busy || !project}
          onClick={forceClear}
          title="Stop any running indexing and wipe the knowledge base"
        >
          <Trash2 className="h-3.5 w-3.5" /> Clear KB
        </button>
        <input
          ref={fileRef}
          type="file"
          multiple
          hidden
          onChange={(e) => upload(e.target.files)}
        />
      </div>

      {docs.length > 0 && (
        <div className="flex items-center gap-2 px-1">
          <label className="flex cursor-pointer items-center gap-2 text-xs text-[var(--tt-text-secondary)]">
            <input
              type="checkbox"
              className="tt-check"
              checked={allSelected}
              ref={(el) => {
                if (el) el.indeterminate = someSelected;
              }}
              onChange={(e) => setAll(e.target.checked)}
            />
            Select all
          </label>
          <span className="text-xs text-muted-foreground">
            {selectedDocs.size} of {docs.length} selected
          </span>
        </div>
      )}

      <div
        className="max-h-52 overflow-auto rounded-lg border border-[var(--tt-outline)] bg-[var(--tt-surface-base)] p-1 focus:outline-none focus-visible:ring-1 focus-visible:ring-[var(--tt-primary)]"
        tabIndex={0}
        role="listbox"
        aria-multiselectable="true"
        aria-label="Knowledge base documents"
        onKeyDown={(e) => {
          // Ctrl/Cmd+A selects every document without leaving the dialog.
          if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "a") {
            e.preventDefault();
            setAll(true);
          }
        }}
      >
        {docs.length === 0 ? (
          <p className="px-2 py-1.5 text-sm text-muted-foreground">
            {uploadingHere
              ? "Uploading documents — they will appear here as each file finishes..."
              : "No documents uploaded yet."}
          </p>
        ) : (
          docs.map((d) => {
            const isSel = selectedDocs.has(d);
            return (
              <label
                key={d}
                className="flex w-full cursor-pointer items-center gap-2 rounded-md px-2 py-1 text-left text-sm hover:bg-[var(--tt-surface-container)]"
                style={{
                  background: isSel ? "var(--tt-row-sel)" : "transparent",
                  color: isSel ? "#ffffff" : "var(--tt-text-secondary)",
                }}
              >
                <input
                  type="checkbox"
                  className="tt-check"
                  checked={isSel}
                  onChange={(e) => toggleDoc(d, e.target.checked)}
                />
                <FileText className="h-3.5 w-3.5 shrink-0 text-[var(--tt-primary)]" />
                <span className="truncate">{d}</span>
              </label>
            );
          })
        )}
      </div>

      {uploads.length > 0 && (
        <div className="flex flex-col gap-1.5 rounded-lg border border-[var(--tt-outline)] bg-[var(--tt-surface-base)] p-2">
          <div className="flex items-center justify-between px-0.5">
            <span className="text-xs font-semibold text-[var(--tt-text-secondary)]">
              {uploadingHere ? "Uploading" : "Uploaded"}{" "}
              {uploads.filter((u) => u.status === "done").length}/
              {uploads.length} file(s)
            </span>
            {!uploadingHere && (
              <button
                className="text-xs text-[var(--tt-text-muted)] hover:text-[var(--tt-text-secondary)]"
                onClick={clearKbUploads}
              >
                Dismiss
              </button>
            )}
          </div>
          {uploads.map((u) => (
            <UploadRow key={u.id} item={u} />
          ))}
        </div>
      )}

      {(indexing || contextRunning) && (
        <div className="flex flex-col gap-3 rounded-lg border border-[var(--tt-outline)] bg-[var(--tt-surface-base)] p-3">
          {indexing && (
            <div className="flex flex-col gap-1">
              <div className="flex items-center justify-between font-mono text-xs text-[var(--tt-warn-alt)]">
                <span>KB index</span>
                <span>{indexProgress}</span>
              </div>
              <ProgressBar value={indexPct} color="var(--tt-warn-alt)" />
            </div>
          )}
          {contextRunning && (
            <div className="flex flex-col gap-1">
              <div className="flex items-center justify-between font-mono text-xs text-[var(--tt-primary)]">
                <span>Project context</span>
                <span>{contextProgress}</span>
              </div>
              <ProgressBar value={contextPct} color="var(--tt-primary)" />
            </div>
          )}
        </div>
      )}

      {status && !indexing && !contextRunning && (
        <p className="text-xs leading-relaxed text-[var(--tt-success)]">
          {status.indexed
            ? `Indexing ${status.n_documents ?? docs.length} document(s), ${
                status.n_chunks ?? "?"
              } chunk(s). Generation mode: recursive retrieval (navigate + map). Retrieval: BM25 lexical (always on), dense vectors, reranker.`
            : "Not yet indexed. Click Rebuild index."}
        </p>
      )}
    </section>
  );
}

function TemplatesSection({
  project,
  pushLog,
}: {
  project: string;
  pushLog: (l: "INFO" | "SUCCESS" | "WARN" | "ERROR", t: string) => void;
}) {
  const [phase, setPhase] = useState<TcType>("implementation");
  const [status, setStatus] = useState<TemplateStatus | null>(null);
  const [busy, setBusy] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);

  // Load template status whenever project or phase changes.
  useEffect(() => {
    if (!project) { setStatus(null); return; }
    agent
      .templateStatus(project, phase)
      .then(setStatus)
      .catch(() => setStatus(null));
  }, [project, phase]);

  const upload = async (files: FileList | null) => {
    if (!files || !files[0] || !project) return;
    const file = files[0];
    if (fileRef.current) fileRef.current.value = "";
    setBusy(true);
    try {
      const s = await agent.uploadTemplate(project, phase, file);
      setStatus(s);
      pushLog("SUCCESS", `Template uploaded for ${TC_DISPLAY_NAME[phase]}: ${file.name}`);
    } catch (e) {
      pushLog("ERROR", `Template upload failed: ${(e as Error).message}`);
    } finally {
      setBusy(false);
    }
  };

  const openTemplate = () => {
    if (!project || !status?.has) return;
    window.open(agent.templateDownloadUrl(project, phase), "_blank", "noopener");
  };

  const removeTemplate = async () => {
    if (!project || !status?.has) return;
    const ok = window.confirm(
      `Remove the ${TC_DISPLAY_NAME[phase]} template? Generation will fall back to the standard reviewer spreadsheet.`
    );
    if (!ok) return;
    setBusy(true);
    try {
      await agent.deleteTemplate(project, phase);
      setStatus((prev) => prev ? { ...prev, has: false, name: "" } : prev);
      pushLog("INFO", `Removed ${TC_DISPLAY_NAME[phase]} template.`);
    } catch (e) {
      pushLog("ERROR", `Remove failed: ${(e as Error).message}`);
    } finally {
      setBusy(false);
    }
  };

  const hasTemplate = !!status?.has;

  return (
    <section className="flex flex-col gap-2">
      <h4 className="text-sm font-bold text-[var(--tt-text-primary)]">
        Test script templates (per phase)
      </h4>
      <div className="flex items-center gap-2">
        <label className="text-sm text-[var(--tt-text-secondary)]">Phase:</label>
        <select
          className="tt-input w-auto min-w-44 cursor-pointer text-sm"
          value={phase}
          disabled={busy}
          onChange={(e) => setPhase(e.target.value as TcType)}
        >
          {TC_TYPES.map((t) => (
            <option key={t} value={t}>{TC_DISPLAY_NAME[t]}</option>
          ))}
        </select>
        <button
          className="tt-btn-primary !px-3 !py-1.5 text-xs"
          disabled={busy || !project}
          onClick={() => fileRef.current?.click()}
        >
          {hasTemplate ? "Replace template" : "Upload template"}
        </button>
        <button
          className="tt-btn-ghost !px-3 !py-1.5 text-xs"
          disabled={busy || !hasTemplate}
          onClick={openTemplate}
          title="Download and open the stored template"
        >
          <Download className="h-3.5 w-3.5" /> Open
        </button>
        <button
          className="tt-btn-ghost !px-3 !py-1.5 text-xs"
          disabled={busy || !hasTemplate}
          onClick={removeTemplate}
          title="Remove the stored template for this phase"
        >
          <Trash2 className="h-3.5 w-3.5" /> Remove
        </button>
        <input
          ref={fileRef}
          type="file"
          accept=".xlsx,.xls"
          hidden
          onChange={(e) => upload(e.target.files)}
        />
      </div>
      {!project ? (
        <p className="text-xs text-muted-foreground">Select a project to manage templates.</p>
      ) : status === null ? (
        <p className="text-xs text-muted-foreground">Loading...</p>
      ) : hasTemplate ? (
        <p className="text-xs text-[var(--tt-success)]">
          Template: <span className="font-mono">{status.name}</span>
          {status.describe ? ` — ${status.describe}` : ""}
        </p>
      ) : (
        <p className="text-xs text-muted-foreground">
          No template uploaded for {TC_DISPLAY_NAME[phase]}. Generation falls back to the standard
          reviewer spreadsheet only.
        </p>
      )}
    </section>
  );
}

export const PROMPT_SCOPES: { value: string; label: string }[] = [
  { value: "", label: "General (default)" },
  { value: "implementation", label: "Implementation phase" },
  { value: "sit", label: "SIT phase" },
  { value: "uat", label: "UAT phase" },
];

/**
 * Project Context (desktop KB dialog "Project Context"): view the auto-extracted
 * context summary (actors, entities, workflows, screens, ...) and regenerate it
 * from the current KB index using the LLM.
 */
function ProjectContextSection({
  project,
  pushLog,
}: {
  project: string;
  pushLog: (l: "INFO" | "SUCCESS" | "WARN" | "ERROR", t: string) => void;
}) {
  const [ctx, setCtx] = useState<ProjectContextSummary | null>(null);
  const [showSummary, setShowSummary] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState("");
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    if (!project) return;
    let alive = true;
    agent
      .projectContext(project)
      .then((c) => alive && setCtx(c))
      .catch(() => {
        // Older agents (< 2.9.4) don't expose this route — degrade to the
        // empty state rather than spinning forever.
        if (alive)
          setCtx({
            has: false,
            n_items: 0,
            counts: {},
            summary: "",
            status: "unavailable",
            enabled: true,
            mapped_documents: 0,
            total_documents: 0,
            failed_documents: [],
          });
      });
    return () => {
      alive = false;
    };
  }, [project]);

  const toggleEnabled = async () => {
    if (!project || !ctx?.has) return;
    const newEnabled = !ctx.enabled;
    setBusy(true);
    setError("");
    try {
      const updated = await agent.setContextEnabled(project, newEnabled);
      setCtx(updated);
      pushLog(
        "INFO",
        newEnabled
          ? "Project context will be injected into generation prompts."
          : "Project context injection disabled."
      );
    } catch (e) {
      const msg = (e as Error).message;
      setError(msg);
      pushLog("ERROR", `Failed to update context setting: ${msg}`);
    } finally {
      setBusy(false);
    }
  };

  const regenerate = async () => {
    if (!project) return;
    setBusy(true);
    setError("");
    pushLog("INFO", "Re-extracting project context from the knowledge base...");
    try {
      const c = await agent.regenerateContext(project);
      setCtx(c);
      if (c.has) {
        pushLog("SUCCESS", `Project context extracted: ${c.n_items} item(s).`);
        setShowSummary(true);
      } else {
        pushLog("WARN", "No project context could be extracted.");
      }
    } catch (e) {
      const msg = (e as Error).message;
      setError(msg);
      pushLog("ERROR", `Context extraction failed: ${msg}`);
    } finally {
      setBusy(false);
    }
  };

  const startEdit = () => {
    setDraft(ctx?.summary ?? "");
    setEditing(true);
    setShowSummary(true);
    setError("");
  };

  const saveEdit = async () => {
    if (!project) return;
    setBusy(true);
    setError("");
    try {
      const c = await agent.editContext(project, draft);
      setCtx(c);
      setEditing(false);
      pushLog("SUCCESS", "Project context saved.");
    } catch (e) {
      const msg = (e as Error).message;
      setError(msg);
      pushLog("ERROR", `Could not save project context: ${msg}`);
    } finally {
      setBusy(false);
    }
  };

  const copySummary = async () => {
    const text = ctx?.summary ?? "";
    if (!text) return;
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1500);
    } catch {
      setError("Clipboard is unavailable in this browser.");
    }
  };

  const clearContext = async () => {
    if (!project || !ctx?.has) return;
    if (
      !window.confirm(
        "Clear the stored project context for this project? This removes the extracted summary and any edits."
      )
    )
      return;
    setBusy(true);
    setError("");
    try {
      const c = await agent.clearContext(project);
      setCtx(c);
      setEditing(false);
      setShowSummary(false);
      pushLog("INFO", "Project context cleared.");
    } catch (e) {
      const msg = (e as Error).message;
      setError(msg);
      pushLog("ERROR", `Could not clear project context: ${msg}`);
    } finally {
      setBusy(false);
    }
  };

  const statusText = !ctx
    ? "Loading project context..."
    : ctx.has
      ? `${ctx.status === "partial" ? "Partial context" : ctx.status === "preserved" ? "Context preserved" : "Context complete"}: ${
          ctx.mapped_documents
        }/${ctx.total_documents} documents, ${ctx.n_items} items (${
          ctx.counts.actors ?? 0
        } actors, ${ctx.counts.entities ?? 0} entities, ${
          ctx.counts.workflows ?? 0
        } workflows, ${ctx.counts.screens ?? 0} screens)${ctx.edited ? " • edited" : ""}`
      : "No project context extracted yet. Index the knowledge base with the AI API available, or click Regenerate.";

  const canToggle = ctx?.has && !busy;

  return (
    <div className="flex flex-col gap-2">
      <div className="flex items-center gap-2">
        <h4 className="mr-auto text-sm font-bold text-[var(--tt-text-primary)]">
          Project Context
        </h4>
        {canToggle && (
          <label className="flex cursor-pointer items-center gap-1.5 text-xs text-[var(--tt-text-secondary)]">
            <input
              type="checkbox"
              className="tt-check"
              checked={ctx.enabled}
              onChange={toggleEnabled}
              disabled={busy}
            />
            Inject into generation
          </label>
        )}
        <button
          className="tt-btn-ghost !px-2.5 !py-1 !text-xs !gap-1.5"
          onClick={() => setShowSummary((s) => !s)}
          disabled={!ctx?.has || editing}
          title="View the project context summary (actors, entities, workflows, screens, ...)"
        >
          {showSummary ? (
            <EyeOff className="h-3.5 w-3.5" />
          ) : (
            <Eye className="h-3.5 w-3.5" />
          )}
          {showSummary ? "Hide" : "View"}
        </button>
        <button
          className="tt-btn-ghost !px-2.5 !py-1 !text-xs !gap-1.5"
          onClick={startEdit}
          disabled={!ctx?.has || busy || editing}
          title="Edit the project context text that gets injected into generation"
        >
          <Pencil className="h-3.5 w-3.5" />
          Edit
        </button>
        <button
          className="tt-btn-ghost !px-2.5 !py-1 !text-xs !gap-1.5"
          onClick={copySummary}
          disabled={!ctx?.has || editing}
          title="Copy the project context summary to the clipboard"
        >
          {copied ? (
            <Check className="h-3.5 w-3.5" />
          ) : (
            <Copy className="h-3.5 w-3.5" />
          )}
          {copied ? "Copied" : "Copy"}
        </button>
        <button
          className="tt-btn-ghost !px-2.5 !py-1 !text-xs !gap-1.5"
          onClick={clearContext}
          disabled={!ctx?.has || busy || editing}
          title="Delete the stored project context for this project"
        >
          <Trash2 className="h-3.5 w-3.5" />
          Clear
        </button>
        <button
          className="tt-btn-ghost !px-2.5 !py-1 !text-xs !gap-1.5"
          onClick={regenerate}
          disabled={busy || editing}
          title="Re-extract project context from KB documents using the LLM"
        >
          <Sparkles className="h-3.5 w-3.5" />
          {busy ? "Extracting..." : "Regenerate"}
        </button>
      </div>
      <p
        className="text-xs"
        style={{
          color: ctx?.has ? "var(--tt-success)" : "var(--tt-text-muted)",
        }}
      >
        {statusText}
      </p>
      {ctx?.status === "partial" && ctx.failed_documents.length > 0 && (
        <p className="text-xs text-[var(--tt-warn)]">
          Retry needed for: {ctx.failed_documents.join(", ")}
        </p>
      )}
      {error && (
        <p className="text-xs text-[var(--tt-danger)]">{error}</p>
      )}
      {editing ? (
        <div className="flex flex-col gap-2">
          <textarea
            className="tt-input h-52 resize-y font-mono text-[11px] leading-relaxed"
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            disabled={busy}
            spellCheck={false}
            aria-label="Project context editor"
          />
          <div className="flex items-center gap-2">
            <button
              className="tt-btn-primary !px-2.5 !py-1 !text-xs !gap-1.5"
              onClick={saveEdit}
              disabled={busy}
            >
              <Check className="h-3.5 w-3.5" />
              {busy ? "Saving..." : "Save"}
            </button>
            <button
              className="tt-btn-ghost !px-2.5 !py-1 !text-xs !gap-1.5"
              onClick={() => {
                setEditing(false);
                setError("");
              }}
              disabled={busy}
            >
              <X className="h-3.5 w-3.5" />
              Cancel
            </button>
            <span className="text-xs text-[var(--tt-text-muted)]">
              Saved text is injected verbatim into generation. Clear the box and
              save to revert to the auto-extracted summary.
            </span>
          </div>
        </div>
      ) : (
        showSummary &&
        ctx?.has && (
          <pre className="max-h-52 overflow-auto whitespace-pre-wrap rounded-[10px] border border-[var(--tt-outline)] bg-[var(--tt-surface-base)] p-3 font-mono text-[11px] leading-relaxed text-[var(--tt-text-secondary)]">
            {ctx.summary}
          </pre>
        )
      )}
    </div>
  );
}

function PromptsSection({
  project,
  pushLog,
}: {
  project: string;
  pushLog: (l: "INFO" | "SUCCESS" | "WARN" | "ERROR", t: string) => void;
}) {
  const [scope, setScope] = useState("");
  const [text, setText] = useState("");
  const [loaded, setLoaded] = useState("");
  const [editing, setEditing] = useState(false);
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState("");

  const load = (sc: string) => {
    if (!project) return;
    setBusy(true);
    agent
      .getSystemPrompt(project, sc)
      .then((r) => {
        setText(r.text);
        setLoaded(r.text);
        setEditing(false);
        setStatus("");
      })
      .catch((e) => setStatus(`Could not load prompt: ${(e as Error).message}`))
      .finally(() => setBusy(false));
  };

  // Load the General prompt when the dialog opens for this project.
  useEffect(() => {
    if (project) load(scope);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [project]);

  const changeScope = (sc: string) => {
    if (editing && text !== loaded) {
      const ok = window.confirm(
        "You have unsaved changes to the current prompt. Discard them and switch?"
      );
      if (!ok) return;
    }
    setScope(sc);
    load(sc);
  };

  const scopeLabel =
    PROMPT_SCOPES.find((s) => s.value === scope)?.label ?? "General";

  const save = async () => {
    if (!project) return;
    if (!text.trim()) {
      setStatus(
        "The system prompt cannot be empty. Use Reset to default to restore the standard prompt."
      );
      return;
    }
    setBusy(true);
    try {
      const r = await agent.saveSystemPrompt(project, scope, text);
      setLoaded(r.text);
      setText(r.text);
      setEditing(false);
      setStatus(`System prompt saved (${scopeLabel}).`);
      pushLog("SUCCESS", `System prompt saved (${scopeLabel}).`);
    } catch (e) {
      setStatus(`Save failed: ${(e as Error).message}`);
      pushLog("ERROR", `System prompt save failed: ${(e as Error).message}`);
    } finally {
      setBusy(false);
    }
  };

  const reset = async () => {
    if (!project) return;
    const ok = window.confirm(
      `Replace the ${scopeLabel} prompt with the standard default?`
    );
    if (!ok) return;
    setBusy(true);
    try {
      const r = await agent.resetSystemPrompt(project, scope);
      setText(r.text);
      setLoaded(r.text);
      setStatus(`Reset to default (${scopeLabel}).`);
      pushLog("INFO", `System prompt reset to default (${scopeLabel}).`);
    } catch (e) {
      setStatus(`Reset failed: ${(e as Error).message}`);
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="flex flex-col gap-2">
      <div className="flex items-center gap-2">
        <h4 className="text-sm font-bold text-[var(--tt-text-primary)]">System prompt</h4>
        <label className="ml-2 text-sm text-[var(--tt-text-secondary)]">Scope:</label>
        <select
          className="tt-input w-auto min-w-56 cursor-pointer text-sm"
          value={scope}
          disabled={busy || !project}
          onChange={(e) => changeScope(e.target.value)}
        >
          {PROMPT_SCOPES.map((s) => (
            <option key={s.value} value={s.value}>
              {s.label}
            </option>
          ))}
        </select>
        <div className="flex-1" />
        <button
          className="tt-btn-ghost !px-3 !py-1.5 text-xs"
          title="Show prompt (read-only)"
          onClick={() => setEditing(false)}
        >
          View
        </button>
        <button
          className="tt-btn-ghost !px-3 !py-1.5 text-xs"
          title="Edit the system prompt"
          disabled={!project}
          onClick={() => setEditing(true)}
        >
          Edit
        </button>
      </div>
      <textarea
        className="tt-input min-h-40 resize-y font-mono text-xs"
        placeholder="System prompt (extends the canonical strict TC contract)..."
        value={text}
        readOnly={!editing}
        onChange={(e) => setText(e.target.value)}
      />
      {status && (
        <p className="text-xs text-muted-foreground">{status}</p>
      )}
      <div className="flex items-center justify-end gap-2">
        <button
          className="tt-btn-ghost !px-3 !py-1.5 text-xs"
          disabled={!editing || busy}
          onClick={reset}
        >
          Reset to default
        </button>
        <button
          className="tt-btn-primary !px-4 !py-1.5 text-sm"
          disabled={!editing || busy}
          onClick={save}
        >
          Save prompt
        </button>
      </div>
    </section>
  );
}
