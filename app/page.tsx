"use client";

import { useAgent } from "@/lib/agent-context";
import { OnboardingScreen } from "@/components/onboarding/OnboardingScreen";
import { AppShell } from "@/components/layout/AppShell";

export default function Home() {
  const { status } = useAgent();

  if (status === "connecting") {
    return <LoadingScreen />;
  }

  if (status === "offline") {
    return <OnboardingScreen />;
  }

  return <AppShell />;
}

function LoadingScreen() {
  return (
    <div className="flex h-screen items-center justify-center bg-background">
      <div className="flex flex-col items-center gap-4">
        <div className="h-8 w-8 animate-spin rounded-full border-2 border-muted-foreground border-t-primary" />
        <p className="text-sm text-muted-foreground">Connecting to agent...</p>
      </div>
    </div>
  );
}
