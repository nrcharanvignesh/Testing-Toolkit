"use client";

import { useEffect, useRef, useState } from "react";
import { ChevronDown } from "lucide-react";
import {
  agent,
  type ModelInfo,
  type SaveSettingsPayload,
} from "@/lib/agent-client";

// Mirrors ConnectionFields._SEED_MODELS in the desktop app.
const SEED_MODELS = [
  "bedrock.anthropic.claude-opus-4-6",
  "bedrock.anthropic.claude-sonnet-4-6",
  "bedrock.anthropic.claude-haiku-4-5",
];

interface ModelGroup {
  provider: string;
  items: ModelInfo[];
}

/** Group a flat ModelInfo list into ordered provider sections, preserving the
 *  server's ordering (the backend already sorts via group_models_by_provider). */
function groupModels(list: ModelInfo[]): ModelGroup[] {
  const order: string[] = [];
  const map = new Map<string, ModelInfo[]>();
  for (const m of list) {
    if (!map.has(m.provider)) {
      map.set(m.provider, []);
      order.push(m.provider);
    }
    map.get(m.provider)!.push(m);
  }
  return order.map((provider) => ({ provider, items: map.get(provider)! }));
}

function seedGroups(): ModelGroup[] {
  return [
    {
      provider: "Anthropic",
      items: SEED_MODELS.map((id) => ({ id, provider: "Anthropic", label: id })),
    },
  ];
}

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
    model: v.model,
    fast_model: v.fast_model,
    fallback_model: v.fallback_model,
    organization: v.organization,
    project_prefix: v.project_prefix,
  };
  // Base URL is masked like a secret (desktop T02): only send it when the user
  // typed a fresh value, otherwise the backend keeps the stored URL.
  if (v.base_url && v.base_url !== MASK) p.base_url = v.base_url;
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

/**
 * Editable model combobox that mirrors the desktop QComboBox built in
 * ConnectionFields._populate_model_combos(): a free-text field plus a dropdown
 * grouped by provider, with bold, non-selectable provider headers and an
 * optional leading blank row (for fast/fallback "reuse primary" selection).
 */
function ModelCombo({
  value,
  onChange,
  groups,
  allowBlank,
  placeholder,
  title,
}: {
  value: string;
  onChange: (v: string) => void;
  groups: ModelGroup[];
  allowBlank?: boolean;
  placeholder?: string;
  title?: string;
}) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onDoc = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) {
        setOpen(false);
      }
    };
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, [open]);

  const pick = (id: string) => {
    onChange(id);
    setOpen(false);
  };

  return (
    <div ref={ref} className="relative flex-1" title={title}>
      <div className="flex">
        <input
          className="tt-input w-full !rounded-r-none"
          placeholder={placeholder}
          value={value}
          onChange={(e) => onChange(e.target.value)}
          onFocus={() => setOpen(true)}
        />
        <button
          type="button"
          aria-label="Show models"
          className="tt-btn-ghost shrink-0 !rounded-l-none !border-l-0 !px-2"
          onClick={() => setOpen((o) => !o)}
        >
          <ChevronDown className="h-4 w-4" />
        </button>
      </div>
      {open && (
        <div className="tt-dialog absolute z-[60] mt-1 max-h-64 w-full overflow-auto rounded-md border border-[#1e2128] py-1 shadow-2xl">
          {allowBlank && (
            <button
              type="button"
              className="tt-list-item block w-full truncate text-left text-sm italic text-muted-foreground"
              onClick={() => pick("")}
            >
              (reuse primary)
            </button>
          )}
          {groups.map((g) => (
            <div key={g.provider}>
              <div className="px-3 py-1 text-xs font-bold uppercase tracking-wide text-muted-foreground">
                -- {g.provider} --
              </div>
              {g.items.map((m) => (
                <button
                  key={m.id}
                  type="button"
                  title={m.label}
                  data-selected={m.id === value}
                  className="tt-list-item block w-full truncate text-left text-sm"
                  onClick={() => pick(m.id)}
                >
                  {m.id}
                </button>
              ))}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

export function ConnectionFields({
  values,
  setValues,
}: {
  values: ConnectionValues;
  setValues: React.Dispatch<React.SetStateAction<ConnectionValues>>;
}) {
  const [groups, setGroups] = useState<ModelGroup[]>(seedGroups());
  const [modelStatus, setModelStatus] = useState("");
  const [statusColor, setStatusColor] = useState<"muted" | "warn" | "success">(
    "muted"
  );
  const [fetching, setFetching] = useState(false);

  const set = (k: keyof ConnectionValues, v: string) =>
    setValues((prev) => ({ ...prev, [k]: v }));

  const fetchModels = async () => {
    if (!values.base_url) {
      setStatusColor("warn");
      setModelStatus("Base URL is required to fetch models.");
      return;
    }
    if (!values.api_key) {
      setStatusColor("warn");
      setModelStatus("Enter the API key to fetch models.");
      return;
    }
    setFetching(true);
    setStatusColor("muted");
    setModelStatus("Checking which models respond with 200 OK...");
    try {
      const list = await agent.listModels();
      if (list.length) {
        const g = groupModels(list);
        setGroups(g);
        setStatusColor("success");
        setModelStatus(
          `${list.length} working model(s) across ${g.length} provider(s) ` +
            `(only models that returned 200 OK are listed).`
        );
      } else {
        setStatusColor("warn");
        setModelStatus(
          "No models responded with 200 OK (check the base URL, key, and TLS mode)."
        );
      }
    } catch (e) {
      setStatusColor("warn");
      setModelStatus(`Could not fetch models: ${(e as Error).message}`);
    } finally {
      setFetching(false);
    }
  };

  const statusClass =
    statusColor === "warn"
      ? "text-[#d69e2e]"
      : statusColor === "success"
        ? "text-[#48bb78]"
        : "text-muted-foreground";

  // Auto-check on open (mirrors SettingsDialog._auto_check): if a key and base
  // URL are already saved, silently fetch the model list so the user lands on a
  // verified, populated dropdown.
  useEffect(() => {
    if (values.base_url && values.api_key === MASK) {
      void fetchModels();
    }
    // Run once on mount only.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

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
        <MaskedField
          value={values.base_url}
          placeholder="https://your-llm-api-endpoint.com"
          onChange={(v) => set("base_url", v)}
        />
      </Field>
      <Field label="Model">
        <div className="flex gap-2">
          <ModelCombo
            value={values.model}
            onChange={(v) => set("model", v)}
            groups={groups}
          />
          <button
            type="button"
            className="tt-btn-ghost shrink-0 !px-3 !py-1.5 text-xs"
            onClick={fetchModels}
            disabled={fetching}
            title="Query the API for available models and fill the dropdowns. Needs the API key and base URL above."
          >
            {fetching ? "Fetching..." : "Fetch models"}
          </button>
        </div>
      </Field>
      <Field label="Fast model">
        <ModelCombo
          value={values.fast_model}
          onChange={(v) => set("fast_model", v)}
          groups={groups}
          allowBlank
          placeholder="(reuse primary if blank)"
          title="Used for the Recursive Language Model retrieval steps. Leave blank to reuse the primary model."
        />
      </Field>
      <Field label="Fallback model">
        <ModelCombo
          value={values.fallback_model}
          onChange={(v) => set("fallback_model", v)}
          groups={groups}
          allowBlank
          placeholder="(safety fallback)"
          title="Safety fallback model used when the primary or fast model fails or is rate-limited."
        />
      </Field>
      {modelStatus && (
        <p className={`pl-[152px] text-xs ${statusClass}`}>{modelStatus}</p>
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
