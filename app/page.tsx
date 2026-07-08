"use client";

import { useEffect, useState } from "react";
import { useAgent } from "@/lib/agent-context";
import { agent, type SettingsResponse } from "@/lib/agent-client";
import { AppStateProvider } from "@/lib/app-state";
import { OnboardingScreen } from "@/components/onboarding/OnboardingScreen";
import { AppShell } from "@/components/layout/AppShell";
import { FirstRunGate } from "@/components/onboarding/FirstRunGate";
import { getPreferences, setPendingReinstallPref } from "@/lib/preferences";

export default function Home() {
  const { status } = useAgent();
  // Read the persisted reinstall flag once on mount. When set, we force the
  // Step 1 download/install screen even if the old agent is still connected.
  const [reinstalling, setReinstalling] = useState(false);
  useEffect(() => {
    setReinstalling(getPreferences().pendingReinstall);
  }, []);

  if (reinstalling) {
    return (
      <OnboardingScreen
        reinstall
        onReinstallComplete={() => {
          setPendingReinstallPref(false);
          setReinstalling(false);
        }}
        onReinstallCancel={() => {
          setPendingReinstallPref(false);
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
      <FirstRunGate>
        <AppShell />
      </FirstRunGate>
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
