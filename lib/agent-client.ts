/**
 * agent-client.ts
 * Typed client for the local compute agent at localhost:7842.
 *
 * Mirrors the desktop app's data model (ado/boards.py, testgen/tc_types.py)
 * so the web GUI is a faithful 1:1 of the PySide6 desktop experience.
 */

import * as demo from "./demo-data";

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
  has_pat?: boolean;
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
  tls_mode?: string;
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
  comment: string;
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
  comments_html: Array<[string, string, string]>;
  attachments: Attachment[];
  hyperlinks: Array<[string, string]>;
  related: Array<[string, number, string]>;
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
  kind: string; // "testcases" | "packets" | ...
  size: number;
  modified: number;
}

export type AgentStatus = "connected" | "offline" | "connecting";

// ---------------------------------------------------------------------------
// Demo mode: when the local agent is unavailable (e.g. the hosted Vercel app),
// the user can opt into sample data to explore the full GUI.
// ---------------------------------------------------------------------------
const DEMO_KEY = "tt_demo_mode";
let _demo = false;

export function isDemoMode(): boolean {
  if (_demo) return true;
  if (typeof window !== "undefined") {
    _demo = window.localStorage.getItem(DEMO_KEY) === "1";
  }
  return _demo;
}

export function setDemoMode(on: boolean): void {
  _demo = on;
  if (typeof window !== "undefined") {
    if (on) window.localStorage.setItem(DEMO_KEY, "1");
    else window.localStorage.removeItem(DEMO_KEY);
  }
}

const delay = (ms: number) => new Promise((r) => setTimeout(r, ms));

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
    throw new Error(`Agent ${res.status}: ${body}`);
  }
  return res.json();
}

export const agent = {
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
    return agentFetch<BoardView>("/ado/board-view", {
      method: "POST",
      body: JSON.stringify({
        project,
        team: board.team_id || board.team_name,
        board: board.id || board.name,
      }),
    });
  },

  async workItemDetail(project: string, wiId: number): Promise<WorkItemDetail> {
    return agentFetch<WorkItemDetail>(
      `/ado/workitem/${encodeURIComponent(project)}/${wiId}`
    );
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
  ): Promise<{ n_chunks: number; n_documents: number }> {
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
    if (!res.ok) throw new Error(`Upload failed: ${res.status}`);
  },

  // -- Artifacts (generated outputs browser) --
  async listArtifacts(project: string): Promise<ArtifactFile[]> {
    return agentFetch<ArtifactFile[]>(
      `/artifacts/${encodeURIComponent(project)}`
    );
  },

  // -- Generation / packaging / upload --
  async generate(payload: {
    project: string;
    wi_ids: number[];
    tc_type: TcType | "";
    feedback?: string;
  }): Promise<{ xlsx_path: string; n_test_cases: number }> {
    return agentFetch("/testgen/generate", {
      method: "POST",
      body: JSON.stringify(payload),
    });
  },

  async packagePdfs(payload: {
    project: string;
    wi_ids: number[];
  }): Promise<{ output_dir: string; n_pdfs: number }> {
    return agentFetch("/tools/package", {
      method: "POST",
      body: JSON.stringify(payload),
    });
  },

  async uploadToAdo(payload: {
    project: string;
    xlsx_path: string;
  }): Promise<{ created: number; skipped: number }> {
    return agentFetch("/ado/upload", {
      method: "POST",
      body: JSON.stringify(payload),
    });
  },

  async uploadDefects(payload: {
    project: string;
    files: string[];
  }): Promise<{ review_xlsx: string; n_defects: number }> {
    return agentFetch("/defects/parse", {
      method: "POST",
      body: JSON.stringify(payload),
    });
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
