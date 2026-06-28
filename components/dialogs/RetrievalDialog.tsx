"use client";

import { useState } from "react";
import { Modal } from "@/components/ui/modal";
import { agent, type RetrievedChunk } from "@/lib/agent-client";
import { useAppState } from "@/lib/app-state";

export function RetrievalDialog({ onClose }: { onClose: () => void }) {
  const { currentProject, displayName, pushLog } = useAppState();
  const [query, setQuery] = useState("");
  const [topK, setTopK] = useState(32);
  const [busy, setBusy] = useState(false);
  const [chunks, setChunks] = useState<RetrievedChunk[]>([]);
  const [status, setStatus] = useState("");

  const projectLabel = currentProject ? displayName(currentProject) : "";

  const run = async () => {
    if (!currentProject || !query.trim()) return;
    setBusy(true);
    setStatus("Retrieving...");
    try {
      const res = await agent.kbRetrieve(currentProject, query.trim(), topK);
      setChunks(res);
      setStatus(`${res.length} chunk(s) retrieved.`);
      pushLog("INFO", `Retrieval preview: ${res.length} chunk(s).`);
    } catch (e) {
      setStatus(`Retrieval failed: ${(e as Error).message}`);
      pushLog("ERROR", `Retrieval failed: ${(e as Error).message}`);
    } finally {
      setBusy(false);
    }
  };

  return (
    <Modal
      open
      onClose={onClose}
      title={`Retrieval preview (test)${projectLabel ? ` - ${projectLabel}` : ""}`}
      width={780}
      footer={
        <>
          {status && (
            <span className="mr-auto text-xs text-muted-foreground">{status}</span>
          )}
          <button className="tt-btn-ghost" onClick={onClose}>
            Close
          </button>
        </>
      }
    >
      <div className="flex flex-col gap-3">
        <h3 className="text-sm font-bold text-[#edf0f5]">
          Retrieval preview{projectLabel ? ` - ${projectLabel}` : ""}
        </h3>
        <p className="text-sm leading-relaxed text-[#bfc4cc]">
          Paste a user story (or any work-item text). This shows the KB chunks the
          local retriever would supply for it - ranked, scored, and by source -
          with NO LLM API call and NO ADO changes. Use it to sanity-check
          retrieval before generating.
        </p>

        <div className="flex flex-col gap-1.5">
          <label className="text-sm text-[#bfc4cc]">Story / work-item text</label>
          <textarea
            className="tt-input min-h-32 resize-y"
            placeholder="Paste the user story title and description here..."
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
        </div>

        <div className="flex items-center gap-3">
          <label className="text-sm text-[#bfc4cc]">Chunks to retrieve:</label>
          <input
            type="number"
            className="tt-input w-20"
            value={topK}
            min={1}
            max={96}
            onChange={(e) => setTopK(parseInt(e.target.value, 10) || 32)}
          />
          <button
            className="tt-btn-primary !px-4 !py-1.5 text-sm"
            onClick={run}
            disabled={busy || !query.trim() || !currentProject}
          >
            {busy ? "Retrieving..." : "Preview retrieval"}
          </button>
        </div>

        <div className="flex max-h-[44vh] flex-col gap-2 overflow-auto rounded-lg border border-[#2d313c] bg-[#0d1017] p-2">
          {chunks.length === 0 ? (
            <p className="py-4 text-center text-sm text-muted-foreground">
              No results yet. Paste text and preview retrieval.
            </p>
          ) : (
            chunks.map((c, i) => (
              <div
                key={c.chunk_id || i}
                className="rounded-lg border border-[#2d313c] bg-[#1a1d26] p-3"
              >
                <div className="mb-1 flex items-center justify-between text-xs">
                  <span className="font-semibold text-[#5ba8ff]">
                    {i + 1}. {c.title || c.doc}
                  </span>
                  <span className="text-muted-foreground">
                    score {c.score.toFixed(3)}
                  </span>
                </div>
                <p className="line-clamp-4 text-sm leading-relaxed text-[#bfc4cc]">
                  {c.text}
                </p>
              </div>
            ))
          )}
        </div>
      </div>
    </Modal>
  );
}
