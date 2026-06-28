"use client";

import { useEffect, useRef } from "react";
import { useAppState, type LogLine } from "@/lib/app-state";

const LEVEL_COLOR: Record<LogLine["level"], string> = {
  INFO: "#8a8f99",
  SUCCESS: "#1aab5c",
  WARN: "#f59e0b",
  ERROR: "#e53e3e",
};

export function LogPanel() {
  const { log } = useAppState();
  const endRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [log]);

  // Desktop LogProgressPanel: no in-panel header or trash/close icons — just
  // the scrolling log view (Hide lives in the action strip) — L02.
  return (
    <div className="tt-card flex h-44 flex-col overflow-hidden p-0">
      <div className="min-h-0 flex-1 overflow-auto bg-[#0d1017] px-3 py-2 font-mono text-xs leading-relaxed">
        {log.length === 0 ? (
          <p className="text-[#5a5f6a]">No activity yet.</p>
        ) : (
          log.map((line) => {
            // Desktop log lines are "[LEVEL] text" with no timestamp (L03).
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
                <span className="text-[#bfc4cc]">{text}</span>
              </div>
            );
          })
        )}
        <div ref={endRef} />
      </div>
    </div>
  );
}
