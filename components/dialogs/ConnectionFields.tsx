"use client";

import { useState } from "react";
import { type SaveSettingsPayload } from "@/lib/agent-client";

// Default model IDs shown (read-only) in the first-run wizard. These MUST match
// the agent's real tier defaults in core/app_config.py
// (DEFAULT_MODEL / DEFAULT_FAST_MODEL / DEFAULT_FALLBACK_MODEL): the Bedrock
// Claude trio. Order: [0]=primary, [1]=fast, [2]=fallback. Models are managed by
// the backend and are no longer user-editable in Settings (desktop parity).
const SEED_MODELS = [
  "bedrock.anthropic.claude-opus-4-6",
  "bedrock.anthropic.claude-sonnet-4-6",
  "bedrock.anthropic.claude-haiku-4-5",
];

export interface ConnectionValues {
  api_key: string;
  base_url: string;
  model: string;
  fast_model: string;
  fallback_model: string;
  pat: string;
  organization: string;
  project_prefix: string;
  tls_mode: string;
  // -- JIRA source (optional second work-item source) --
  jira_url: string;
  jira_user: string;
  jira_pat: string;
  jira_project_prefix: string;
}

const MASK = "************";

export function useConnectionFields(initial?: Partial<ConnectionValues>) {
  const [values, setValues] = useState<ConnectionValues>({
    api_key: initial?.api_key ?? "",
    base_url: initial?.base_url ?? "",
    model: initial?.model ?? "",
    fast_model: initial?.fast_model ?? "",
    fallback_model: initial?.fallback_model ?? "",
    pat: initial?.pat ?? "",
    organization: initial?.organization ?? "",
    project_prefix: initial?.project_prefix ?? "",
    tls_mode: initial?.tls_mode ?? "system",
    jira_url: initial?.jira_url ?? "",
    jira_user: initial?.jira_user ?? "",
    jira_pat: initial?.jira_pat ?? "",
    jira_project_prefix: initial?.jira_project_prefix ?? "",
  });
  return { values, setValues };
}

export function toPayload(v: ConnectionValues): SaveSettingsPayload {
  const p: SaveSettingsPayload = {
    organization: v.organization,
    project_prefix: v.project_prefix,
    tls_mode: v.tls_mode || "system",
  };
  // Base URL / API key are entered only in the first-run wizard and are masked
  // like secrets: only send when the user typed a fresh value, otherwise the
  // backend keeps the stored value.
  if (v.base_url && v.base_url !== MASK) p.base_url = v.base_url;
  if (v.api_key && v.api_key !== MASK) p.api_key = v.api_key;
  if (v.pat && v.pat !== MASK) p.pat = v.pat;
  // Models are backend-managed (no UI). Only forward a value when one is
  // actually present so we never clobber the stored/default model with "".
  if (v.model) p.model = v.model;
  if (v.fast_model) p.fast_model = v.fast_model;
  if (v.fallback_model) p.fallback_model = v.fallback_model;
  // JIRA: URL/user/prefix are plain values; the token is masked like a secret.
  p.jira_url = v.jira_url;
  p.jira_user = v.jira_user;
  p.jira_project_prefix = v.jira_project_prefix;
  if (v.jira_pat && v.jira_pat !== MASK) p.jira_pat = v.jira_pat;
  return p;
}

function Field({
  label,
  required,
  children,
}: {
  label: string;
  required?: boolean;
  children: React.ReactNode;
}) {
  return (
    <div className="grid grid-cols-[140px_1fr] items-center gap-3">
      <label className="text-right text-sm text-[var(--tt-text-secondary)]">
        {label}:{required && <span className="text-[var(--tt-danger)]"> *</span>}
      </label>
      {children}
    </div>
  );
}

/**
 * Masked secret field with an Edit button. Shows dots for a saved value;
 * clicking Edit clears it and enables typing a new value.
 */
function MaskedField({
  value,
  onChange,
  placeholder,
}: {
  value: string;
  onChange: (v: string) => void;
  placeholder?: string;
}) {
  const hasSaved = value === MASK;
  const [editing, setEditing] = useState(!hasSaved);
  return (
    <div className="flex gap-2">
      <input
        type="password"
        className="tt-input flex-1"
        placeholder={placeholder}
        value={editing ? value : MASK}
        disabled={!editing}
        onChange={(e) => onChange(e.target.value)}
      />
      {!editing && (
        <button
          type="button"
          className="tt-btn-ghost shrink-0 !px-3 !py-1.5 text-xs"
          onClick={() => {
            setEditing(true);
            onChange("");
          }}
        >
          Edit
        </button>
      )}
    </div>
  );
}

function SectionHeader({ children }: { children: React.ReactNode }) {
  return <h3 className="tt-header mt-2 text-sm first:mt-0">{children}</h3>;
}

/** Read-only model rows for the first-run setup stage. Models are not editable
 *  — the agent manages the working list automatically in the background. */
function ReadOnlyModels({ values }: { values: ConnectionValues }) {
  const rows: { label: string; value: string; fallback: string }[] = [
    { label: "Model", value: values.model, fallback: SEED_MODELS[0] },
    { label: "Fast model", value: values.fast_model, fallback: SEED_MODELS[1] },
    {
      label: "Fallback model",
      value: values.fallback_model,
      fallback: SEED_MODELS[2],
    },
  ];
  return (
    <>
      {rows.map((r) => (
        <Field key={r.label} label={r.label}>
          <input
            type="text"
            className="tt-input cursor-not-allowed opacity-70"
            value={r.value || r.fallback}
            readOnly
            disabled
            title="Models are configured automatically by the backend."
          />
        </Field>
      ))}
      <p className="pl-[152px] text-xs text-muted-foreground">
        These are sensible defaults. The working model list is managed
        automatically by the backend once the app is connected.
      </p>
    </>
  );
}

export function ConnectionFields({
  values,
  setValues,
  readOnlyModels = false,
}: {
  values: ConnectionValues;
  setValues: React.Dispatch<React.SetStateAction<ConnectionValues>>;
  readOnlyModels?: boolean;
}) {
  const set = (k: keyof ConnectionValues, v: string) =>
    setValues((prev) => ({ ...prev, [k]: v }));

  return (
    <div className="flex flex-col gap-3">
      {/* LLM API + models are collected ONLY during first-run setup. The
          ongoing Settings dialog has no LLM section — the API endpoint and
          models are backend-managed (desktop parity with the updated
          global_settings_dialog, which hides these fields). */}
      {readOnlyModels && (
        <>
          <SectionHeader>LLM</SectionHeader>
          <Field label="API Key">
            <MaskedField
              value={values.api_key}
              placeholder="sk-ant-..."
              onChange={(v) => set("api_key", v)}
            />
          </Field>
          <Field label="Base URL">
            <MaskedField
              value={values.base_url}
              placeholder="https://your-llm-api-endpoint.com"
              onChange={(v) => set("base_url", v)}
            />
          </Field>
          <ReadOnlyModels values={values} />
        </>
      )}

      <SectionHeader>Azure DevOps</SectionHeader>
      <Field label="PAT" required>
        <MaskedField
          value={values.pat}
          placeholder="Personal Access Token (required)"
          onChange={(v) => set("pat", v)}
        />
      </Field>
      <Field label="Organization" required>
        <input
          type="text"
          className="tt-input"
          placeholder="e.g. pwc-us-adv-digital (required)"
          value={values.organization}
          onChange={(e) => set("organization", e.target.value)}
        />
      </Field>
      <p className="pl-[152px] text-xs text-[var(--tt-danger)]">* required</p>

      {!readOnlyModels && (
        <>
          <SectionHeader>JIRA (optional)</SectionHeader>
          <Field label="Base URL">
            <input
              type="text"
              className="tt-input"
              placeholder="https://jira.your-company.com"
              value={values.jira_url}
              onChange={(e) => set("jira_url", e.target.value)}
            />
          </Field>
          <Field label="Username / Email">
            <input
              type="text"
              className="tt-input"
              placeholder="you@company.com"
              value={values.jira_user}
              onChange={(e) => set("jira_user", e.target.value)}
            />
          </Field>
          <Field label="API Token / PAT">
            <MaskedField
              value={values.jira_pat}
              placeholder="JIRA API token or PAT"
              onChange={(v) => set("jira_pat", v)}
            />
          </Field>
          <Field label="Strip project prefix">
            <input
              type="text"
              className="tt-input"
              placeholder="(optional)"
              value={values.jira_project_prefix}
              onChange={(e) => set("jira_project_prefix", e.target.value)}
            />
          </Field>
          <p className="pl-[152px] text-xs text-muted-foreground">
            Connect a JIRA Server/Data Center instance to browse boards and
            generate test cases from JIRA issues alongside Azure DevOps. Leave
            blank to use Azure DevOps only.
          </p>

          <SectionHeader>Display</SectionHeader>
          <Field label="Strip project prefix">
            <input
              type="text"
              className="tt-input"
              placeholder="InteractionsHub_"
              value={values.project_prefix}
              onChange={(e) => set("project_prefix", e.target.value)}
            />
          </Field>
          <p className="pl-[152px] text-xs text-muted-foreground">
            Project names are shown with this prefix stripped, e.g.
            InteractionsHub_Abbott → Abbott.
          </p>
        </>
      )}
    </div>
  );
}
