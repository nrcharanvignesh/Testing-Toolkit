/**
 * agent-client.ts
 * Typed client for the local compute agent at localhost:7842.
 */

const AGENT_URL = "http://127.0.0.1:7842";

export interface HealthResponse {
  status: string;
  version: string;
  user: string;
  machine: string;
  models_loaded: boolean;
}

export interface SettingsResponse {
  configured: boolean;
  has_api_key: boolean;
  organization: string;
  model: string;
  fast_model: string;
  fallback_model: string;
  base_url: string;
  project_prefix: string;
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
}

export type AgentStatus = "connected" | "offline" | "connecting";

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

  async listBoards(project: string): Promise<Array<{ name: string; id: string }>> {
    return agentFetch(`/ado/boards/${encodeURIComponent(project)}`);
  },

  async listWorkItems(
    project: string,
    team: string,
    board: string
  ): Promise<Array<Record<string, unknown>>> {
    return agentFetch("/ado/workitems", {
      method: "POST",
      body: JSON.stringify({ project, team, board }),
    });
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

  async kbIndex(project: string): Promise<{ n_chunks: number; n_documents: number }> {
    return agentFetch("/kb/index", {
      method: "POST",
      body: JSON.stringify({ project }),
    });
  },

  async kbUpload(project: string, file: File): Promise<void> {
    const formData = new FormData();
    formData.append("file", file);
    const res = await fetch(`${AGENT_URL}/kb/upload/${encodeURIComponent(project)}`, {
      method: "POST",
      body: formData,
    });
    if (!res.ok) throw new Error(`Upload failed: ${res.status}`);
  },

  // -- LLM --
  async complete(params: {
    system?: string;
    user: string;
    model?: string;
    max_tokens?: number;
    temperature?: number;
    thinking_budget?: number;
  }): Promise<{ text: string; stop_reason: string; input_tokens: number; output_tokens: number }> {
    return agentFetch("/llm/complete", {
      method: "POST",
      body: JSON.stringify(params),
    });
  },

  async listModels(): Promise<string[]> {
    return agentFetch<string[]>("/llm/models");
  },
};
