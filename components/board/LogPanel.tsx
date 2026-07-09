"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { Trash2, Clipboard, Check } from "lucide-react";
import { useAppState, type LogLine } from "@/lib/app-state";
import { getPreferences, setSizePref } from "@/lib/preferences";
import { ResizeHandle } from "@/components/ui/resizer";

// Level -> left-border color and label text color
const LEVEL_BORDER: Record<LogLine["level"], string> = {
  DEBUG:   "var(--tt-outline-soft)",
  INFO:    "var(--tt-text-faint)",
  SUCCESS: "var(--tt-success)",
  WARN:    "var(--tt-warn)",
  ERROR:   "var(--tt-danger)",
};
const LEVEL_TEXT: Record<LogLine["level"], string> = {
  DEBUG:   "var(--tt-text-faint)",
  INFO:    "var(--tt-text-muted)",
  SUCCESS: "var(--tt-success)",
  WARN:    "var(--tt-warn)",
  ERROR:   "var(--tt-danger)",
};

function fmtTs(ts: number): string {
  const d = new Date(ts);
  const p = (n: number) => String(n).padStart(2, "0");
  return `${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
}

export function LogPanel() {
  const { log, clearLog } = useAppState();
  const endRef = useRef<HTMLDivElement | null>(null);
  const [height, setHeight] = useState(() => getPreferences().sizes.logHeight);
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [log]);

  const copyAll = useCallback(() => {
    const text = log
      .map((l) => {
        const clean = l.text.replace(/^\[(DEBUG|INFO|SUCCESS|WARN|WARNING|ERROR)\]\s*/i, "");
        return `[${fmtTs(l.ts)}] [${l.level}] ${clean}`;
      })
      .join("\n");
    void navigator.clipboard.writeText(text).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 1800);
    });
  }, [log]);

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
      <div className="flex min-h-0 flex-1 flex-col overflow-hidden rounded-t-none border border-b-0 border-[var(--tt-outline)] bg-[var(--tt-surface-deepest)]">
        {/* Log header */}
        <div className="flex items-center justify-between border-b border-[var(--tt-outline-soft)] px-3 py-1">
          <span className="tt-section-header text-[9px]">Activity Log</span>
          <div className="flex items-center gap-1">
            <span className="mr-1 text-[10px] text-[var(--tt-text-faint)]">
              {log.length} line{log.length === 1 ? "" : "s"}
            </span>
            <button
              className="tt-btn-ghost !px-1.5 !py-0.5 !text-[10px] !gap-1"
              onClick={copyAll}
              disabled={log.length === 0}
              title="Copy all log lines to clipboard"
              aria-label="Copy log"
            >
              {copied ? (
                <Check className="h-3 w-3 text-[var(--tt-success)]" />
              ) : (
                <Clipboard className="h-3 w-3" />
              )}
            </button>
            <button
              className="tt-btn-ghost !px-1.5 !py-0.5 !text-[10px] !gap-1"
              onClick={clearLog}
              disabled={log.length === 0}
              title="Clear activity log"
              aria-label="Clear log"
            >
              <Trash2 className="h-3 w-3" />
            </button>
          </div>
        </div>

        {/* Log lines */}
        <div className="min-h-0 flex-1 overflow-auto px-0 py-1 font-mono leading-relaxed">
          {log.length === 0 ? (
            <p className="px-3 py-2 text-[11px] text-[var(--tt-text-faint)]">
              No activity yet.
            </p>
          ) : (
            log.map((line) => {
              const text = line.text.replace(
                /^\[(DEBUG|INFO|SUCCESS|WARN|WARNING|ERROR)\]\s*/i,
                ""
              );
              const border = LEVEL_BORDER[line.level];
              const textColor = LEVEL_TEXT[line.level];
              return (
                <div
                  key={line.id}
                  className="flex items-start gap-2 border-l-2 px-3 py-0.5 hover:bg-[var(--tt-surface-container)]"
                  style={{ borderLeftColor: border }}
                >
                  {/* Timestamp */}
                  <span className="shrink-0 text-[10px] tabular-nums text-[var(--tt-text-faint)]">
                    {fmtTs(line.ts)}
                  </span>
                  {/* Level tag */}
                  <span
                    className="w-[46px] shrink-0 text-[10px] font-bold uppercase"
                    style={{ color: textColor }}
                  >
                    {line.level}
                  </span>
                  {/* Message */}
                  <span className="min-w-0 flex-1 whitespace-pre-wrap break-words text-[11px] text-[var(--tt-text-secondary)]">
                    {text}
                  </span>
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
