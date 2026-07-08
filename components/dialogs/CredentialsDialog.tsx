"use client";

import { useEffect, useState } from "react";
import { Plus, Pencil, Trash2, KeyRound, ShieldCheck, X } from "lucide-react";
import { Modal } from "@/components/ui/modal";
import { agent, type MaskedCredential } from "@/lib/agent-client";
import { useAppState } from "@/lib/app-state";

const LOGIN_METHODS = ["form", "sso", "basic_auth", "oauth"];

interface FormState {
  env: string;
  login_url: string;
  user_id: string;
  password: string;
  login_method: string;
  notes: string;
  ai_instructions: string;
}

const EMPTY_FORM: FormState = {
  env: "",
  login_url: "",
  user_id: "",
  password: "",
  login_method: "form",
  notes: "",
  ai_instructions: "",
};

/**
 * Credentials Vault - web port of the desktop CredentialDialog.
 * Manages per-project test-environment credentials used by the E2E runner.
 * Passwords are stored encrypted on the agent host and are NEVER returned to
 * the browser: editing shows an empty password field labelled "leave blank to
 * keep", and the table only shows whether a secret is on file.
 */
export function CredentialsDialog({ onClose }: { onClose: () => void }) {
  const { currentProject, displayName, pushLog } = useAppState();

  const [creds, setCreds] = useState<MaskedCredential[]>([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  // null = form hidden; "" env = adding new; non-empty env = editing that entry
  const [editingEnv, setEditingEnv] = useState<string | null>(null);
  const [form, setForm] = useState<FormState>(EMPTY_FORM);

  const isEditing = editingEnv !== null && editingEnv !== "";

  useEffect(() => {
    let cancelled = false;
    if (!currentProject) {
      setLoading(false);
      return;
    }
    (async () => {
      try {
        const list = await agent.listCredentials(currentProject);
        if (!cancelled) setCreds(list);
      } catch (e) {
        if (!cancelled)
          setError(e instanceof Error ? e.message : "Failed to load credentials");
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [currentProject]);

  const openAdd = () => {
    setForm(EMPTY_FORM);
    setEditingEnv("");
    setError("");
  };

  const openEdit = (c: MaskedCredential) => {
    setForm({
      env: c.env,
      login_url: c.login_url,
      user_id: c.user_id,
      password: "",
      login_method: c.login_method || "form",
      notes: c.notes,
      ai_instructions: c.ai_instructions,
    });
    setEditingEnv(c.env);
    setError("");
  };

  const cancelForm = () => {
    setEditingEnv(null);
    setForm(EMPTY_FORM);
    setError("");
  };

  const save = async () => {
    if (!currentProject) return;
    if (!form.env.trim()) return setError("Environment name is required.");
    if (!form.login_url.trim()) return setError("Login URL is required.");
    if (!form.user_id.trim()) return setError("Username is required.");
    setBusy(true);
    setError("");
    try {
      const list = await agent.upsertCredential(currentProject, {
        env: form.env.trim(),
        login_url: form.login_url.trim(),
        user_id: form.user_id.trim(),
        password: form.password,
        // When editing with a blank password, keep the stored secret.
        keep_password: isEditing && form.password === "",
        login_method: form.login_method,
        notes: form.notes.trim(),
        ai_instructions: form.ai_instructions.trim(),
      });
      setCreds(list);
      pushLog("INFO", `Saved credential for "${form.env.trim()}".`);
      cancelForm();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to save credential");
    } finally {
      setBusy(false);
    }
  };

  const remove = async (env: string) => {
    if (!currentProject) return;
    if (
      !window.confirm(`Delete the credential for environment "${env}"?`)
    )
      return;
    setBusy(true);
    setError("");
    try {
      const list = await agent.deleteCredential(currentProject, env);
      setCreds(list);
      pushLog("INFO", `Deleted credential for "${env}".`);
      if (editingEnv === env) cancelForm();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to delete credential");
    } finally {
      setBusy(false);
    }
  };

  const footer = (
    <div className="flex w-full items-center justify-between">
      <span className="text-xs text-[var(--tt-text-muted)]">
        Stored encrypted on this machine. Passwords never leave the agent.
      </span>
      <button className="tt-btn-ghost" onClick={onClose}>
        Close
      </button>
    </div>
  );

  return (
    <Modal
      open
      title="Test Credentials Vault"
      onClose={onClose}
      footer={footer}
      width={860}
    >
      <div className="flex flex-col gap-4">
        <div className="flex items-center justify-between gap-3">
          <p className="text-sm text-[var(--tt-text-secondary)]">
            {currentProject ? (
              <>
                Credentials for{" "}
                <span className="font-medium text-[var(--tt-text-bright)]">
                  {displayName(currentProject)}
                </span>
              </>
            ) : (
              "Select a project to manage its test credentials."
            )}
          </p>
          <button
            className="tt-btn-primary inline-flex items-center gap-1.5"
            disabled={!currentProject || busy || editingEnv !== null}
            onClick={openAdd}
          >
            <Plus className="h-4 w-4" strokeWidth={2} />
            Add credential
          </button>
        </div>

        {error && (
          <div
            className="rounded-md border px-3 py-2 text-sm"
            style={{
              borderColor: "var(--tt-danger)",
              color: "var(--tt-danger-hover)",
              background: "var(--tt-surface-high)",
            }}
          >
            {error}
          </div>
        )}

        {/* Credential table */}
        <div className="overflow-hidden rounded-lg border border-[var(--tt-outline)]">
          <table className="w-full text-left text-sm">
            <thead className="bg-[var(--tt-surface-high)] text-xs uppercase tracking-wide text-[var(--tt-text-muted)]">
              <tr>
                <th className="px-3 py-2">Environment</th>
                <th className="px-3 py-2">URL</th>
                <th className="px-3 py-2">Username</th>
                <th className="px-3 py-2">Method</th>
                <th className="px-3 py-2 text-center">Secret</th>
                <th className="px-3 py-2 text-right">Actions</th>
              </tr>
            </thead>
            <tbody>
              {loading ? (
                <tr>
                  <td
                    colSpan={6}
                    className="px-3 py-6 text-center text-[var(--tt-text-muted)]"
                  >
                    Loading...
                  </td>
                </tr>
              ) : creds.length === 0 ? (
                <tr>
                  <td
                    colSpan={6}
                    className="px-3 py-6 text-center text-[var(--tt-text-muted)]"
                  >
                    No credentials stored yet.
                  </td>
                </tr>
              ) : (
                creds.map((c) => (
                  <tr
                    key={c.env}
                    className="border-t border-[var(--tt-outline)]"
                  >
                    <td className="px-3 py-2 font-medium text-[var(--tt-text-bright)]">
                      {c.env}
                    </td>
                    <td className="max-w-[16rem] truncate px-3 py-2 text-[var(--tt-text-secondary)]">
                      {c.login_url}
                    </td>
                    <td className="px-3 py-2 text-[var(--tt-text-secondary)]">
                      {c.user_id}
                    </td>
                    <td className="px-3 py-2 text-[var(--tt-text-muted)]">
                      {c.login_method}
                    </td>
                    <td className="px-3 py-2 text-center">
                      {c.has_password ? (
                        <ShieldCheck
                          className="mx-auto h-4 w-4 text-[var(--tt-success)]"
                          strokeWidth={2}
                          aria-label="Password stored"
                        />
                      ) : (
                        <span className="text-xs text-[var(--tt-text-muted)]">
                          none
                        </span>
                      )}
                    </td>
                    <td className="px-3 py-2">
                      <div className="flex justify-end gap-1">
                        <button
                          className="tt-btn-ghost h-7 w-7 !p-0"
                          title="Edit"
                          disabled={busy || editingEnv !== null}
                          onClick={() => openEdit(c)}
                        >
                          <Pencil className="h-3.5 w-3.5" strokeWidth={2} />
                        </button>
                        <button
                          className="tt-btn-ghost h-7 w-7 !p-0"
                          title="Delete"
                          disabled={busy || editingEnv !== null}
                          onClick={() => remove(c.env)}
                        >
                          <Trash2 className="h-3.5 w-3.5" strokeWidth={2} />
                        </button>
                      </div>
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>

        {/* Add / edit form */}
        {editingEnv !== null && (
          <div className="rounded-lg border border-[var(--tt-outline)] bg-[var(--tt-surface-high)] p-4">
            <div className="mb-3 flex items-center gap-2 text-sm font-medium text-[var(--tt-text-bright)]">
              <KeyRound className="h-4 w-4" strokeWidth={2} />
              {isEditing ? `Edit "${editingEnv}"` : "New credential"}
            </div>
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
              <label className="flex flex-col gap-1 text-xs text-[var(--tt-text-secondary)]">
                Environment
                <input
                  className="tt-input"
                  placeholder="DEV, TEST, PROD"
                  value={form.env}
                  disabled={isEditing}
                  onChange={(e) => setForm({ ...form, env: e.target.value })}
                />
              </label>
              <label className="flex flex-col gap-1 text-xs text-[var(--tt-text-secondary)]">
                Login method
                <select
                  className="tt-input"
                  value={form.login_method}
                  onChange={(e) =>
                    setForm({ ...form, login_method: e.target.value })
                  }
                >
                  {LOGIN_METHODS.map((m) => (
                    <option key={m} value={m}>
                      {m}
                    </option>
                  ))}
                </select>
              </label>
              <label className="flex flex-col gap-1 text-xs text-[var(--tt-text-secondary)] sm:col-span-2">
                Login URL
                <input
                  className="tt-input"
                  placeholder="https://app.example.com/login"
                  value={form.login_url}
                  onChange={(e) =>
                    setForm({ ...form, login_url: e.target.value })
                  }
                />
              </label>
              <label className="flex flex-col gap-1 text-xs text-[var(--tt-text-secondary)]">
                Username
                <input
                  className="tt-input"
                  placeholder="username or email"
                  value={form.user_id}
                  onChange={(e) => setForm({ ...form, user_id: e.target.value })}
                />
              </label>
              <label className="flex flex-col gap-1 text-xs text-[var(--tt-text-secondary)]">
                Password
                <input
                  type="password"
                  className="tt-input"
                  placeholder={
                    isEditing ? "leave blank to keep" : "stored encrypted"
                  }
                  value={form.password}
                  onChange={(e) =>
                    setForm({ ...form, password: e.target.value })
                  }
                />
              </label>
              <label className="flex flex-col gap-1 text-xs text-[var(--tt-text-secondary)] sm:col-span-2">
                AI login instructions (optional)
                <textarea
                  className="tt-input min-h-[72px] resize-y"
                  placeholder="e.g. Click the SSO button first, then use corporate login"
                  value={form.ai_instructions}
                  onChange={(e) =>
                    setForm({ ...form, ai_instructions: e.target.value })
                  }
                />
              </label>
            </div>
            <div className="mt-3 flex justify-end gap-2">
              <button
                className="tt-btn-ghost inline-flex items-center gap-1.5"
                disabled={busy}
                onClick={cancelForm}
              >
                <X className="h-4 w-4" strokeWidth={2} />
                Cancel
              </button>
              <button
                className="tt-btn-primary"
                disabled={busy}
                onClick={save}
              >
                {busy ? "Saving..." : "Save credential"}
              </button>
            </div>
          </div>
        )}
      </div>
    </Modal>
  );
}
