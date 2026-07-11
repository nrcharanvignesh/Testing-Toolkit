"use client";

import { useState } from "react";
import { type SaveSettingsPayload } from "@/lib/agent-client";

export interface ConnectionValues {
  pat: string;
  organization: string;
  project_prefix: string;
  // -- JIRA source (optional second work-item source) --
  jira_url: string;
  jira_user: string;
  jira_pat: string;
  jira_project_prefix: string;
}

const MASK = "************";

export function useConnectionFields(initial?: Partial<ConnectionValues>) {
  const [values, setValues] = useState<ConnectionValues>({
    pat: initial?.pat ?? "",
    organization: initial?.organization ?? "",
    project_prefix: initial?.project_prefix ?? "",
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
  };
  // AI secrets, endpoints, and model IDs are intentionally never accepted from
  // browser state. The installed agent resolves centrally managed secrets and
  // the backend model router owns all task-to-model selection.
  if (v.pat && v.pat !== MASK) p.pat = v.pat;
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

export function ConnectionFields({
  values,
  setValues,
}: {
  values: ConnectionValues;
  setValues: React.Dispatch<React.SetStateAction<ConnectionValues>>;
}) {
  const set = (k: keyof ConnectionValues, v: string) =>
    setValues((prev) => ({ ...prev, [k]: v }));

  return (
    <div className="flex flex-col gap-3">
      <SectionHeader>Azure DevOps (optional)</SectionHeader>
      <Field label="PAT">
        <MaskedField
          value={values.pat}
          placeholder="Personal Access Token"
          onChange={(v) => set("pat", v)}
        />
      </Field>
      <Field label="Organization">
        <input
          type="text"
          className="tt-input"
          placeholder="e.g. pwc-us-adv-digital"
          value={values.organization}
          onChange={(e) => set("organization", e.target.value)}
        />
      </Field>
      <p className="pl-[152px] text-xs text-muted-foreground">
        Connect Azure DevOps to browse boards and generate test cases from ADO
        work items. Provide both PAT and Organization, or leave blank to use
        JIRA only.
      </p>
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
    </div>
  );
}
