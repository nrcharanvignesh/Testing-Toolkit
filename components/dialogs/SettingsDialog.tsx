"use client";

import { useState } from "react";
import { Modal } from "@/components/ui/modal";
import { agent } from "@/lib/agent-client";
import { useAppState } from "@/lib/app-state";
import { InstallationSection } from "./InstallationSection";
import { DiagnosticsSection } from "./DiagnosticsSection";
import {
  ConnectionFields,
  toPayload,
  useConnectionFields,
} from "./ConnectionFields";

export function SettingsDialog({ onClose }: { onClose: () => void }) {
  const { settings, setSettings, reloadProjects, pushLog } = useAppState();
  const { values, setValues } = useConnectionFields({
    base_url: settings?.base_url ? "************" : "",
    model: settings?.model ?? "",
    fast_model: settings?.fast_model ?? "",
    fallback_model: settings?.fallback_model ?? "",
    organization: settings?.organization ?? "",
    project_prefix: settings?.project_prefix ?? "",
    api_key: settings?.has_api_key ? "************" : "",
    pat: settings?.has_pat ? "************" : "",
  });
  const [status, setStatus] = useState("");
  const [busy, setBusy] = useState(false);

  const test = async () => {
    setBusy(true);
    setStatus("Testing connection...");
    try {
      await agent.saveSettings(toPayload(values));
      const r = await agent.verifyPat();
      setStatus(r.ok ? "Connection OK." : `Failed: ${r.detail}`);
    } catch (e) {
      setStatus(`Failed: ${(e as Error).message}`);
    } finally {
      setBusy(false);
    }
  };

  const save = async () => {
    if (!values.pat.trim() || !values.organization.trim()) {
      setStatus("PAT and Organization are required.");
      return;
    }
    setBusy(true);
    try {
      await agent.saveSettings(toPayload(values));
      const s = await agent.getSettings();
      setSettings(s);
      pushLog("INFO", "Settings saved; reloading projects.");
      reloadProjects();
      onClose();
    } catch (e) {
      setStatus(`Save failed: ${(e as Error).message}`);
      setBusy(false);
    }
  };

  return (
    <Modal
      open
      onClose={onClose}
      title="Testing Toolkit - Settings"
      width={680}
      footer={
        <>
          <button
            className="tt-btn-ghost mr-auto"
            onClick={test}
            disabled={busy}
          >
            Test Connection
          </button>
          {status && (
            <span className="mr-auto text-xs text-muted-foreground">
              {status}
            </span>
          )}
          <button className="tt-btn-ghost" onClick={onClose} disabled={busy}>
            Cancel
          </button>
          <button className="tt-btn-primary" onClick={save} disabled={busy}>
            {busy ? "Saving..." : "Save"}
          </button>
        </>
      }
    >
      <ConnectionFields values={values} setValues={setValues} />
      <InstallationSection />
      <DiagnosticsSection />
    </Modal>
  );
}
