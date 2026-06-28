"use client";

import { useState } from "react";
import { Search } from "lucide-react";
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

  const run = async () => {
    if (!currentProject || !query.trim()) return;
    setBusy(true);
    setStatus("Retrieving...");
    try {
      const res = await agent.kbRetrieve(currentProject, query.trim(), topK);
      setChunks(res);
      setStatus(`${res.length} chunk(s) retrieved.`);
      pushLog("INFO", `Retrieval preview: ${res.length} chunk(s) for "${query.trim()}".`);
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
      title="Retrieval Preview"
      subtitle={
        currentProject
          ? `${displayName(currentProject)} · local, API-free`
          : "Select a project first"
      }
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
        <p className="text-sm leading-relaxed text-muted-foreground">
          Hybrid retrieval: BM25 + dense vectors (bge-small) with Reciprocal Rank
          Fusion and cross-encoder reranking. Falls back to BM25 when dense models
          are absent.
        </p>
        <div className="flex items-center gap-2">
          <div className="relative flex-1">
            <Search className="absolute left-2.5 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
            <input
              className="tt-input !pl-8"
              placeholder="Query the knowledge base..."
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && run()}
            />
          </div>
          <input
            type="number"
            className="tt-input w-20"
            value={topK}
            min={1}
            max={96}
            onChange={(e) => setTopK(parseInt(e.target.value, 10) || 32)}
            title="Top K"
          />
          <button
            className="tt-btn-primary !px-4 !py-1.5 text-sm"
            onClick={run}
            disabled={busy || !query.trim() || !currentProject}
          >
            {busy ? "..." : "Retrieve"}
          </button>
        </div>

        <div className="flex max-h-[50vh] flex-col gap-2 overflow-auto">
          {chunks.length === 0 ? (
            <p className="py-4 text-center text-sm text-muted-foreground">
              No results yet. Enter a query and retrieve.
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
