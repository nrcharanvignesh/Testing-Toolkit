"use client";

import { useEffect, useState } from "react";
import { useAgent } from "@/lib/agent-context";
import { agent, type SettingsResponse } from "@/lib/agent-client";
import { AppStateProvider } from "@/lib/app-state";
import { OnboardingScreen } from "@/components/onboarding/OnboardingScreen";
import { AppShell } from "@/components/layout/AppShell";
import { getPreferences, setPendingReinstallPref } from "@/lib/preferences";
import { getReinstallReason, clearReinstallReason } from "@/lib/reinstall";
import type { ReinstallReason } from "@/lib/reinstall";

export default function Home() {
  const { status } = useAgent();
  const [reinstalling, setReinstalling] = useState(false);
  const [reason, setReason] = useState<ReinstallReason>("reinstall");
  useEffect(() => {
    const prefs = getPreferences();
    setReinstalling(prefs.pendingReinstall);
    if (prefs.pendingReinstall) setReason(getReinstallReason());
  }, []);

  // No page-level auto-dismiss — the OnboardingScreen owns the full
  // lifecycle (download → sawDrop → reconnect → onReinstallComplete). The
  // page only clears the flag when OnboardingScreen fires its callback.

  if (reinstalling) {
    return (
      <OnboardingScreen
        reinstall
        reason={reason}
        onReinstallComplete={() => {
          setPendingReinstallPref(false);
          clearReinstallReason();
          setReinstalling(false);
        }}
        onReinstallCancel={() => {
          setPendingReinstallPref(false);
          clearReinstallReason();
          setReinstalling(false);
        }}
      />
    );
  }

  if (status === "connecting") return <LoadingScreen label="Connecting to agent..." />;
  if (status === "offline") return <OnboardingScreen />;
  return <ConnectedApp />;
}

function ConnectedApp() {
  const [settings, setSettings] = useState<SettingsResponse | null | undefined>(
    undefined
  );

  useEffect(() => {
    agent
      .getSettings()
      .then(setSettings)
      .catch(() => setSettings(null));
  }, []);

  if (settings === undefined) return <LoadingScreen label="Loading settings..." />;

  return (
    <AppStateProvider initialSettings={settings}>
      <AppShell />
    </AppStateProvider>
  );
}

function LoadingScreen({ label }: { label: string }) {
  return (
    <div className="flex h-full items-center justify-center bg-background">
      <div className="flex flex-col items-center gap-4">
        <div className="h-8 w-8 animate-spin rounded-full border-2 border-[var(--tt-outline)] border-t-[var(--tt-primary)]" />
        <p className="text-sm text-muted-foreground">{label}</p>
      </div>
    </div>
  );
}
