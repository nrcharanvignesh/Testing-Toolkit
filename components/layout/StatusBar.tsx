"use client";

import { useAgent } from "@/lib/agent-context";
import { useAppState, type KbState } from "@/lib/app-state";

function Dot({ color, pulse }: { color: string; pulse?: boolean }) {
  return (
    <span
      className={`h-2 w-2 rounded-full ${pulse ? "animate-pulse" : ""}`}
      style={{ background: color }}
    />
  );
}

const KB_COLOR: Record<KbState, string> = {
  none: "#e53e3e",
  indexing: "#f59e0b",
  ready: "#1aab5c",
  error: "#e53e3e",
};

export function StatusBar() {
  const { status, health } = useAgent();
  const { settings, kbState, kbMessage } = useAppState();

  const hasOrg = !!settings?.organization;
  const hasKey = !!settings?.has_api_key;
  const connected = status === "connected";

  return (
    <footer className="tt-statusbar flex h-7 items-center justify-between px-3">
      <div className="flex items-center gap-4">
        <span className="flex items-center gap-1.5">
          <Dot color={connected ? "#1aab5c" : "#f59e0b"} pulse={!connected} />
          {connected
            ? hasOrg
              ? `Connected · org: ${settings?.organization}`
              : "Connected"
            : "Agent offline"}
        </span>
        <span className="flex items-center gap-1.5" title="LLM API status">
          <Dot color={hasKey ? "#1aab5c" : "#3b82f6"} />
          {hasKey ? "API key set" : "no API key (manual mode)"}
        </span>
        <span className="flex items-center gap-1.5" title="Azure DevOps status">
          <Dot color={hasOrg && connected ? "#1aab5c" : "#f59e0b"} />
          ADO
        </span>
      </div>
      <div className="flex items-center gap-4">
        <span className="flex items-center gap-1.5" title={kbMessage}>
          <Dot color={KB_COLOR[kbState]} pulse={kbState === "indexing"} />
          {kbMessage}
        </span>
        {health?.tls_mode && (
          <span className="text-[#5a5f6a]">TLS: {health.tls_mode}</span>
        )}
        <span className="text-[#5a5f6a]">
          Testing Toolkit v{health?.version ?? "2.0.0"}
        </span>
      </div>
    </footer>
  );
}
