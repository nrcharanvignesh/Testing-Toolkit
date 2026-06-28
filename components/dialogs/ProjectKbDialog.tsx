"use client";

import { useEffect, useRef, useState } from "react";
import { Upload, FileText, RefreshCw } from "lucide-react";
import { Modal } from "@/components/ui/modal";
import { agent, type KbStatus } from "@/lib/agent-client";
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
  color = "#5ba8ff",
}: {
  value: number | null;
  color?: string;
}) {
  const indeterminate = value === null;
  const pct = Math.round((value ?? 0) * 100);
  return (
    <div className="h-1.5 w-full overflow-hidden rounded-full bg-[#2d313c]">
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
      ? "#e5484d"
      : item.status === "done"
        ? "#1aab5c"
        : "#5ba8ff";
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
        <FileText className="h-3.5 w-3.5 shrink-0 text-[#5ba8ff]" />
        <span className="truncate text-[#bfc4cc]" title={item.name}>
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
  const { currentProject, displayName, pushLog } = useAppState();
  const projectLabel = currentProject ? displayName(currentProject) : "";

  return (
    <Modal
      open
      onClose={onClose}
      title={`Knowledge base${projectLabel ? ` - ${projectLabel}` : ""}`}
      width={780}
      footer={
        <button className="tt-btn-ghost" onClick={onClose}>
          Close
        </button>
      }
    >
      <div className="flex flex-col gap-5">
        <h3 className="text-sm font-bold text-[#edf0f5]">
          Knowledge base{projectLabel ? ` - ${projectLabel}` : ""}
        </h3>
        <DocumentsSection project={currentProject} pushLog={pushLog} />
        <TemplatesSection project={currentProject} pushLog={pushLog} />
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
  const [status, setStatus] = useState<KbStatus | null>(null);
  const [busy, setBusy] = useState(false);
  const [indexing, setIndexing] = useState(false);
  const [indexProgress, setIndexProgress] = useState("");
  const [indexPct, setIndexPct] = useState<number | null>(null);
  const [uploads, setUploads] = useState<UploadItem[]>([]);
  const [selectedDocs, setSelectedDocs] = useState<Set<string>>(new Set());
  const fileRef = useRef<HTMLInputElement>(null);

  const refresh = () => {
    if (!project) return;
    agent
      .kbStatus(project)
      .then(setStatus)
      .catch(() => setStatus(null));
  };

  useEffect(refresh, [project]);

  const docs = status?.documents ?? [];

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
    let okCount = 0;
    for (const name of names) {
      try {
        await agent.deleteKbDocument(project, name);
        okCount += 1;
      } catch (e) {
        const msg = (e as Error).message || "";
        // A generic 404 ("Not Found") means the running agent predates the
        // remove route. Stop hammering 70+ doomed requests and tell the user
        // exactly how to fix it (one clear message instead of a wall of errors).
        if (/40[34]/.test(msg) && /not found/i.test(msg)) {
          pushLog(
            "ERROR",
            "Remove failed: your local Testing Toolkit agent is out of date " +
              "and doesn't support removing KB documents yet. Update/restart " +
              "the agent (pull the latest build, then relaunch) and try again."
          );
          setBusy(false);
          return;
        }
        pushLog("ERROR", `Could not remove ${name}: ${msg}`);
      }
    }
    setSelectedDocs(new Set());
    pushLog(
      okCount ? "SUCCESS" : "WARN",
      `Removed ${okCount}/${names.length} KB document(s). Rebuild the index to apply.`
    );
    refresh();
    setBusy(false);
  };

  const upload = async (files: FileList | null) => {
    if (!files || !project || files.length === 0) return;
    setBusy(true);

    // Populate every file name up-front so the user sees the full batch the
    // instant they pick it, then fill in per-file progress as we go.
    const items: UploadItem[] = Array.from(files).map((f, i) => ({
      id: `${Date.now()}-${i}-${f.name}`,
      name: f.name,
      size: f.size,
      progress: 0,
      status: "queued",
    }));
    setUploads(items);

    const patch = (id: string, p: Partial<UploadItem>) =>
      setUploads((prev) =>
        prev.map((u) => (u.id === id ? { ...u, ...p } : u))
      );

    let okCount = 0;
    const fileArr = Array.from(files);
    for (let i = 0; i < fileArr.length; i++) {
      const f = fileArr[i];
      const id = items[i].id;
      patch(id, { status: "uploading", progress: 0 });
      try {
        await agent.kbUploadProgress(project, f, (frac) => {
          if (frac === null) return; // indeterminate — leave bar animating
          // Cap the transfer bar at 99%; flip to "processing" on completion.
          patch(id, {
            progress: Math.min(0.99, frac),
            status: frac >= 1 ? "processing" : "uploading",
          });
        });
        patch(id, { status: "done", progress: 1 });
        okCount += 1;
        pushLog("SUCCESS", `Uploaded ${f.name} to KB.`);
      } catch (e) {
        patch(id, { status: "error", error: (e as Error).message });
        pushLog("ERROR", `Upload failed for ${f.name}: ${(e as Error).message}`);
      }
    }

    refresh();
    setBusy(false);

    // Auto-dismiss the progress list shortly after a fully successful batch;
    // keep it on screen if anything failed so the user can read the error.
    if (okCount === fileArr.length) {
      setTimeout(() => setUploads([]), 2500);
    }
  };

  const reindex = async () => {
    if (!project) return;
    setIndexing(true);
    setIndexProgress("Starting...");
    setIndexPct(null);
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
      pushLog("SUCCESS", `Indexed ${r.n_documents} doc(s), ${r.n_chunks} chunk(s).`);
      refresh();
    } catch (e) {
      setIndexProgress("");
      setIndexPct(null);
      pushLog("ERROR", `Index failed: ${(e as Error).message}`);
    } finally {
      setIndexing(false);
    }
  };

  return (
    <section className="flex flex-col gap-2">
      <div className="flex items-center gap-2">
        <h4 className="mr-auto text-sm font-bold text-[#edf0f5]">Documents</h4>
        <button
          className="tt-btn-primary !px-3 !py-1.5 text-xs"
          disabled={busy || !project}
          onClick={() => fileRef.current?.click()}
        >
          <Upload className="h-3.5 w-3.5" /> Add files...
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
          disabled
          title="Opening the KB folder requires the desktop app"
        >
          Open KB folder
        </button>
        <button
          className="tt-btn-ghost !px-3 !py-1.5 text-xs"
          disabled={indexing || !project}
          onClick={reindex}
        >
          <RefreshCw className={`h-3.5 w-3.5 ${indexing ? "animate-spin" : ""}`} />{" "}
          Rebuild index
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
          <label className="flex cursor-pointer items-center gap-2 text-xs text-[#bfc4cc]">
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
        className="max-h-52 overflow-auto rounded-lg border border-[#2d313c] bg-[#13161d] p-1 focus:outline-none focus-visible:ring-1 focus-visible:ring-[#5ba8ff]"
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
            No documents uploaded yet.
          </p>
        ) : (
          docs.map((d) => {
            const isSel = selectedDocs.has(d);
            return (
              <label
                key={d}
                className="flex w-full cursor-pointer items-center gap-2 rounded-md px-2 py-1 text-left text-sm hover:bg-[#1a1d26]"
                style={{
                  background: isSel ? "#16466e" : "transparent",
                  color: isSel ? "#ffffff" : "#bfc4cc",
                }}
              >
                <input
                  type="checkbox"
                  className="tt-check"
                  checked={isSel}
                  onChange={(e) => toggleDoc(d, e.target.checked)}
                />
                <FileText className="h-3.5 w-3.5 shrink-0 text-[#5ba8ff]" />
                <span className="truncate">{d}</span>
              </label>
            );
          })
        )}
      </div>

      {uploads.length > 0 && (
        <div className="flex flex-col gap-1.5 rounded-lg border border-[#2d313c] bg-[#13161d] p-2">
          <div className="flex items-center justify-between px-0.5">
            <span className="text-xs font-semibold text-[#bfc4cc]">
              Uploading {uploads.filter((u) => u.status === "done").length}/
              {uploads.length} file(s)
            </span>
          </div>
          {uploads.map((u) => (
            <UploadRow key={u.id} item={u} />
          ))}
        </div>
      )}

      {indexing && (
        <div className="flex flex-col gap-1">
          <div className="flex items-center justify-between font-mono text-xs text-[#d69e2e]">
            <span>Rebuilding index</span>
            <span>{indexProgress}</span>
          </div>
          <ProgressBar value={indexPct} color="#d69e2e" />
        </div>
      )}

      {status && !indexing && (
        <p className="text-xs leading-relaxed text-[#1aab5c]">
          {status.indexed
            ? `Indexing ${status.n_documents ?? status.documents.length} document(s), ${
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
  const [phase, setPhase] = useState("Implementation");
  return (
    <section className="flex flex-col gap-2">
      <h4 className="text-sm font-bold text-[#edf0f5]">
        Test script templates (per phase)
      </h4>
      <div className="flex items-center gap-2">
        <label className="text-sm text-[#bfc4cc]">Phase:</label>
        <select
          className="tt-input w-44 cursor-pointer text-sm"
          value={phase}
          onChange={(e) => setPhase(e.target.value)}
        >
          {["Implementation", "SIT", "UAT"].map((p) => (
            <option key={p}>{p}</option>
          ))}
        </select>
        <button
          className="tt-btn-primary !px-3 !py-1.5 text-xs"
          disabled={!project}
          onClick={() => pushLog("INFO", `${phase} template upload (xlsx).`)}
        >
          <Upload className="h-3.5 w-3.5" /> Upload template...
        </button>
        <button className="tt-btn-ghost !px-3 !py-1.5 text-xs" disabled>
          Open
        </button>
        <button className="tt-btn-ghost !px-3 !py-1.5 text-xs" disabled>
          Remove
        </button>
      </div>
      <p className="text-xs text-muted-foreground">
        No template uploaded for this phase. Generation falls back to the standard
        reviewer spreadsheet only.
      </p>
    </section>
  );
}

const PROMPT_SCOPES: { value: string; label: string }[] = [
  { value: "", label: "General (manual mode / default)" },
  { value: "implementation", label: "Implementation phase" },
  { value: "sit", label: "SIT phase" },
  { value: "uat", label: "UAT phase" },
];

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
        <h4 className="text-sm font-bold text-[#edf0f5]">System prompt</h4>
        <label className="ml-2 text-sm text-[#bfc4cc]">Scope:</label>
        <select
          className="tt-input w-56 cursor-pointer text-sm"
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
