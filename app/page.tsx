"use client";

import { useEffect, useState } from "react";
import { useAgent } from "@/lib/agent-context";
import { agent, type SettingsResponse } from "@/lib/agent-client";
import { AppStateProvider } from "@/lib/app-state";
import { OnboardingScreen } from "@/components/onboarding/OnboardingScreen";
import { AppShell } from "@/components/layout/AppShell";
import { FirstRunGate } from "@/components/onboarding/FirstRunGate";

export default function Home() {
  const { status } = useAgent();

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
        <div className="h-8 w-8 animate-spin rounded-full border-2 border-[#2d313c] border-t-[#5ba8ff]" />
        <p className="text-sm text-muted-foreground">{label}</p>
      </div>
    </div>
  );
}
