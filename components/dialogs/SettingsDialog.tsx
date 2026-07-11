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
    organization: settings?.organization ?? "",
    project_prefix: settings?.project_prefix ?? "",
    pat: settings?.has_pat ? "************" : "",
    jira_url: settings?.jira_url ?? "",
    jira_user: settings?.jira_user ?? "",
    jira_pat: settings?.has_jira_pat ? "************" : "",
    jira_project_prefix: settings?.jira_project_prefix ?? "",
  });
  const [status, setStatus] = useState("");
  const [busy, setBusy] = useState(false);

  // Test ADO — verifies the ADO PAT AND the backend-managed AI API, matching
  // the desktop "Test ADO" button ("Testing ADO + AI API connections...").
  const testAdo = async () => {
    setBusy(true);
    setStatus("Testing ADO + AI API connections...");
    try {
      await agent.saveSettings(toPayload(values));
      const [ado, llm] = await Promise.all([
        agent.verifyAdo(),
        agent.verifyLlm(),
      ]);
      const parts = [
        ado.ok ? "[OK] ADO connected" : `[FAIL] ADO: ${ado.detail}`,
        llm.ok ? "[OK] AI API reachable" : `[FAIL] AI API: ${llm.detail}`,
      ];
      setStatus(parts.join("  ·  "));
    } catch (e) {
      setStatus(`Failed: ${(e as Error).message}`);
    } finally {
      setBusy(false);
    }
  };

  // Test Jira — verifies the stored JIRA credentials only.
  const testJira = async () => {
    setBusy(true);
    setStatus("Testing Jira connection...");
    try {
      await agent.saveSettings(toPayload(values));
      const r = await agent.verifyJira();
      setStatus(r.ok ? "[OK] Jira connected" : `[FAIL] Jira: ${r.detail}`);
    } catch (e) {
      setStatus(`Failed: ${(e as Error).message}`);
    } finally {
      setBusy(false);
    }
  };

  const save = async () => {
    // ADO is optional (like JIRA): allow saving with it blank. Only block the
    // half-filled case, which can't authenticate and is almost always a typo.
    const pat = values.pat.trim();
    const org = values.organization.trim();
    if ((pat && !org) || (!pat && org)) {
      setStatus("Enter both Azure DevOps PAT and Organization, or leave both blank.");
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
          <button className="tt-btn-ghost" onClick={testAdo} disabled={busy}>
            Test ADO
          </button>
          <button className="tt-btn-ghost" onClick={testJira} disabled={busy}>
            Test Jira
          </button>
          {status && (
            <span className="mr-auto ml-2 text-xs text-muted-foreground">
              {status}
            </span>
          )}
          {!status && <span className="mr-auto" />}
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
