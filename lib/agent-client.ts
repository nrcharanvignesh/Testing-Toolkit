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
export interface HealthResponse {
  status: string;
  version: string;
  user: string;
  machine: string;
  models_loaded: boolean;
  tls_mode?: string;
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
  comments: Array<{ when: string; author: string; text: string }>;
  attachments: Array<{ name: string; url: string; size: number }>;
  hyperlinks: Array<{ url: string; comment: string }>;
  related: Array<{ name: string; wi_id: number; url: string }>;
}

// ---------------------------------------------------------------------------
// Public API
// ---------------------------------------------------------------------------
export const agent = {
  // -- Health --
  async health(): Promise<HealthResponse> {
    return agentFetch<HealthResponse>("/health");
  },

  async checkConnection(): Promise<AgentStatus> {
    try {
      await this.health();
      return "connected";
    } catch {
      return "offline";
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
      description_html: textToHtml(d.description_text),
      acceptance_html: textToHtml(d.acceptance_text),
      comments_html: (d.comments ?? []).map(
        (c) => [c.author, c.when, textToHtml(c.text)] as [string, string, string]
      ),
      attachments: (d.attachments ?? []).map((a) => ({
        name: a.name,
        url: a.url,
        size: a.size,
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
    project: string
  ): Promise<{ n_chunks: number; n_documents: number; has_dense?: boolean }> {
    return agentFetch("/kb/index", {
      method: "POST",
      body: JSON.stringify({ project }),
    });
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

  // -- Artifacts (generated outputs browser) --
  async listArtifacts(project: string): Promise<ArtifactFile[]> {
    return agentFetch<ArtifactFile[]>(
      `/artifacts/${encodeURIComponent(project)}`
    );
  },

  artifactDownloadUrl(path: string): string {
    return `${AGENT_URL}/artifacts/download?path=${encodeURIComponent(path)}`;
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

  async listModels(): Promise<string[]> {
    return agentFetch<string[]>("/llm/models");
  },
};

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
