"use client";

import { useEffect, useRef, useState } from "react";
import { useAppState, type LogLine } from "@/lib/app-state";
import { getPreferences, setSizePref } from "@/lib/preferences";
import { ResizeHandle } from "@/components/ui/resizer";

const LEVEL_COLOR: Record<LogLine["level"], string> = {
  INFO: "var(--tt-text-muted)",
  SUCCESS: "var(--tt-success)",
  WARN: "var(--tt-warn)",
  ERROR: "var(--tt-danger)",
};

export function LogPanel() {
  const { log } = useAppState();
  const endRef = useRef<HTMLDivElement | null>(null);
  const [height, setHeight] = useState(() => getPreferences().sizes.logHeight);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [log]);

  // Log panel: no in-panel header or trash/close icons — just the scrolling
  // log view (Hide lives in the action strip). The top edge is a free-hand
  // resize handle whose height persists to preferences.
  return (
    <div className="flex shrink-0 flex-col" style={{ height }}>
      <ResizeHandle
        axis="y"
        value={height}
        min={100}
        max={600}
        invert
        onChange={setHeight}
        onCommit={(v) => setSizePref("logHeight", v)}
        ariaLabel="Resize activity log"
      />
      <div className="tt-card flex min-h-0 flex-1 flex-col overflow-hidden p-0">
        <div className="min-h-0 flex-1 overflow-auto bg-[var(--tt-surface-deepest)] px-3 py-2 font-mono text-xs leading-relaxed">
        {log.length === 0 ? (
          <p className="text-[var(--tt-text-faint)]">No activity yet.</p>
        ) : (
          log.map((line) => {
            // Log lines are "[LEVEL] text" with no timestamp.
            // The agent already emits lines like "[INFO] ..."; strip a leading
            // duplicate level tag so we render exactly one.
            const text = line.text.replace(
              /^\[(INFO|SUCCESS|WARN|WARNING|ERROR)\]\s*/i,
              ""
            );
            return (
              <div key={line.id} className="whitespace-pre-wrap">
                <span style={{ color: LEVEL_COLOR[line.level] }}>
                  [{line.level}]
                </span>{" "}
                <span className="text-[var(--tt-text-secondary)]">{text}</span>
              </div>
            );
          })
        )}
          <div ref={endRef} />
        </div>
      </div>
    </div>
  );
}
