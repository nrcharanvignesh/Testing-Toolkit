"use client";

import { useState, type ReactNode } from "react";
import { motion } from "framer-motion";
import { agent } from "@/lib/agent-client";
import { useAppState } from "@/lib/app-state";
import {
  ConnectionFields,
  toPayload,
  useConnectionFields,
} from "@/components/dialogs/ConnectionFields";

export function FirstRunGate({ children }: { children: ReactNode }) {
  const { settings, setSettings } = useAppState();

  if (settings?.configured) return <>{children}</>;

  return <SetupWizard onDone={(s) => setSettings(s)} />;
}

function SetupWizard({
  onDone,
}: {
  onDone: (s: Awaited<ReturnType<typeof agent.getSettings>>) => void;
}) {
  const { values, setValues } = useConnectionFields({
    base_url: "https://api.anthropic.com",
  });
  const [busy, setBusy] = useState(false);
  const [logs, setLogs] = useState<string[]>([]);
  const log = (m: string) => setLogs((p) => [...p, m]);

  const refresh = async () => {
    const s = await agent.getSettings();
    onDone(s);
  };

  const connect = async () => {
    if (!values.pat.trim() || !values.organization.trim()) {
      log("[ERROR] PAT and Organization are required.");
      return;
    }
    setBusy(true);
    setLogs([]);
    log("[INFO] Saving settings and connecting...");
    try {
      await agent.saveSettings(toPayload(values));
      const v = await agent.verifyPat();
      if (!v.ok) {
        log(`[ERROR] ADO connection failed: ${v.detail}`);
        setBusy(false);
        return;
      }
      log("[SUCCESS] ADO connected.");
      await refresh();
    } catch (e) {
      log(`[ERROR] Setup failed: ${(e as Error).message}`);
      setBusy(false);
    }
  };

  const skip = async () => {
    setBusy(true);
    try {
      await agent.saveSettings(toPayload(values));
    } catch {
      /* ignore */
    }
    await refresh().catch(() => setBusy(false));
  };

  return (
    <div className="flex h-full items-center justify-center overflow-auto bg-background px-6 py-8">
      <motion.div
        initial={{ opacity: 0, scale: 0.97 }}
        animate={{ opacity: 1, scale: 1 }}
        transition={{ duration: 0.2 }}
        className="tt-dialog w-full max-w-2xl p-7"
      >
        <h2 className="text-lg font-bold tracking-tight text-white">
          Testing Toolkit — one-time setup
        </h2>
        <p className="mt-1 text-sm leading-relaxed text-muted-foreground">
          Enter your LLM API and Azure DevOps details. On{" "}
          <b className="text-[#bfc4cc]">Save &amp; Connect</b> the app stores
          credentials, verifies the PAT, and loads your projects. No API key?
          You can still proceed and use Manual Mode.
        </p>

        <div className="mt-5">
          <ConnectionFields values={values} setValues={setValues} />
        </div>

        {logs.length > 0 && (
          <div className="mt-4 max-h-28 overflow-auto rounded-lg border border-[#2d313c] bg-[#0d1017] p-3 font-mono text-xs">
            {logs.map((l, i) => (
              <div key={i} className="text-[#bfc4cc]">
                {l}
              </div>
            ))}
          </div>
        )}

        <div className="mt-6 flex justify-end gap-2">
          <button className="tt-btn-ghost" onClick={skip} disabled={busy}>
            Skip (manual mode)
          </button>
          <button
            className="tt-btn-success"
            onClick={connect}
            disabled={busy}
          >
            {busy ? "Connecting..." : "Save & Connect"}
          </button>
        </div>
      </motion.div>
    </div>
  );
}
