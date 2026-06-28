/**
 * agent-client.ts
 * Typed client for the local compute agent at localhost:7842.
 *
 * This mirrors the REAL agent route contract (agent-bundle/src/agent/routes/*)
 * so the web GUI drives the exact same Python backend the desktop app uses.
 * Long operations (generate / push / defect upload / package) run as background
 * jobs on the agent; the browser starts a job, gets a {job_id}, and polls
 * /jobs/{id} for live logs + progress — exactly like the desktop worker + log
 * panel.
 */

const AGENT_URL = "http://127.0.0.1:7842";

// ---------------------------------------------------------------------------
// Test-case generation phases (testgen/tc_types.py)
// ---------------------------------------------------------------------------
export const TC_TYPES = ["implementation", "sit", "uat"] as const;
export type TcType = (typeof TC_TYPES)[number];

export const TC_DISPLAY_NAME: Record<TcType, string> = {
  implementation: "Implementation",
  sit: "SIT",
  uat: "UAT",
};

export const TC_BUTTON_LABEL: Record<TcType, string> = {
  implementation: "Implementation",
  sit: "SIT",
  uat: "UAT",
};

// ---------------------------------------------------------------------------
// Core responses
// ---------------------------------------------------------------------------
/** Best-effort hardware summary from the agent (>= 2.2.0). */
export interface HealthHardware {
  arch?: string;
  chip?: string;
  is_arm?: boolean;
  is_unified_memory?: boolean;
}

/** What the loaded ONNX models actually bound to (>= 2.3.0). */
export interface ModelRuntimeEntry {
  model: string;
  providers: string[] | null;
  accelerated: boolean;
  active_provider: string | null;
}

/** Compact feature map served by /capabilities and embedded in /health
 *  (agent >= 2.3.0). Any field may be null when the probe could not run. */
export interface AgentCapabilities {
  dense_retrieval: boolean | null;
  reranker: boolean | null;
  model_bundle: boolean | null;
  embedder_model_files: boolean | null;
  reranker_model_files: boolean | null;
  gpu_capable: boolean | null;
  model_runtime: {
    models?: Record<string, ModelRuntimeEntry>;
    accelerated?: boolean;
    active_provider?: string | null;
  } | null;
  ocr: boolean | null;
  audio_transcription: boolean | null;
  video: boolean | null;
  incremental_hash_indexing: boolean | null;
  updates_configured: boolean | null;
}

export interface HealthResponse {
  status: string;
  version: string;
  user: string;
  machine: string;
  models_loaded: boolean;
  tls_mode?: string;
  /** Present on agent >= 2.2.0. */
  hardware?: HealthHardware;
  /** Present on agent >= 2.3.0. */
  capabilities?: AgentCapabilities;
}

/** One diagnostic check from /doctor (agent >= 2.3.0). */
export interface DoctorCheck {
  id: string;
  label: string;
  status: "pass" | "warn" | "fail";
  detail: string;
  fix: string;
}

export interface DoctorReport {
  status: "pass" | "warn" | "fail";
  checks: DoctorCheck[];
}

export interface GpuMetrics {
  name: string;
  in_use: boolean;
  util_percent: number | null;
  mem_used_mb: number | null;
  mem_total_mb: number | null;
  /** True on a unified-memory SoC (e.g. Apple Silicon) where the accelerator
   *  shares system RAM, so mem_total_mb is the shared pool, not separate VRAM.
   *  Optional: absent on older agents that predate unified-memory reporting. */
  unified_memory?: boolean;
  /** The execution provider a model actually bound to, e.g.
   *  "CoreMLExecutionProvider" / "CUDAExecutionProvider". Null until a model
   *  loads (or if models run on CPU). Present on agent >= 2.3.0. */
  ep?: string | null;
  /** True once a loaded model is confirmed running off-CPU (>= 2.3.0). */
  accelerated?: boolean;
}

/** Live system resource usage from the agent's `/metrics` endpoint. Any field
 * may be null when the host can't report it. */
export interface MetricsResponse {
  /** The app's own CPU usage as a % of total machine capacity. */
  cpu_percent: number | null;
  /** The app's own resident memory (RAM) in MB. */
  proc_mem_mb: number | null;
  /** Actual disk space the app's workspace directory occupies, in MB. */
  app_data_mb: number | null;
  /** System RAM context (whole machine), kept for tooltips / older agents. */
  ram_used_mb: number | null;
  ram_total_mb: number | null;
  ram_percent: number | null;
  /** Whole-drive context, kept for tooltips / older agents. */
  disk_used_mb: number | null;
  disk_total_mb: number | null;
  disk_percent: number | null;
  gpu: GpuMetrics | null;
}

/**
 * Live install/reinstall progress, served by the installer's temporary
 * "beacon" HTTP server on the agent port (127.0.0.1:7842) BEFORE the real
 * agent is up. The smart installer writes its download progress and the
 * offline `install.py` writes the install/clean/copy/start phases to the same
 * shared progress file, which the beacon serves at `/install/progress`. Absent
 * (null) when no install is running.
 */
export interface InstallProgress {
  /** downloading | extracting | overlay | cleaning | installing_deps |
   *  copying | starting | done | error */
  phase: string;
  message: string;
  /** 0-100 overall completion, when known. */
  percent?: number;
  /** Agent version being installed, when known. */
  version?: string;
  /** Epoch ms of this snapshot. */
  ts?: number;
}

export interface SettingsResponse {
  configured: boolean;
  has_api_key: boolean;
  has_pat: boolean;
  organization: string;
  model: string;
  fast_model: string;
  fallback_model: string;
  base_url: string;
  project_prefix: string;
  tls_mode?: string;
  /** Server-persisted: true once the first-run guided tour is done/skipped. */
  tour_completed?: boolean;
}

export interface SaveSettingsPayload {
  organization?: string;
  base_url?: string;
  model?: string;
  fast_model?: string;
  fallback_model?: string;
  project_prefix?: string;
  api_key?: string;
  pat?: string;
}

// ---------------------------------------------------------------------------
// ADO board model (ado/boards.py)
// ---------------------------------------------------------------------------
export interface Board {
  id: string;
  name: string;
  team_id: string;
  team_name: string;
  label: string;
}

export interface BoardColumn {
  id: string;
  name: string;
  column_type: string;
}

export interface WorkItemRow {
  wi_id: number;
  title: string;
  wi_type: string;
  state: string;
  board_column: string;
  board_lane: string;
  assigned_to: string;
  tags: string[];
  iteration_path: string;
  iteration_leaf?: string;
  area_path: string;
}

export interface BoardView {
  columns: BoardColumn[];
  rows: WorkItemRow[];
}

export interface Attachment {
  name: string;
  url: string;
  size: number;
  comment?: string;
  /** Browser-loadable URL that streams the blob via the agent (PAT-authed). */
  downloadUrl: string;
}

export interface WorkItemDetail {
  wi_id: number;
  title: string;
  wi_type: string;
  state: string;
  board_column: string;
  area_path: string;
  iteration_path: string;
  assigned_to: string;
  tags: string[];
  description_html: string;
  acceptance_html: string;
  comments_html: Array<[string, string, string]>; // [author, when, html]
  attachments: Attachment[];
  hyperlinks: Array<[string, string]>; // [label, url]
  related: Array<[string, number, string]>; // [name, wi_id, url]
}

// ---------------------------------------------------------------------------
// KB model (kb/*)
// ---------------------------------------------------------------------------
export interface RetrievedChunk {
  chunk_id: string;
  doc: string;
  title: string;
  text: string;
  score: number;
}

export interface KbStatus {
  project: string;
  documents: string[];
  indexed: boolean;
  n_chunks?: number;
  n_documents?: number;
}

// Per-phase test-script template status (backend kb._template_payload).
export interface TemplateStatus {
  has: boolean;
  name: string;
  describe: string;
}

export interface ArtifactFile {
  name: string;
  path: string;
  kind: string; // "testcases" | "packets"
  size: number;
  modified: number;
}

// ---------------------------------------------------------------------------
// Generation / defects payloads
// ---------------------------------------------------------------------------
export interface GenerationResult {
  payload: Record<string, unknown>;
  n_test_cases: number;
  n_stories: number;
  xlsx_path: string;
  xlsx_name: string;
}

export interface ParsedDefect {
  parent_id: number;
  title: string;
  description: string;
  repro_steps: string;
  severity: string;
  expected_result: string;
  actual_result: string;
  images?: Array<{ filename: string; data_b64: string; mime_type: string }>;
  skip?: boolean;
}

export interface CreatedResult {
  n_ok: number;
  n_failed: number;
  created: Array<{
    title: string;
    parent_id: number;
    created_id?: number;
    created_url?: string;
    ok: boolean;
    error?: string;
  }>;
}

// ---------------------------------------------------------------------------
// Jobs
// ---------------------------------------------------------------------------
export interface JobProgress {
  stage: string;
  current: number;
  total: number;
}

export type JobState = "running" | "done" | "error" | "stopped";

export interface JobSnapshot {
  id: string;
  kind: string;
  state: JobState;
  logs: string[];
  log_count: number;
  progress: JobProgress;
  error: string;
  result: Record<string, unknown>;
}

export interface JobHandlers {
  onLog?: (line: string) => void;
  onProgress?: (p: JobProgress) => void;
  signal?: AbortSignal;
  intervalMs?: number;
}

export type AgentStatus = "connected" | "offline" | "connecting";

// ---------------------------------------------------------------------------
// Low-level fetch helpers
// ---------------------------------------------------------------------------
async function agentFetch<T>(path: string, options?: RequestInit): Promise<T> {
  const res = await fetch(`${AGENT_URL}${path}`, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      ...options?.headers,
    },
  });
  if (!res.ok) {
    const body = await res.text().catch(() => "");
    throw new Error(humanizeError(res.status, body));
  }
  return res.json();
}

function humanizeError(status: number, body: string): string {
  let detail = body;
  try {
    const parsed = JSON.parse(body);
    detail = parsed.detail ?? body;
  } catch {
    /* not JSON */
  }
  return `Agent ${status}: ${detail || "request failed"}`;
}

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

/** Map a raw agent log line ("[ERROR] ...") to a UI log level. */
export function agentLogLevel(
  line: string
): "INFO" | "SUCCESS" | "WARN" | "ERROR" {
  const m = /^\s*\[(INFO|SUCCESS|WARN|WARNING|ERROR)\]/i.exec(line);
  const tag = (m?.[1] ?? "INFO").toUpperCase();
  if (tag === "WARNING") return "WARN";
  return tag as "INFO" | "SUCCESS" | "WARN" | "ERROR";
}

/** Poll a background job until it reaches a terminal state. */
async function pollJob(jobId: string, h: JobHandlers = {}): Promise<JobSnapshot> {
  let offset = 0;
  const interval = h.intervalMs ?? 700;
  for (;;) {
    if (h.signal?.aborted) throw new Error("Cancelled");
    const snap = await agentFetch<JobSnapshot>(
      `/jobs/${jobId}?log_offset=${offset}`
    );
    if (snap.logs?.length) {
      for (const line of snap.logs) h.onLog?.(line);
      offset = snap.log_count;
    }
    if (snap.progress && h.onProgress) h.onProgress(snap.progress);
    if (snap.state !== "running") return snap;
    await sleep(interval);
  }
}

// ---------------------------------------------------------------------------
// Adapters: real backend shapes -> UI shapes
// ---------------------------------------------------------------------------
function escapeHtml(s: string): string {
  return (s || "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
}

/** Plain text from the agent -> safe HTML with preserved line breaks. */
function textToHtml(s: string): string {
  const trimmed = (s || "").trim();
  if (!trimmed) return "";
  return escapeHtml(trimmed).replace(/\r?\n/g, "<br/>");
}

interface RawWorkItemsResponse {
  columns: string[];
  groups: Array<{ column: string; items: WorkItemRow[] }>;
  total: number;
}

interface RawWorkItemDetail {
  wi_id: number;
  title: string;
  wi_type: string;
  state: string;
  board_column: string;
  area_path: string;
  iteration_path: string;
  assigned_to: string;
  tags: string[];
  description_text: string;
  acceptance_text: string;
  description_html?: string;
  acceptance_html?: string;
  comments: Array<{ when: string; author: string; text: string }>;
  comments_html?: Array<{ when: string; author: string; html: string }>;
  attachments: Array<{ name: string; url: string; size: number; comment?: string }>;
  hyperlinks: Array<{ url: string; comment: string }>;
  related: Array<{ name: string; wi_id: number; url: string }>;
}

// Hosts whose <img>/attachment blobs require the stored PAT and must be proxied
// through the agent so the browser can load them.
const ADO_BLOB_HOST_RE = /(?:dev\.azure\.com|visualstudio\.com|\.azure\.com)/i;

/** Build a browser-loadable URL that streams an authenticated ADO blob. */
function adoBlobUrl(project: string, url: string, filename?: string): string {
  const q = new URLSearchParams({ project, url });
  if (filename) q.set("download", filename);
  return `${AGENT_URL}/ado/blob?${q.toString()}`;
}

/** Rewrite ADO-hosted <img src> values to load through the agent blob proxy. */
function rewriteHtmlMedia(html: string, project: string): string {
  if (!html) return "";
  return html.replace(/<img\b[^>]*>/gi, (tag) => {
    const m = /\ssrc=("|')(.*?)\1/i.exec(tag);
    if (!m) return tag;
    const src = m[2];
    if (!/^https?:/i.test(src) || !ADO_BLOB_HOST_RE.test(src)) return tag;
    return tag.replace(m[0], ` src="${adoBlobUrl(project, src)}"`);
  });
}

// ---------------------------------------------------------------------------
// Public API
// ---------------------------------------------------------------------------
export const agent = {
  // -- Health --
  async health(): Promise<HealthResponse> {
    return agentFetch<HealthResponse>("/health");
  },

  /** Live CPU/RAM/GPU usage. Present on agent >= 1.8.0; older agents 404 here,
   * so callers should treat a thrown error as "metrics unavailable". */
  async metrics(): Promise<MetricsResponse> {
    return agentFetch<MetricsResponse>("/metrics");
  },

  /** Agent capability map. Present on agent >= 2.3.0; older agents 404 here,
   * so callers should treat a thrown error as "capabilities unavailable" and
   * fall back to the `capabilities` field on /health when present. */
  async capabilities(): Promise<AgentCapabilities | null> {
    try {
      return await agentFetch<AgentCapabilities>("/capabilities");
    } catch {
      return null;
    }
  },

  /** Run agent self-diagnostics. Present on agent >= 2.3.0; returns null when
   * the endpoint is missing (older agents 404). */
  async doctor(): Promise<DoctorReport | null> {
    try {
      return await agentFetch<DoctorReport>("/doctor");
    } catch {
      return null;
    }
  },

  async checkConnection(): Promise<AgentStatus> {
    try {
      await this.health();
      return "connected";
    } catch {
      return "offline";
    }
  },

  /**
   * Live install/reinstall progress from the installer's temporary beacon on
   * the agent port, shown while the real agent is not yet up. Returns null when
   * nothing is serving it (no install in progress, or the beacon is between the
   * old-agent shutdown and binding the port). Never throws.
   */
  async installProgress(): Promise<InstallProgress | null> {
    try {
      const res = await fetch(`${AGENT_URL}/install/progress`, {
        // Keep it snappy: the beacon is local and we poll frequently.
        cache: "no-store",
      });
      if (!res.ok) return null;
      return (await res.json()) as InstallProgress;
    } catch {
      return null;
    }
  },

  // -- Settings --
  async getSettings(): Promise<SettingsResponse> {
    return agentFetch<SettingsResponse>("/settings");
  },

  async saveSettings(payload: SaveSettingsPayload): Promise<void> {
    await agentFetch("/settings", {
      method: "POST",
      body: JSON.stringify(payload),
    });
  },

  /** Persist guided-tour completion server-side (survives a localStorage wipe).
   * Older agents without this route 404 — callers should ignore that. */
  async setTourCompleted(completed: boolean): Promise<void> {
    await agentFetch("/settings/tour", {
      method: "POST",
      body: JSON.stringify({ completed }),
    });
  },

  // -- System prompts (per project + phase scope) --
  async getSystemPrompt(
    project: string,
    scope = ""
  ): Promise<SystemPrompt> {
    return agentFetch<SystemPrompt>(
      `/settings/system-prompt?project=${encodeURIComponent(
        project
      )}&scope=${encodeURIComponent(scope)}`
    );
  },

  async saveSystemPrompt(
    project: string,
    scope: string,
    text: string
  ): Promise<SystemPrompt> {
    return agentFetch<SystemPrompt>("/settings/system-prompt", {
      method: "POST",
      body: JSON.stringify({ project, scope, text }),
    });
  },

  async resetSystemPrompt(
    project: string,
    scope: string
  ): Promise<SystemPrompt> {
    return agentFetch<SystemPrompt>("/settings/system-prompt/reset", {
      method: "POST",
      body: JSON.stringify({ project, scope, text: "" }),
    });
  },

  // -- ADO --
  async verifyPat(): Promise<{ ok: boolean; detail: string }> {
    return agentFetch("/ado/verify");
  },

  async listProjects(): Promise<string[]> {
    return agentFetch<string[]>("/ado/projects");
  },

  async listBoards(project: string): Promise<Board[]> {
    return agentFetch<Board[]>(`/ado/boards/${encodeURIComponent(project)}`);
  },

  async boardView(project: string, board: Board): Promise<BoardView> {
    const raw = await agentFetch<RawWorkItemsResponse>("/ado/workitems", {
      method: "POST",
      body: JSON.stringify({
        project,
        board_id: board.id,
        board_name: board.name,
        team_id: board.team_id,
        team_name: board.team_name,
      }),
    });
    const columns: BoardColumn[] = (raw.columns ?? []).map((name) => ({
      id: name,
      name,
      column_type: "",
    }));
    const rows: WorkItemRow[] = [];
    for (const g of raw.groups ?? []) {
      for (const item of g.items ?? []) {
        // Ensure board_column is populated so the grid groups correctly.
        rows.push({ ...item, board_column: item.board_column || g.column });
      }
    }
    return { columns, rows };
  },

  async workItemDetail(project: string, wiId: number): Promise<WorkItemDetail> {
    const d = await agentFetch<RawWorkItemDetail>(
      `/ado/workitem/${encodeURIComponent(project)}/${wiId}`
    );
    return {
      wi_id: d.wi_id,
      title: d.title,
      wi_type: d.wi_type,
      state: d.state,
      board_column: d.board_column,
      area_path: d.area_path,
      iteration_path: d.iteration_path,
      assigned_to: d.assigned_to,
      tags: d.tags ?? [],
      description_html: rewriteHtmlMedia(
        d.description_html || textToHtml(d.description_text),
        project
      ),
      acceptance_html: rewriteHtmlMedia(
        d.acceptance_html || textToHtml(d.acceptance_text),
        project
      ),
      comments_html: (
        d.comments_html ??
        (d.comments ?? []).map((c) => ({
          when: c.when,
          author: c.author,
          html: textToHtml(c.text),
        }))
      ).map(
        (c) =>
          [c.author, c.when, rewriteHtmlMedia(c.html, project)] as [
            string,
            string,
            string
          ]
      ),
      attachments: (d.attachments ?? []).map((a) => ({
        name: a.name,
        url: a.url,
        size: a.size,
        comment: a.comment,
        downloadUrl: adoBlobUrl(project, a.url, a.name),
      })),
      hyperlinks: (d.hyperlinks ?? []).map(
        (h) => [h.comment || h.url, h.url] as [string, string]
      ),
      related: (d.related ?? []).map(
        (r) => [r.name, r.wi_id, r.url] as [string, number, string]
      ),
    };
  },

  // -- KB --
  async kbStatus(project: string): Promise<KbStatus> {
    return agentFetch<KbStatus>(`/kb/status/${encodeURIComponent(project)}`);
  },

  async kbRetrieve(
    project: string,
    query: string,
    topK = 32
  ): Promise<RetrievedChunk[]> {
    const res = await agentFetch<{ chunks: RetrievedChunk[] }>("/kb/retrieve", {
      method: "POST",
      body: JSON.stringify({ project, query, top_k: topK }),
    });
    return res.chunks;
  },

  async kbIndex(
    project: string,
    handlers: JobHandlers = {},
    force = false
  ): Promise<{ n_chunks: number; n_documents: number; has_dense?: boolean }> {
    // Background job mirroring the desktop _kick_kb_index worker: returns a job
    // id we poll for live per-file progress + logs, then read the final result.
    // force=true does a whole rebuild (ignores the "index is current" shortcut).
    const { job_id } = await agentFetch<{ job_id: string }>("/kb/index", {
      method: "POST",
      body: JSON.stringify({ project, force }),
    });
    const snap = await pollJob(job_id, handlers);
    if (snap.state === "error") {
      throw new Error(snap.error || "KB indexing failed");
    }
    const r = snap.result as {
      n_chunks?: number;
      n_documents?: number;
      has_dense?: boolean;
    };
    return {
      n_chunks: r.n_chunks ?? 0,
      n_documents: r.n_documents ?? 0,
      has_dense: r.has_dense,
    };
  },

  async kbUpload(project: string, file: File): Promise<void> {
    const formData = new FormData();
    formData.append("file", file);
    const res = await fetch(
      `${AGENT_URL}/kb/upload/${encodeURIComponent(project)}`,
      { method: "POST", body: formData }
    );
    if (!res.ok) {
      const body = await res.text().catch(() => "");
      throw new Error(humanizeError(res.status, body));
    }
  },

  /**
   * Upload a single KB document while reporting byte-level progress (0..1).
   * Uses XMLHttpRequest because the fetch API cannot surface upload progress.
   * onProgress(null) signals an indeterminate state (bytes total unknown).
   */
  kbUploadProgress(
    project: string,
    file: File,
    onProgress?: (fraction: number | null) => void
  ): Promise<void> {
    return new Promise<void>((resolve, reject) => {
      const formData = new FormData();
      formData.append("file", file);
      const xhr = new XMLHttpRequest();
      xhr.open(
        "POST",
        `${AGENT_URL}/kb/upload/${encodeURIComponent(project)}`
      );
      xhr.upload.onprogress = (e) => {
        if (!onProgress) return;
        onProgress(e.lengthComputable ? e.loaded / e.total : null);
      };
      // Body fully sent; server is now writing/processing the file.
      xhr.upload.onload = () => onProgress?.(1);
      xhr.onload = () => {
        if (xhr.status >= 200 && xhr.status < 300) {
          onProgress?.(1);
          resolve();
        } else {
          reject(new Error(humanizeError(xhr.status, xhr.responseText || "")));
        }
      };
      xhr.onerror = () =>
        reject(
          new Error(
            "Could not reach the local agent. Make sure Testing Toolkit is running."
          )
        );
      xhr.onabort = () => reject(new Error("Upload cancelled."));
      xhr.send(formData);
    });
  },

  /** Remove a single KB document and invalidate the stored index. */
  async deleteKbDocument(project: string, name: string): Promise<void> {
    await agentFetch(
      `/kb/document/${encodeURIComponent(project)}?name=${encodeURIComponent(
        name
      )}`,
      { method: "DELETE" }
    );
  },

  // -- Per-phase test-script templates --
  async templateStatus(
    project: string,
    phase: TcType
  ): Promise<TemplateStatus> {
    return agentFetch<TemplateStatus>(
      `/kb/template/${encodeURIComponent(project)}/${encodeURIComponent(phase)}`
    );
  },

  async uploadTemplate(
    project: string,
    phase: TcType,
    file: File
  ): Promise<TemplateStatus> {
    const formData = new FormData();
    formData.append("file", file);
    const res = await fetch(
      `${AGENT_URL}/kb/template/${encodeURIComponent(
        project
      )}/${encodeURIComponent(phase)}`,
      { method: "POST", body: formData }
    );
    if (!res.ok) {
      const body = await res.text().catch(() => "");
      throw new Error(humanizeError(res.status, body));
    }
    return res.json();
  },

  async deleteTemplate(project: string, phase: TcType): Promise<{ ok: boolean }> {
    return agentFetch<{ ok: boolean }>(
      `/kb/template/${encodeURIComponent(project)}/${encodeURIComponent(phase)}`,
      { method: "DELETE" }
    );
  },

  templateDownloadUrl(project: string, phase: TcType): string {
    return `${AGENT_URL}/kb/template/${encodeURIComponent(
      project
    )}/${encodeURIComponent(phase)}/download`;
  },

  // -- Artifacts (generated outputs browser) --
  async listArtifacts(project: string): Promise<ArtifactFile[]> {
    return agentFetch<ArtifactFile[]>(
      `/artifacts/${encodeURIComponent(project)}`
    );
  },

  artifactDownloadUrl(path: string): string {
    return `${AGENT_URL}/artifacts/download?path=${encodeURIComponent(path)}`;
  },

  async deleteArtifact(path: string): Promise<{ ok: boolean }> {
    return agentFetch<{ ok: boolean }>(
      `/artifacts/delete?path=${encodeURIComponent(path)}`,
      { method: "DELETE" }
    );
  },

  // -- Generation (async job) --
  /** Start a generation run and poll to completion. */
  async generate(
    payload: {
      project: string;
      wi_ids: number[];
      tc_type: TcType | "";
      manual_payload?: Record<string, unknown> | null;
      regen_feedback?: string;
      base_payload?: Record<string, unknown> | null;
      fast_model?: boolean;
    },
    handlers: JobHandlers = {}
  ): Promise<GenerationResult> {
    const { job_id } = await agentFetch<{ job_id: string }>("/generate/start", {
      method: "POST",
      body: JSON.stringify({
        project: payload.project,
        wi_ids: payload.wi_ids,
        tc_type: payload.tc_type,
        manual_payload: payload.manual_payload ?? null,
        regen_feedback: payload.regen_feedback ?? "",
        base_payload: payload.base_payload ?? null,
        fast_model: payload.fast_model ?? false,
      }),
    });
    const snap = await pollJob(job_id, handlers);
    if (snap.state !== "done") {
      throw new Error(snap.error || `Generation ${snap.state}`);
    }
    return snap.result as unknown as GenerationResult;
  },

  /** Build the manual-mode work-item dump + system prompt. */
  async buildDump(
    project: string,
    wiIds: number[],
    tcType: TcType | ""
  ): Promise<{ dump: string; system_prompt: string; n_items: number }> {
    return agentFetch("/generate/dump", {
      method: "POST",
      body: JSON.stringify({ project, wi_ids: wiIds, tc_type: tcType }),
    });
  },

  /** Push a reviewed payload (in-memory JSON) to ADO. */
  async pushPayload(
    payload: {
      project: string;
      payload: Record<string, unknown>;
      area_override?: string;
      iteration_override?: string;
      inherit_paths?: boolean;
      test_category_field?: string;
    },
    handlers: JobHandlers = {}
  ): Promise<CreatedResult> {
    const { job_id } = await agentFetch<{ job_id: string }>("/generate/push", {
      method: "POST",
      body: JSON.stringify({
        project: payload.project,
        payload: payload.payload,
        area_override: payload.area_override ?? "",
        iteration_override: payload.iteration_override ?? "",
        inherit_paths: payload.inherit_paths ?? true,
        test_category_field: payload.test_category_field ?? "Custom.TestCategory",
      }),
    });
    const snap = await pollJob(job_id, handlers);
    if (snap.state !== "done") throw new Error(snap.error || `Push ${snap.state}`);
    return snap.result as unknown as CreatedResult;
  },

  /** Push a reviewer-edited .xlsx (on the agent host) to ADO. */
  async pushReviewedXlsx(
    payload: { project: string; xlsx_path: string },
    handlers: JobHandlers = {}
  ): Promise<CreatedResult> {
    const { job_id } = await agentFetch<{ job_id: string }>(
      "/generate/push-xlsx",
      {
        method: "POST",
        body: JSON.stringify({
          project: payload.project,
          xlsx_path: payload.xlsx_path,
        }),
      }
    );
    const snap = await pollJob(job_id, handlers);
    if (snap.state !== "done") throw new Error(snap.error || `Push ${snap.state}`);
    return snap.result as unknown as CreatedResult;
  },

  /** Reviewer Excel download URL for a finished generation job. */
  generateExcelUrl(jobId: string): string {
    return `${AGENT_URL}/generate/excel/${jobId}`;
  },

  // -- Defects --
  /** Parse uploaded defect documents (multipart) into structured records. */
  async parseDefects(
    files: File[],
    useLlm: boolean
  ): Promise<{ defects: ParsedDefect[]; logs: string[]; n_defects: number }> {
    const fd = new FormData();
    for (const f of files) fd.append("files", f);
    fd.append("use_llm", String(useLlm));
    const res = await fetch(`${AGENT_URL}/defects/parse`, {
      method: "POST",
      body: fd,
    });
    if (!res.ok) {
      const body = await res.text().catch(() => "");
      throw new Error(humanizeError(res.status, body));
    }
    return res.json();
  },

  /** Export reviewed defects to an .xlsx and trigger a browser download. */
  async downloadDefectsExcel(defects: ParsedDefect[]): Promise<void> {
    const kept = defects.filter((d) => !d.skip && d.title.trim());
    if (kept.length === 0) throw new Error("No defects to export");
    const res = await fetch(`${AGENT_URL}/defects/excel`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ defects: kept }),
    });
    if (!res.ok) {
      const body = await res.text().catch(() => "");
      throw new Error(humanizeError(res.status, body));
    }
    const blob = await res.blob();
    const disposition = res.headers.get("content-disposition") ?? "";
    const match = /filename="?([^"]+)"?/.exec(disposition);
    const filename = match?.[1] ?? "defects_review.xlsx";
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
  },

  /** Create Bug work items from reviewed defects (async job). */
  async uploadDefects(
    project: string,
    defects: ParsedDefect[],
    handlers: JobHandlers = {}
  ): Promise<CreatedResult> {
    const { job_id } = await agentFetch<{ job_id: string }>("/defects/upload", {
      method: "POST",
      body: JSON.stringify({ project, defects }),
    });
    const snap = await pollJob(job_id, handlers);
    if (snap.state !== "done")
      throw new Error(snap.error || `Upload ${snap.state}`);
    return snap.result as unknown as CreatedResult;
  },

  // -- Tools: PDF packaging (async job) --
  async packagePdfs(
    payload: { project: string; wi_ids: number[]; paper_size?: string },
    handlers: JobHandlers = {}
  ): Promise<{
    output_dir: string;
    n_package_ok: number;
    n_extract_ok: number;
  }> {
    const { job_id } = await agentFetch<{ job_id: string }>("/tools/package", {
      method: "POST",
      body: JSON.stringify({
        project: payload.project,
        wi_ids: payload.wi_ids,
        paper_size: payload.paper_size ?? "A4",
      }),
    });
    const snap = await pollJob(job_id, handlers);
    if (snap.state !== "done")
      throw new Error(snap.error || `Packaging ${snap.state}`);
    return snap.result as unknown as {
      output_dir: string;
      n_package_ok: number;
      n_extract_ok: number;
    };
  },

  async stopJob(jobId: string): Promise<void> {
    await agentFetch(`/jobs/${jobId}/stop`, { method: "POST" });
  },

  // -- LLM --
  async complete(params: {
    system?: string;
    user: string;
    model?: string;
    max_tokens?: number;
    temperature?: number;
    thinking_budget?: number;
  }): Promise<{
    text: string;
    stop_reason: string;
    input_tokens: number;
    output_tokens: number;
  }> {
    return agentFetch("/llm/complete", {
      method: "POST",
      body: JSON.stringify(params),
    });
  },

  async listModels(refresh = false): Promise<ModelInfo[]> {
    // Default reads the agent-side cache (instant). Pass refresh=true to
    // force a fresh probe (the "Fetch models" button).
    return agentFetch<ModelInfo[]>(
      `/llm/models${refresh ? "?refresh=true" : ""}`
    );
  },

  async recentLog(maxBytes = 60000): Promise<RecentLog> {
    return agentFetch<RecentLog>(`/tools/log?max_bytes=${maxBytes}`);
  },

  // -- Updates --
  async updateStatus(): Promise<UpdateStatus> {
    try {
      return await agentFetch<UpdateStatus>("/update/status");
    } catch (e) {
      // This agent build ships without the update routes — report it as simply
      // "not configured" rather than surfacing a 404 as an error.
      if (isAgent404(e)) {
        return {
          current: "unknown",
          latest: null,
          update_available: false,
          configured: false,
          reachable: true,
          install_dir: "",
        };
      }
      throw e;
    }
  },

  async applyUpdate(): Promise<UpdateApplyResult> {
    try {
      return await agentFetch<UpdateApplyResult>("/update/apply", {
        method: "POST",
      });
    } catch (e) {
      if (isAgent404(e)) return NOT_CONFIGURED_RESULT;
      throw e;
    }
  },

  /**
   * Live progress of an in-flight apply, for the "Update in progress" screen.
   * Cheap to poll. Returns null on older agents that lack the /update/progress
   * route (so callers can fall back to an indeterminate bar).
   */
  async updateProgress(): Promise<UpdateProgress | null> {
    try {
      return await agentFetch<UpdateProgress>("/update/progress");
    } catch (e) {
      if (isAgent404(e)) return null;
      throw e;
    }
  },

  /**
   * Hand the local agent a read-only update token so a token-less install can
   * start self-updating without a reinstall. Returns the refreshed update
   * status (now `configured:true`), or null when this agent build predates the
   * /update/config route (404) so callers can no-op gracefully.
   */
  async configureUpdate(cfg: AgentUpdateConfig): Promise<UpdateStatus | null> {
    try {
      return await agentFetch<UpdateStatus>("/update/config", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(cfg),
      });
    } catch (e) {
      if (isAgent404(e)) return null;
      throw e;
    }
  },
};

export interface AgentUpdateConfig {
  token: string;
  repo?: string;
  ref?: string;
  manifest_url?: string;
}

/** True when an agent request failed because the route does not exist (404). */
function isAgent404(e: unknown): boolean {
  return !!(e as Error)?.message?.includes("Agent 404");
}

/** Synthetic result used when the agent has no update/reinstall routes. */
const NOT_CONFIGURED_RESULT: UpdateApplyResult = {
  applied: false,
  status: "not_configured",
  current: "unknown",
  latest: null,
  restarting: false,
};

export interface ModelInfo {
  id: string;
  provider: string;
  label: string;
}

export interface RecentLog {
  text: string;
  path: string;
  dir: string;
}

export interface UpdateStatus {
  current: string;
  latest: string | null;
  update_available: boolean;
  configured: boolean;
  reachable: boolean;
  install_dir: string;
}

export interface UpdateApplyResult {
  applied: boolean;
  status:
    | "applied"
    | "started"
    | "up_to_date"
    | "failed"
    | "not_configured"
    | "unreachable";
  current: string;
  latest: string | null;
  restarting: boolean;
  detail?: string;
}

export type UpdatePhase =
  | "idle"
  | "starting"
  | "downloading"
  | "installing_deps"
  | "staging"
  | "restarting"
  | "done"
  | "up_to_date"
  | "failed";

export interface UpdateProgress {
  active: boolean;
  phase: UpdatePhase;
  message: string;
  current: number;
  total: number;
  percent: number;
  version: string;
  status: string;
  detail: string;
  updated_at: number;
}

export interface SystemPrompt {
  project: string;
  scope: string;
  text: string;
}

// ---------------------------------------------------------------------------
// Display helpers (core/app_config.py display_project_name)
// ---------------------------------------------------------------------------
export function displayProjectName(full: string, prefix: string): string {
  if (prefix && full.toLowerCase().startsWith(prefix.toLowerCase())) {
    const stripped = full.slice(prefix.length).replace(/^[\s_-]+/, "");
    return stripped || full;
  }
  return full;
}
