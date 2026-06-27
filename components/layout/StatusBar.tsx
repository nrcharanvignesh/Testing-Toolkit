"use client";

import { useAgent } from "@/lib/agent-context";

interface StatusBarProps {
  user?: string;
  machine?: string;
  modelsLoaded?: boolean;
}

export function StatusBar({ user, machine, modelsLoaded }: StatusBarProps) {
  const { status } = useAgent();

  return (
    <footer className="flex h-7 items-center justify-between border-t border-border/50 bg-muted/20 px-4 text-xs text-muted-foreground">
      <div className="flex items-center gap-3">
        <span className="flex items-center gap-1.5">
          <span
            className={`h-2 w-2 rounded-full ${
              status === "connected" ? "bg-emerald-500" : "bg-amber-500 animate-pulse"
            }`}
          />
          {status === "connected" ? "Agent connected" : "Agent offline"}
        </span>
        {user && (
          <span className="text-muted-foreground/60">
            {user}@{machine}
          </span>
        )}
      </div>
      <div className="flex items-center gap-3">
        {modelsLoaded !== undefined && (
          <span className="flex items-center gap-1.5">
            <span
              className={`h-2 w-2 rounded-full ${
                modelsLoaded ? "bg-emerald-500" : "bg-amber-500"
              }`}
            />
            {modelsLoaded ? "Models ready" : "Loading models..."}
          </span>
        )}
        <span className="text-muted-foreground/40">Testing Toolkit v2.0</span>
      </div>
    </footer>
  );
}
