"use client";

import { useEffect, useRef } from "react";
import { Trash2, X } from "lucide-react";
import { useAppState, type LogLine } from "@/lib/app-state";

const LEVEL_COLOR: Record<LogLine["level"], string> = {
  INFO: "#8a8f99",
  SUCCESS: "#1aab5c",
  WARN: "#f59e0b",
  ERROR: "#e53e3e",
};

export function LogPanel() {
  const { log, clearLog, setLogVisible } = useAppState();
  const endRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [log]);

  return (
    <div className="tt-card flex h-44 flex-col overflow-hidden p-0">
      <div className="flex items-center justify-between border-b border-[#1e2128] px-3 py-1.5">
        <span className="text-xs font-semibold uppercase tracking-wide text-[#bfc4cc]">
          Log / Progress
        </span>
        <div className="flex items-center gap-1">
          <button
            className="tt-btn-ghost h-7 w-7 !border-transparent !p-0"
            title="Clear log"
            onClick={clearLog}
          >
            <Trash2 className="h-3.5 w-3.5" />
          </button>
          <button
            className="tt-btn-ghost h-7 w-7 !border-transparent !p-0"
            title="Hide log"
            onClick={() => setLogVisible(false)}
          >
            <X className="h-3.5 w-3.5" />
          </button>
        </div>
      </div>
      <div className="min-h-0 flex-1 overflow-auto bg-[#0d1017] px-3 py-2 font-mono text-xs leading-relaxed">
        {log.length === 0 ? (
          <p className="text-[#5a5f6a]">No activity yet.</p>
        ) : (
          log.map((line) => (
            <div key={line.id} className="whitespace-pre-wrap">
              <span className="text-[#5a5f6a]">
                {new Date(line.ts).toLocaleTimeString()}{" "}
              </span>
              <span style={{ color: LEVEL_COLOR[line.level] }}>
                [{line.level}]
              </span>{" "}
              <span className="text-[#bfc4cc]">{line.text}</span>
            </div>
          ))
        )}
        <div ref={endRef} />
      </div>
    </div>
  );
}
