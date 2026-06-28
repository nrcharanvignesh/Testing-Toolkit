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

/** Right-side status chip: colored dot + label (desktop footer G04). */
function Chip({
  label,
  ok,
  warn,
  pulse,
  title,
}: {
  label: string;
  ok: boolean;
  warn?: boolean;
  pulse?: boolean;
  title?: string;
}) {
  const color = ok ? "#1aab5c" : warn ? "#f59e0b" : "#5a5f6a";
  return (
    <span className="flex items-center gap-1.5" title={title}>
      <Dot color={color} pulse={pulse} />
      {label}
    </span>
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
  const { settings, kbState, kbMessage, boardLoading, projectsLoading } =
    useAppState();

  const hasOrg = !!settings?.organization;
  const hasKey = !!settings?.has_api_key;
  const connected = status === "connected";
  const working = boardLoading || projectsLoading;
  const hasTls = !!health?.tls_mode;

  // Left side: activity label + KB status (green when ready), matching desktop.
  const activity = working ? "Working" : "Idle";
  const kbReady = kbState === "ready";

  return (
    <footer className="tt-statusbar flex h-7 items-center justify-between px-3">
      <div className="flex items-center gap-2">
        <span className="text-[#8a8f99]">{activity}</span>
        <span
          title={kbMessage}
          style={{ color: kbReady ? "#1aab5c" : KB_COLOR[kbState] }}
          className="font-medium"
        >
          {kbMessage}
        </span>
      </div>
      <div className="flex items-center gap-4">
        <Chip
          label="App"
          ok={connected}
          pulse={!connected}
          title="Desktop agent connection"
        />
        <Chip
          label="ADO"
          ok={hasOrg && connected}
          warn={!hasOrg || !connected}
          title="Azure DevOps status"
        />
        <Chip
          label="API"
          ok={hasKey}
          warn={!hasKey}
          title={hasKey ? "LLM API key set" : "no API key (manual mode)"}
        />
        <Chip
          label="TLS"
          ok={hasTls}
          title={hasTls ? `TLS: ${health?.tls_mode}` : "TLS status"}
        />
        <Chip
          label="Activity"
          ok={connected}
          pulse={working}
          title="App activity"
        />
      </div>
    </footer>
  );
}
