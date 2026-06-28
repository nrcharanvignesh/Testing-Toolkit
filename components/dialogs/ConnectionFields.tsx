"use client";

import { useState } from "react";
import { agent, type SaveSettingsPayload } from "@/lib/agent-client";

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
  });
  return { values, setValues };
}

export function toPayload(v: ConnectionValues): SaveSettingsPayload {
  const p: SaveSettingsPayload = {
    base_url: v.base_url,
    model: v.model,
    fast_model: v.fast_model,
    fallback_model: v.fallback_model,
    organization: v.organization,
    project_prefix: v.project_prefix,
  };
  if (v.api_key && v.api_key !== MASK) p.api_key = v.api_key;
  if (v.pat && v.pat !== MASK) p.pat = v.pat;
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
      <label className="text-right text-sm text-[#bfc4cc]">
        {label}:{required && <span className="text-[#e53e3e]"> *</span>}
      </label>
      {children}
    </div>
  );
}

/**
 * Masked secret field with an Edit button (desktop T02). Shows dots for a saved
 * value; clicking Edit clears it and enables typing a new value.
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
  return (
    <h3 className="tt-header mt-2 text-sm first:mt-0">{children}</h3>
  );
}

export function ConnectionFields({
  values,
  setValues,
}: {
  values: ConnectionValues;
  setValues: React.Dispatch<React.SetStateAction<ConnectionValues>>;
}) {
  const [models, setModels] = useState<string[]>(SEED_MODELS);
  const [modelStatus, setModelStatus] = useState("");
  const [fetching, setFetching] = useState(false);

  const set = (k: keyof ConnectionValues, v: string) =>
    setValues((prev) => ({ ...prev, [k]: v }));

  const fetchModels = async () => {
    if (!values.base_url) {
      setModelStatus("Base URL is required to fetch models.");
      return;
    }
    if (!values.api_key) {
      setModelStatus("Enter the API key to fetch models.");
      return;
    }
    setFetching(true);
    setModelStatus("Checking which models respond with 200 OK...");
    try {
      const list = await agent.listModels();
      if (list.length) {
        setModels(Array.from(new Set([...list, ...SEED_MODELS])));
        setModelStatus(`${list.length} working model(s) found.`);
      } else {
        setModelStatus("No models responded (check base URL, key, TLS).");
      }
    } catch (e) {
      setModelStatus(`Could not fetch models: ${(e as Error).message}`);
    } finally {
      setFetching(false);
    }
  };

  return (
    <div className="flex flex-col gap-3">
      <SectionHeader>LLM</SectionHeader>
      <Field label="API Key">
        <MaskedField
          value={values.api_key}
          placeholder="sk-ant-..."
          onChange={(v) => set("api_key", v)}
        />
      </Field>
      <Field label="Base URL">
        <input
          type="text"
          className="tt-input"
          placeholder="https://your-llm-api-endpoint.com"
          value={values.base_url}
          onChange={(e) => set("base_url", e.target.value)}
        />
      </Field>
      <Field label="Model">
        <div className="flex gap-2">
          <input
            list="tt-models"
            className="tt-input flex-1"
            value={values.model}
            onChange={(e) => set("model", e.target.value)}
          />
          <button
            type="button"
            className="tt-btn-ghost shrink-0 !px-3 !py-1.5 text-xs"
            onClick={fetchModels}
            disabled={fetching}
          >
            {fetching ? "Fetching..." : "Fetch models"}
          </button>
        </div>
      </Field>
      <Field label="Fast model">
        <input
          list="tt-models"
          className="tt-input"
          placeholder="(reuse primary if blank)"
          value={values.fast_model}
          onChange={(e) => set("fast_model", e.target.value)}
        />
      </Field>
      <Field label="Fallback model">
        <input
          list="tt-models"
          className="tt-input"
          placeholder="(safety fallback)"
          value={values.fallback_model}
          onChange={(e) => set("fallback_model", e.target.value)}
        />
      </Field>
      <datalist id="tt-models">
        {models.map((m) => (
          <option key={m} value={m} />
        ))}
      </datalist>
      {modelStatus && (
        <p className="pl-[152px] text-xs text-muted-foreground">{modelStatus}</p>
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
      <p className="pl-[152px] text-xs text-[#e53e3e]">* required</p>

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
    </div>
  );
}
