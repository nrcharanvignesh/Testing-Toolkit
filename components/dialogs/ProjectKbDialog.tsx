"use client";

import { useEffect, useRef, useState } from "react";
import { Upload, FileText, RefreshCw } from "lucide-react";
import { Modal } from "@/components/ui/modal";
import { agent, type KbStatus } from "@/lib/agent-client";
import { useAppState } from "@/lib/app-state";

type Tab = "documents" | "templates" | "prompts";

export function ProjectKbDialog({ onClose }: { onClose: () => void }) {
  const { currentProject, displayName, pushLog } = useAppState();
  const [tab, setTab] = useState<Tab>("documents");

  return (
    <Modal
      open
      onClose={onClose}
      title="Project Knowledge Base"
      subtitle={currentProject ? displayName(currentProject) : undefined}
      width={760}
      footer={
        <button className="tt-btn-ghost" onClick={onClose}>
          Close
        </button>
      }
    >
      <div className="flex flex-col gap-3">
        <div className="flex items-end gap-1 border-b border-[#1e2128]">
          {(["documents", "templates", "prompts"] as Tab[]).map((t) => (
            <button
              key={t}
              className="tt-tab"
              data-active={tab === t}
              onClick={() => setTab(t)}
            >
              {t === "documents" ? "Documents" : t === "templates" ? "Templates" : "System Prompts"}
            </button>
          ))}
        </div>
        {tab === "documents" && (
          <DocumentsTab project={currentProject} pushLog={pushLog} />
        )}
        {tab === "templates" && <TemplatesTab project={currentProject} pushLog={pushLog} />}
        {tab === "prompts" && <PromptsTab />}
      </div>
    </Modal>
  );
}

function DocumentsTab({
  project,
  pushLog,
}: {
  project: string;
  pushLog: (l: "INFO" | "SUCCESS" | "WARN" | "ERROR", t: string) => void;
}) {
  const [status, setStatus] = useState<KbStatus | null>(null);
  const [busy, setBusy] = useState(false);
  const [indexing, setIndexing] = useState(false);
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
    <div className="flex flex-col gap-3">
      <p className="text-sm leading-relaxed text-muted-foreground">
        Drop requirement documents (.md .txt .pdf .docx .xlsx .pptx .html .csv
        .json). Scanned PDFs and images are OCR&apos;d automatically. The index
        rebuilds when documents change.
      </p>
      <div className="flex items-center gap-2">
        <button
          className="tt-btn-primary !px-4 !py-1.5 text-sm"
          disabled={busy || !project}
          onClick={() => fileRef.current?.click()}
        >
          <Upload className="h-4 w-4" /> Upload documents
        </button>
        <button
          className="tt-btn-ghost !px-4 !py-1.5 text-sm"
          disabled={indexing || !project}
          onClick={reindex}
        >
          <RefreshCw className={`h-4 w-4 ${indexing ? "animate-spin" : ""}`} />{" "}
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
      <div className="max-h-64 overflow-auto rounded-lg border border-[#2d313c] bg-[#13161d] p-2">
        {!status || status.documents.length === 0 ? (
          <p className="px-2 py-1.5 text-sm text-muted-foreground">
            No documents uploaded yet.
          </p>
        ) : (
          status.documents.map((d) => (
            <div key={d} className="flex items-center gap-2 px-2 py-1 text-sm">
              <FileText className="h-3.5 w-3.5 text-[#5ba8ff]" />
              <span className="truncate text-[#bfc4cc]">{d}</span>
            </div>
          ))
        )}
      </div>
      {status && (
        <p className="text-xs text-muted-foreground">
          {status.indexed
            ? `Indexed: ${status.n_documents ?? status.documents.length} doc(s), ${status.n_chunks ?? "?"} chunk(s).`
            : "Not yet indexed."}
        </p>
      )}
    </div>
  );
}

function TemplatesTab({
  project,
  pushLog,
}: {
  project: string;
  pushLog: (l: "INFO" | "SUCCESS" | "WARN" | "ERROR", t: string) => void;
}) {
  const phases = ["Implementation", "SIT", "UAT"] as const;
  return (
    <div className="flex flex-col gap-3">
      <p className="text-sm leading-relaxed text-muted-foreground">
        Upload a client&apos;s Excel test-script template per phase. On upload it
        is analyzed by the LLM (header row, column purposes) and saved as a
        deterministic spec used to render generated test cases into a copy of the
        original template.
      </p>
      {phases.map((p) => (
        <div
          key={p}
          className="flex items-center justify-between rounded-lg border border-[#2d313c] bg-[#1a1d26] px-3 py-2.5"
        >
          <span className="text-sm font-medium text-[#edf0f5]">{p} template</span>
          <button
            className="tt-btn-ghost !px-3 !py-1.5 text-xs"
            disabled={!project}
            onClick={() => pushLog("INFO", `${p} template upload (xlsx).`)}
          >
            <Upload className="h-3.5 w-3.5" /> Upload .xlsx
          </button>
        </div>
      ))}
    </div>
  );
}

function PromptsTab() {
  const [phase, setPhase] = useState("Implementation");
  const [text, setText] = useState("");
  return (
    <div className="flex flex-col gap-3">
      <p className="text-sm text-muted-foreground">
        Each phase has its own editable system prompt that extends the canonical
        strict TC contract.
      </p>
      <select
        className="tt-input cursor-pointer"
        value={phase}
        onChange={(e) => setPhase(e.target.value)}
      >
        {["Implementation", "SIT", "UAT"].map((p) => (
          <option key={p}>{p}</option>
        ))}
      </select>
      <textarea
        className="tt-input min-h-52 resize-y font-mono text-xs"
        placeholder={`System prompt for ${phase} phase...`}
        value={text}
        onChange={(e) => setText(e.target.value)}
      />
      <div className="flex justify-end">
        <button className="tt-btn-primary !px-4 !py-1.5 text-sm">Save prompt</button>
      </div>
    </div>
  );
}
