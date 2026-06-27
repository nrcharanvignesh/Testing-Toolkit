"use client";

import { useState } from "react";
import { motion } from "framer-motion";
import { agent } from "@/lib/agent-client";

interface SetupWizardProps {
  onComplete: () => void;
}

type Step = "credentials" | "verify" | "done";

export function SetupWizard({ onComplete }: SetupWizardProps) {
  const [step, setStep] = useState<Step>("credentials");
  const [pat, setPat] = useState("");
  const [org, setOrg] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [baseUrl, setBaseUrl] = useState("https://api.anthropic.com");
  const [error, setError] = useState("");
  const [verifying, setVerifying] = useState(false);

  async function handleSave() {
    if (!pat.trim() || !org.trim()) {
      setError("PAT and Organization are required");
      return;
    }
    setError("");
    setVerifying(true);
    setStep("verify");

    try {
      await agent.saveSettings({
        pat: pat.trim(),
        organization: org.trim(),
        base_url: baseUrl.trim(),
        ...(apiKey.trim() ? { api_key: apiKey.trim() } : {}),
      });

      const result = await agent.verifyPat();
      if (!result.ok) {
        setError(`PAT verification failed: ${result.detail}`);
        setStep("credentials");
        setVerifying(false);
        return;
      }

      setStep("done");
      setTimeout(onComplete, 1200);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Connection failed");
      setStep("credentials");
    } finally {
      setVerifying(false);
    }
  }

  return (
    <div className="flex h-screen items-center justify-center bg-background px-6">
      <motion.div
        initial={{ opacity: 0, scale: 0.95 }}
        animate={{ opacity: 1, scale: 1 }}
        className="w-full max-w-md rounded-2xl border border-border/50 bg-card p-8 shadow-2xl shadow-black/20"
      >
        <div className="mb-6 flex flex-col gap-1">
          <h2 className="text-xl font-semibold">One-time Setup</h2>
          <p className="text-sm text-muted-foreground">
            Connect to Azure DevOps and your LLM provider.
          </p>
        </div>

        {step === "credentials" && (
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            className="flex flex-col gap-4"
          >
            {/* ADO Section */}
            <div className="flex flex-col gap-3">
              <label className="text-xs font-medium uppercase tracking-wider text-muted-foreground">
                Azure DevOps
              </label>
              <input
                type="password"
                placeholder="Personal Access Token"
                value={pat}
                onChange={(e) => setPat(e.target.value)}
                className="h-10 rounded-lg border border-border bg-background px-3 text-sm outline-none transition-colors focus:border-primary focus:ring-1 focus:ring-primary/20"
              />
              <input
                type="text"
                placeholder="Organization (e.g. pwc-us-adv-digital)"
                value={org}
                onChange={(e) => setOrg(e.target.value)}
                className="h-10 rounded-lg border border-border bg-background px-3 text-sm outline-none transition-colors focus:border-primary focus:ring-1 focus:ring-primary/20"
              />
            </div>

            {/* LLM Section */}
            <div className="flex flex-col gap-3">
              <label className="text-xs font-medium uppercase tracking-wider text-muted-foreground">
                LLM API (optional)
              </label>
              <input
                type="password"
                placeholder="API Key"
                value={apiKey}
                onChange={(e) => setApiKey(e.target.value)}
                className="h-10 rounded-lg border border-border bg-background px-3 text-sm outline-none transition-colors focus:border-primary focus:ring-1 focus:ring-primary/20"
              />
              <input
                type="text"
                placeholder="Base URL"
                value={baseUrl}
                onChange={(e) => setBaseUrl(e.target.value)}
                className="h-10 rounded-lg border border-border bg-background px-3 text-sm outline-none transition-colors focus:border-primary focus:ring-1 focus:ring-primary/20"
              />
            </div>

            {error && (
              <p className="text-sm text-red-400">{error}</p>
            )}

            <div className="mt-2 flex gap-3">
              <button
                onClick={handleSave}
                className="flex-1 rounded-lg bg-primary px-4 py-2.5 text-sm font-medium text-primary-foreground transition-colors hover:bg-primary/90"
              >
                Save & Connect
              </button>
              <button
                onClick={onComplete}
                className="rounded-lg border border-border px-4 py-2.5 text-sm text-muted-foreground transition-colors hover:bg-muted"
              >
                Skip
              </button>
            </div>
          </motion.div>
        )}

        {step === "verify" && (
          <div className="flex flex-col items-center gap-4 py-8">
            <div className="h-8 w-8 animate-spin rounded-full border-2 border-muted-foreground border-t-primary" />
            <p className="text-sm text-muted-foreground">Verifying connection...</p>
          </div>
        )}

        {step === "done" && (
          <motion.div
            initial={{ opacity: 0, scale: 0.9 }}
            animate={{ opacity: 1, scale: 1 }}
            className="flex flex-col items-center gap-4 py-8"
          >
            <div className="flex h-12 w-12 items-center justify-center rounded-full bg-emerald-500/10">
              <svg
                className="h-6 w-6 text-emerald-500"
                fill="none"
                viewBox="0 0 24 24"
                strokeWidth={2}
                stroke="currentColor"
              >
                <path strokeLinecap="round" strokeLinejoin="round" d="m4.5 12.75 6 6 9-13.5" />
              </svg>
            </div>
            <p className="text-sm font-medium">Connected successfully</p>
          </motion.div>
        )}
      </motion.div>
    </div>
  );
}
