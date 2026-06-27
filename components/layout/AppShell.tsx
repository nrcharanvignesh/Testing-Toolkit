"use client";

import { useEffect, useState } from "react";
import { motion } from "framer-motion";
import { useAgent } from "@/lib/agent-context";
import { agent, type SettingsResponse } from "@/lib/agent-client";
import { SetupWizard } from "@/components/onboarding/SetupWizard";
import { ActivityBar } from "./ActivityBar";
import { StatusBar } from "./StatusBar";

type View = "boards" | "generate" | "kb" | "defects" | "settings";

export function AppShell() {
  const { health } = useAgent();
  const [settings, setSettings] = useState<SettingsResponse | null>(null);
  const [showSetup, setShowSetup] = useState(false);
  const [activeView, setActiveView] = useState<View>("boards");
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    agent
      .getSettings()
      .then((s) => {
        setSettings(s);
        if (!s.configured) setShowSetup(true);
        setLoading(false);
      })
      .catch(() => setLoading(false));
  }, []);

  if (loading) {
    return (
      <div className="flex h-screen items-center justify-center">
        <div className="h-6 w-6 animate-spin rounded-full border-2 border-muted-foreground border-t-primary" />
      </div>
    );
  }

  if (showSetup) {
    return (
      <SetupWizard
        onComplete={() => {
          setShowSetup(false);
          agent.getSettings().then(setSettings);
        }}
      />
    );
  }

  return (
    <div className="flex h-screen flex-col overflow-hidden">
      <div className="flex flex-1 overflow-hidden">
        <ActivityBar active={activeView} onChange={setActiveView} />
        <main className="flex-1 overflow-auto p-6">
          <motion.div
            key={activeView}
            initial={{ opacity: 0, x: 8 }}
            animate={{ opacity: 1, x: 0 }}
            transition={{ duration: 0.2 }}
          >
            {activeView === "boards" && <BoardsPlaceholder />}
            {activeView === "generate" && <PlaceholderView title="Test Case Generation" />}
            {activeView === "kb" && <PlaceholderView title="Knowledge Base" />}
            {activeView === "defects" && <PlaceholderView title="Bulk Defects" />}
            {activeView === "settings" && (
              <button
                onClick={() => setShowSetup(true)}
                className="text-sm text-primary underline"
              >
                Open Settings
              </button>
            )}
          </motion.div>
        </main>
      </div>
      <StatusBar user={health?.user} machine={health?.machine} modelsLoaded={health?.models_loaded} />
    </div>
  );
}

function BoardsPlaceholder() {
  return (
    <div className="flex flex-col gap-4">
      <h1 className="text-2xl font-bold tracking-tight">Boards</h1>
      <p className="text-muted-foreground">
        Select a project and team to view work items.
      </p>
      <div className="grid grid-cols-3 gap-4">
        {Array.from({ length: 6 }).map((_, i) => (
          <div
            key={i}
            className="h-32 animate-pulse rounded-xl border border-border/50 bg-muted/20"
          />
        ))}
      </div>
    </div>
  );
}

function PlaceholderView({ title }: { title: string }) {
  return (
    <div className="flex flex-col gap-4">
      <h1 className="text-2xl font-bold tracking-tight">{title}</h1>
      <p className="text-muted-foreground">Coming soon.</p>
    </div>
  );
}
