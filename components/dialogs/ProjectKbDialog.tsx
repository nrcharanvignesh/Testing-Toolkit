"use client";

import { useEffect, useRef, useState } from "react";
import { Upload, FileText, RefreshCw } from "lucide-react";
import { Modal } from "@/components/ui/modal";
import { agent, type KbStatus } from "@/lib/agent-client";
import { useAppState } from "@/lib/app-state";

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
        <PromptsSection />
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
  const [selectedDoc, setSelectedDoc] = useState<string>("");
  const fileRef = useRef<HTMLInputElement>(null);

  const refresh = () => {
    if (!project) return;
    agent
      .kbStatus(project)
      .then(setStatus)
      .catch(() => setStatus(null));
  };

  useEffect(refresh, [project]);

  const upload = async (files: FileList | null) => {
    if (!files || !project) return;
    setBusy(true);
    try {
      for (const f of Array.from(files)) {
        await agent.kbUpload(project, f);
        pushLog("SUCCESS", `Uploaded ${f.name} to KB.`);
      }
      refresh();
    } catch (e) {
      pushLog("ERROR", `Upload failed: ${(e as Error).message}`);
    } finally {
      setBusy(false);
    }
  };

  const reindex = async () => {
    if (!project) return;
    setIndexing(true);
    pushLog("INFO", "Rebuilding KB index...");
    try {
      const r = await agent.kbIndex(project);
      pushLog("SUCCESS", `Indexed ${r.n_documents} doc(s), ${r.n_chunks} chunk(s).`);
      refresh();
    } catch (e) {
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
          disabled={!selectedDoc}
          title="Removing individual KB documents is done in the desktop app"
        >
          Remove selected
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

      <div className="max-h-52 overflow-auto rounded-lg border border-[#2d313c] bg-[#13161d] p-1">
        {!status || status.documents.length === 0 ? (
          <p className="px-2 py-1.5 text-sm text-muted-foreground">
            No documents uploaded yet.
          </p>
        ) : (
          status.documents.map((d) => {
            const isSel = d === selectedDoc;
            return (
              <button
                key={d}
                onClick={() => setSelectedDoc(d)}
                className="flex w-full items-center gap-2 rounded-md px-2 py-1 text-left text-sm"
                style={{
                  background: isSel ? "#16466e" : "transparent",
                  color: isSel ? "#ffffff" : "#bfc4cc",
                }}
              >
                <FileText className="h-3.5 w-3.5 shrink-0 text-[#5ba8ff]" />
                <span className="truncate">{d}</span>
              </button>
            );
          })
        )}
      </div>

      {status && (
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

function PromptsSection() {
  const [text, setText] = useState("");
  const [editing, setEditing] = useState(false);
  return (
    <section className="flex flex-col gap-2">
      <div className="flex items-center gap-2">
        <h4 className="text-sm font-bold text-[#edf0f5]">System prompt</h4>
        <label className="ml-2 text-sm text-[#bfc4cc]">Scope:</label>
        <select className="tt-input w-56 cursor-pointer text-sm" defaultValue="general">
          <option value="general">General (manual mode / default)</option>
          <option value="implementation">Implementation</option>
          <option value="sit">SIT</option>
          <option value="uat">UAT</option>
        </select>
        <div className="flex-1" />
        <button
          className="tt-btn-ghost !px-3 !py-1.5 text-xs"
          onClick={() => setEditing(false)}
        >
          View
        </button>
        <button
          className="tt-btn-ghost !px-3 !py-1.5 text-xs"
          onClick={() => setEditing(true)}
        >
          Edit
        </button>
      </div>
      <textarea
        className="tt-input min-h-40 resize-y font-mono text-xs"
        placeholder="System prompt (extends the canonical strict TC contract)..."
        value={text}
        disabled={!editing}
        onChange={(e) => setText(e.target.value)}
      />
      <div className="flex items-center justify-end gap-2">
        <button className="tt-btn-ghost !px-3 !py-1.5 text-xs" disabled={!editing}>
          Reset to default
        </button>
        <button
          className="tt-btn-primary !px-4 !py-1.5 text-sm"
          disabled={!editing}
        >
          Save prompt
        </button>
      </div>
    </section>
  );
}
