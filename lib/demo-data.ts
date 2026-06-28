/**
 * demo-data.ts
 * Sample data so the hosted web app can showcase the full GUI without a
 * running local agent. Activated from the onboarding screen ("Explore with
 * sample data"). Mirrors the desktop data model exactly.
 */

import type {
  ArtifactFile,
  Board,
  BoardView,
  HealthResponse,
  RetrievedChunk,
  SettingsResponse,
  WorkItemDetail,
  WorkItemRow,
} from "./agent-client";

export const demoHealth: HealthResponse = {
  status: "ok",
  version: "2.0.0",
  user: "demo",
  machine: "WEB-PREVIEW",
  models_loaded: true,
  tls_mode: "system",
};

export const demoSettings: SettingsResponse = {
  configured: true,
  has_api_key: true,
  has_pat: true,
  organization: "pwc-us-adv-digital",
  model: "bedrock.anthropic.claude-opus-4-6",
  fast_model: "bedrock.anthropic.claude-haiku-4-5",
  fallback_model: "bedrock.anthropic.claude-sonnet-4-6",
  base_url: "https://api.anthropic.com",
  project_prefix: "InteractionsHub_",
  tls_mode: "system",
};

export const demoProjects = [
  "InteractionsHub_Abbott",
  "InteractionsHub_argenx",
  "InteractionsHub_Novartis",
  "InteractionsHub_Pfizer",
];

export function demoBoards(): Board[] {
  return [
    {
      id: "b-core",
      name: "Stories",
      team_id: "t-core",
      team_name: "Core Platform",
      label: "Core Platform / Stories",
    },
    {
      id: "b-data",
      name: "Stories",
      team_id: "t-data",
      team_name: "Data Services",
      label: "Data Services / Stories",
    },
    {
      id: "b-mobile",
      name: "Stories",
      team_id: "t-mobile",
      team_name: "Mobile",
      label: "Mobile / Stories",
    },
  ];
}

const COLUMNS = ["New", "Active", "Resolved", "Closed"];

const TITLES: Array<[string, string, string, string]> = [
  ["User can reset password via email link", "User Story", "Active", "Sprint 24"],
  ["Login fails with valid SSO token", "Bug", "Active", "Sprint 24"],
  ["Add audit log for record edits", "User Story", "New", "Sprint 25"],
  ["Dashboard chart renders blank on Safari", "Bug", "New", "Sprint 25"],
  ["Bulk export to CSV exceeds 10k rows", "User Story", "Resolved", "Sprint 23"],
  ["Validate phone field format (E.164)", "User Story", "Active", "Sprint 24"],
  ["Session timeout not enforced after idle", "Bug", "Resolved", "Sprint 23"],
  ["Role-based access for admin console", "User Story", "Closed", "Sprint 22"],
  ["Notification email missing unsubscribe", "Bug", "New", "Sprint 25"],
  ["Search returns stale results after edit", "Bug", "Active", "Sprint 24"],
  ["Onboarding wizard step 3 validation", "User Story", "Closed", "Sprint 22"],
  ["API rate limit headers incorrect", "Bug", "Resolved", "Sprint 23"],
];

const ASSIGNEES = ["Priya Nair", "Marcus Chen", "Sofia Ramos", "Liam O'Brien", ""];

export function demoBoardView(): BoardView {
  const rows: WorkItemRow[] = TITLES.map(([title, type, state, sprint], i) => ({
    wi_id: 10240 + i,
    title,
    wi_type: type,
    state,
    board_column: COLUMNS[i % COLUMNS.length],
    board_lane: sprint,
    assigned_to: ASSIGNEES[i % ASSIGNEES.length],
    tags: i % 3 === 0 ? ["priority"] : [],
    iteration_path: `InteractionsHub\\${sprint}`,
    area_path: i % 2 === 0 ? "InteractionsHub\\Web" : "InteractionsHub\\API",
  }));
  return {
    columns: COLUMNS.map((name, i) => ({
      id: `c-${i}`,
      name,
      column_type: i === 0 ? "incoming" : i === COLUMNS.length - 1 ? "outgoing" : "inProgress",
    })),
    rows,
  };
}

export function demoWorkItemDetail(wiId: number): WorkItemDetail {
  const view = demoBoardView();
  const row = view.rows.find((r) => r.wi_id === wiId) ?? view.rows[0];
  return {
    wi_id: row.wi_id,
    title: row.title,
    wi_type: row.wi_type,
    state: row.state,
    board_column: row.board_column,
    area_path: row.area_path,
    iteration_path: row.iteration_path,
    assigned_to: row.assigned_to,
    tags: row.tags,
    description_html: `<p>As a user, I want <b>${row.title.toLowerCase()}</b> so that the workflow is reliable and auditable.</p><p>This story covers the happy path plus boundary and error conditions described in the linked requirements.</p>`,
    acceptance_html:
      "<ul><li>Given a valid input, the action completes and a confirmation is shown.</li><li>Given an invalid input, a clear validation message appears and no state changes.</li><li>All actions are recorded in the audit log.</li></ul>",
    comments_html: [
      ["Priya Nair", "2 days ago", "<p>Confirmed the repro on staging. Attaching the HAR file.</p>"],
      ["Marcus Chen", "1 day ago", "<p>Root cause looks like a missing null check in the validator.</p>"],
    ],
    attachments: [
      { name: "repro-steps.docx", url: "#", size: 24576, comment: "" },
      { name: "screenshot.png", url: "#", size: 102400, comment: "" },
    ],
    hyperlinks: [["Requirements - Confluence", "#"]],
    related: [["Child", row.wi_id + 5000, "Test Case: validation boundaries"]],
  };
}

export function demoArtifacts(): ArtifactFile[] {
  return [
    {
      name: "testcases_review_Implementation_20260628.xlsx",
      path: "outputs/InteractionsHub_Abbott/testcases/testcases_review_Implementation_20260628.xlsx",
      kind: "testcases",
      size: 48213,
      modified: Date.now() - 3600_000,
    },
    {
      name: "All_WIs_Combined.pdf",
      path: "outputs/InteractionsHub_Abbott/packets/All_WIs_Combined.pdf",
      kind: "packets",
      size: 1843200,
      modified: Date.now() - 7200_000,
    },
  ];
}

export function demoChunks(query: string): RetrievedChunk[] {
  return Array.from({ length: 8 }, (_, i) => ({
    chunk_id: `chunk-${i}`,
    doc: `requirements_v${(i % 3) + 1}.pdf`,
    title: `Section ${i + 1}: ${query.slice(0, 24) || "Requirements"}`,
    text: `Relevant requirement context for "${query}". The system shall validate inputs, enforce role-based access, and record an audit entry. Boundary conditions include empty values, maximum field length, and concurrent edits. This chunk was selected via BM25 + dense fusion and cross-encoder reranking.`,
    score: 1 - i * 0.07,
  }));
}
