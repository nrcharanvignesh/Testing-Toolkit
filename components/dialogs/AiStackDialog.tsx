"use client";

import { useState } from "react";
import { Modal } from "@/components/ui/modal";
import { useAppState } from "@/lib/app-state";
import {
  Brain,
  Database,
  FileSearch,
  Layers,
  Network,
  FlaskConical,
  Cpu,
  CheckCircle2,
  AlertCircle,
  ExternalLink,
} from "lucide-react";

// ---------------------------------------------------------------------------
// AI Stack layer definitions — sourced from the LinkedIn diagram + GenAI doc
// ---------------------------------------------------------------------------
interface StackTool {
  name: string;
  active?: boolean; // this is what Testing Toolkit uses
  url?: string;
}

interface StackLayer {
  id: string;
  label: string;
  sublabel: string;
  color: string;       // left accent + icon color
  bgColor: string;     // card background tint
  icon: typeof Brain;
  ttUsing: string;     // what the Testing Toolkit uses at this layer
  ttDetail: string;    // 1-line explanation
  statusKey: "llm" | "embed" | "vector" | "extract" | "access" | "framework" | "eval";
  tools: StackTool[];
}

const LAYERS: StackLayer[] = [
  {
    id: "llm",
    label: "LLMs",
    sublabel: "Language Model Layer",
    color: "#6d8fb5",
    bgColor: "rgba(109,143,181,0.06)",
    icon: Brain,
    ttUsing: "bedrock.anthropic.claude-opus-4-6 (primary) · sonnet-4-6 (fast)",
    ttDetail: "Tier-routed via core/model_router: Opus for TC generation, coverage verification, and decomposition; Sonnet for chat, navigation, and extraction; Haiku as the fast/fallback tier. All served via the GenAI LiteLLM proxy.",
    statusKey: "llm",
    tools: [
      { name: "bedrock.anthropic.claude-opus-4-6", active: true },
      { name: "bedrock.anthropic.claude-sonnet-4-6", active: true },
      { name: "bedrock.anthropic.claude-haiku-4-5", active: true },
      { name: "azure.gpt-4o" },
      { name: "Gemini 1.5 Pro" },
      { name: "Qwen 2.5" },
      { name: "LLAMA 4" },
      { name: "deepseek-r1" },
      { name: "Mistral Large" },
      { name: "Cohere Command R+" },
    ],
  },
  {
    id: "vector",
    label: "Vector Database",
    sublabel: "Semantic Search & Retrieval",
    color: "#b89a5e",
    bgColor: "rgba(184,154,94,0.06)",
    icon: Database,
    ttUsing: "LanceDB (local, embedded)",
    ttDetail: "Embedded vector store — zero infrastructure, runs in-process alongside the agent. Stores project KB embeddings for hybrid BM25 + vector search.",
    statusKey: "vector",
    tools: [
      { name: "LanceDB", active: true },
      { name: "Pinecone" },
      { name: "PostgreSQL pgvector" },
      { name: "Milvus" },
      { name: "Qdrant" },
      { name: "OpenSearch" },
      { name: "Weaviate" },
      { name: "Chroma" },
      { name: "Cassandra" },
    ],
  },
  {
    id: "embed",
    label: "Text Embeddings",
    sublabel: "Semantic Representation",
    color: "#5f9e94",
    bgColor: "rgba(95,158,148,0.06)",
    icon: Network,
    ttUsing: "azure.text-embedding-3-small",
    ttDetail: "1536-dim embeddings via the GenAI proxy /embeddings endpoint. Dimensions configurable (default 512 for speed). Used for KB indexing and similarity search.",
    statusKey: "embed",
    tools: [
      { name: "azure.text-embedding-3-small", active: true },
      { name: "azure.text-embedding-3-large" },
      { name: "nomic-embed-text" },
      { name: "SBERT" },
      { name: "cohere embed-v3" },
      { name: "OpenAI ada-002" },
      { name: "Voyage AI" },
      { name: "Google textembedding" },
    ],
  },
  {
    id: "extract",
    label: "Data Extraction",
    sublabel: "Document & Content Parsing",
    color: "#6a9d81",
    bgColor: "rgba(106,157,129,0.06)",
    icon: FileSearch,
    ttUsing: "PyMuPDF + Vision OCR + ffmpeg",
    ttDetail: "PDFs rasterized with PyMuPDF, then passed to the /chat/completions vision endpoint for OCR. Audio/video tracks extracted via ffmpeg, transcribed via /audio/transcriptions.",
    statusKey: "extract",
    tools: [
      { name: "PyMuPDF (PDF rasterize)", active: true },
      { name: "Vision OCR (/chat/completions)", active: true },
      { name: "ffmpeg (audio extract)", active: true },
      { name: "Whisper (/audio/transcriptions)", active: true },
      { name: "Docling" },
      { name: "Crawl4AI" },
      { name: "Firecrawl" },
      { name: "LlamaParse v2" },
      { name: "MegaParser" },
      { name: "ScrapeGraphAI" },
    ],
  },
  {
    id: "access",
    label: "Open LLM Access",
    sublabel: "Model Gateway & Routing",
    color: "#b56b6b",
    bgColor: "rgba(181,107,107,0.06)",
    icon: Cpu,
    ttUsing: "LiteLLM Proxy (GenAI Gateway)",
    ttDetail: "Organizational LiteLLM proxy at the configured base URL — serves OpenAI-format /chat/completions, /embeddings, /rerank, /audio/transcriptions, and /images/generations. All 100+ models via one key.",
    statusKey: "access",
    tools: [
      { name: "LiteLLM Proxy (GenAI)", active: true },
      { name: "Azure OpenAI", active: true },
      { name: "AWS Bedrock", active: true },
      { name: "Hugging Face" },
      { name: "Groq" },
      { name: "together.ai" },
      { name: "Ollama (local)" },
    ],
  },
  {
    id: "framework",
    label: "Framework",
    sublabel: "Orchestration & Pipelines",
    color: "#7f88a3",
    bgColor: "rgba(127,136,163,0.06)",
    icon: Layers,
    ttUsing: "Custom Python agent (FastAPI)",
    ttDetail: "Bespoke FastAPI agent with 76+ routes — purpose-built for QA workflows. Handles TC generation, KB retrieval, RLM pipelines, E2E orchestration, and ADO/JIRA write-back. No general-purpose chain overhead.",
    statusKey: "framework",
    tools: [
      { name: "Custom FastAPI Agent", active: true },
      { name: "LangChain" },
      { name: "LlamaIndex" },
      { name: "haystack" },
      { name: "txtai" },
    ],
  },
  {
    id: "eval",
    label: "Evaluation",
    sublabel: "Quality & Coverage Scoring",
    color: "#3d8f66",
    bgColor: "rgba(61,143,102,0.06)",
    icon: FlaskConical,
    ttUsing: "Built-in QA quality scorer + coverage engine",
    ttDetail: "LLM-as-judge quality scorer (0-100 per TC), traceability matrix (WI -> TC coverage %), diff engine for regeneration deltas, E2E pass/fail/skip rate tracking. All native — no external eval infra.",
    statusKey: "eval",
    tools: [
      { name: "Native quality scorer (LLM-as-judge)", active: true },
      { name: "Coverage traceability engine", active: true },
      { name: "E2E pass-rate tracker", active: true },
      { name: "Giskard" },
      { name: "ragas" },
      { name: "trulens" },
    ],
  },
];

// ---------------------------------------------------------------------------
// Status helper — derives per-layer connected state from app settings
// ---------------------------------------------------------------------------
type LayerStatus = "connected" | "partial" | "not-configured";

function useLayerStatuses(settings: ReturnType<typeof useAppState>["settings"]) {
  const hasLlm = !!(settings?.base_url && settings.has_api_key);
  const hasModel = !!(settings?.model);
  const hasEmbedModel = true; // always configured via the same base URL

  const map: Record<string, LayerStatus> = {
    llm:       hasLlm && hasModel ? "connected" : hasLlm ? "partial" : "not-configured",
    embed:     hasLlm ? "connected" : "not-configured",
    vector:    "connected",   // LanceDB is always local
    extract:   "connected",   // PyMuPDF / ffmpeg always bundled
    access:    hasLlm ? "connected" : "not-configured",
    framework: "connected",   // agent is running if we can open this dialog
    eval:      "connected",   // native, always available
  };
  return map;
}

function StatusChip({ status }: { status: LayerStatus }) {
  if (status === "connected") {
    return (
      <span className="flex items-center gap-1 text-[10px] font-semibold text-[var(--tt-success)]">
        <CheckCircle2 className="h-3 w-3" />
        Connected
      </span>
    );
  }
  if (status === "partial") {
    return (
      <span className="flex items-center gap-1 text-[10px] font-semibold text-[var(--tt-warn)]">
        <AlertCircle className="h-3 w-3" />
        Partial
      </span>
    );
  }
  return (
    <span className="flex items-center gap-1 text-[10px] font-semibold text-[var(--tt-text-muted)]">
      <AlertCircle className="h-3 w-3" />
      Not configured
    </span>
  );
}

// ---------------------------------------------------------------------------
// Single layer card
// ---------------------------------------------------------------------------
function LayerCard({
  layer,
  status,
  index,
  expanded,
  onToggle,
}: {
  layer: StackLayer;
  status: LayerStatus;
  index: number;
  expanded: boolean;
  onToggle: () => void;
}) {
  const Icon = layer.icon;
  const activeTools = layer.tools.filter((t) => t.active);
  const otherTools = layer.tools.filter((t) => !t.active);

  return (
    <div
      className="group relative rounded-xl border transition-all duration-150"
      style={{
        borderColor: expanded ? layer.color : "var(--tt-outline)",
        background: expanded ? layer.bgColor : "var(--tt-surface-container)",
        boxShadow: expanded ? `0 0 0 1px ${layer.color}22` : "none",
      }}
    >
      {/* Numbered left accent bar */}
      <div
        className="absolute inset-y-0 left-0 w-1 rounded-l-xl"
        style={{ background: layer.color }}
        aria-hidden
      />

      {/* Header row — always visible */}
      <button
        className="flex w-full items-start gap-3 px-4 py-3 pl-5 text-left"
        onClick={onToggle}
        aria-expanded={expanded}
      >
        {/* Layer number */}
        <span
          className="mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center rounded-md text-[10px] font-bold text-white"
          style={{ background: layer.color }}
          aria-hidden
        >
          {index + 1}
        </span>

        {/* Name + status */}
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <span className="text-sm font-bold text-[var(--tt-text-primary)]">
              {layer.label}
            </span>
            <span className="hidden text-xs text-[var(--tt-text-muted)] sm:inline">
              {layer.sublabel}
            </span>
            <StatusChip status={status} />
          </div>

          {/* Active using badge — always visible */}
          <div className="mt-1 flex items-center gap-1.5">
            <Icon className="h-3 w-3 shrink-0" style={{ color: layer.color }} />
            <span className="truncate text-xs font-medium text-[var(--tt-text-secondary)]">
              {layer.ttUsing}
            </span>
          </div>
        </div>

        {/* Expand chevron */}
        <span
          className="mt-1 shrink-0 text-[var(--tt-text-muted)] transition-transform duration-150"
          style={{ transform: expanded ? "rotate(90deg)" : "rotate(0deg)" }}
          aria-hidden
        >
          ›
        </span>
      </button>

      {/* Expanded body */}
      {expanded && (
        <div className="border-t border-[var(--tt-outline-soft)] px-5 pb-4 pt-3 pl-[52px]">
          {/* Description */}
          <p className="mb-3 text-xs leading-relaxed text-[var(--tt-text-secondary)]">
            {layer.ttDetail}
          </p>

          {/* Tool chips */}
          <div className="flex flex-wrap gap-1.5">
            {/* Active tools first */}
            {activeTools.map((t) => (
              <span
                key={t.name}
                className="inline-flex items-center gap-1 rounded-full border px-2.5 py-0.5 text-[11px] font-semibold"
                style={{
                  borderColor: layer.color,
                  color: layer.color,
                  background: layer.bgColor,
                }}
                title="Used by Testing Toolkit"
              >
                <CheckCircle2 className="h-2.5 w-2.5" />
                {t.name}
              </span>
            ))}
            {/* Separator if both groups non-empty */}
            {activeTools.length > 0 && otherTools.length > 0 && (
              <div className="my-0.5 h-5 w-px self-center bg-[var(--tt-outline)]" />
            )}
            {/* Ecosystem alternatives */}
            {otherTools.map((t) => (
              <span
                key={t.name}
                className="tt-badge tt-badge-neutral"
                title="Ecosystem alternative"
              >
                {t.name}
              </span>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Dialog
// ---------------------------------------------------------------------------
export function AiStackDialog({ onClose }: { onClose: () => void }) {
  const { settings, openDialog } = useAppState();
  const statuses = useLayerStatuses(settings);
  const [expandedId, setExpandedId] = useState<string | null>("llm");

  const connectedCount = Object.values(statuses).filter((s) => s === "connected").length;

  const toggle = (id: string) =>
    setExpandedId((prev) => (prev === id ? null : id));

  return (
    <Modal
      open
      onClose={onClose}
      title="AI Stack — Testing Toolkit Architecture"
      width={780}
      footer={
        <>
          <div className="mr-auto flex items-center gap-2 text-xs text-[var(--tt-text-muted)]">
            <span className="tt-badge tt-badge-success">{connectedCount}/7 layers connected</span>
            <span>GenAI LiteLLM Proxy — OpenAI-format gateway</span>
          </div>
          <button
            className="tt-btn-ghost !px-3 !py-1.5 !text-xs"
            onClick={() => { onClose(); openDialog("settings"); }}
          >
            Configure LLM
          </button>
          <button className="tt-btn-ghost" onClick={onClose}>
            Close
          </button>
        </>
      }
    >
      {/* Subtitle */}
      <div className="mb-4 flex items-center justify-between gap-4">
        <p className="text-xs leading-relaxed text-[var(--tt-text-secondary)]">
          A complete view of the AI components powering the Testing Toolkit, mapped
          to the enterprise GenAI stack. Layers marked{" "}
          <span className="font-semibold text-[var(--tt-success)]">Connected</span>{" "}
          are live and operational. Configure the LLM layer in Settings to unlock
          the full stack.
        </p>
        <a
          href="https://hebbkx1anhila5yf.public.blob.vercel-storage.com/image-ALBrfmrfOZrDWeFUqqKHbkCvmSKIO6.png"
          target="_blank"
          rel="noopener noreferrer"
          className="tt-btn-ghost shrink-0 !px-2.5 !py-1 !text-[10px] !gap-1"
          title="View reference diagram"
        >
          <ExternalLink className="h-3 w-3" />
          Stack diagram
        </a>
      </div>

      {/* Layer stack */}
      <div className="flex flex-col gap-2">
        {LAYERS.map((layer, i) => (
          <LayerCard
            key={layer.id}
            layer={layer}
            status={statuses[layer.statusKey] as LayerStatus}
            index={i}
            expanded={expandedId === layer.id}
            onToggle={() => toggle(layer.id)}
          />
        ))}
      </div>

      {/* Footer attribution note */}
      <p className="mt-4 text-[10px] text-[var(--tt-text-faint)]">
        Ecosystem alternatives sourced from the Full AI Stack reference diagram.
        Active tools are those actually deployed and running in the Testing Toolkit agent.
        API contract: GenAI Documentation v1.0.0 (OAS 3.0) — LiteLLM proxy, OpenAI format.
      </p>
    </Modal>
  );
}
