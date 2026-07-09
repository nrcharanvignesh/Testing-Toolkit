"use client";

import { useEffect, useState } from "react";
import { Modal } from "@/components/ui/modal";
import { agent } from "@/lib/agent-client";
import { useAppState } from "@/lib/app-state";

/**
 * ViewLogDialog
 * Shows the tail of the log file in a scrollable, read-only monospace view so
 * it can be copied into a bug report. Footer: Copy all / Open log folder /
 * Close.
 */
export function ViewLogDialog({ onClose }: { onClose: () => void }) {
  const { pushLog } = useAppState();
  const [text, setText] = useState("Loading recent log...");
  const [path, setPath] = useState("");
  const [dir, setDir] = useState("");

  useEffect(() => {
    let alive = true;
    agent
      // Request a large tail (~8 MB) so the full recent history is shown, not
      // a small 60 KB window.
      .recentLog(8_000_000)
      .then((r) => {
        if (!alive) return;
        setText(r.text || "(log is empty)");
        setPath(r.path);
        setDir(r.dir);
      })
      .catch((e) => {
        if (!alive) return;
        setText(`Could not display the log: ${(e as Error).message}`);
      });
    return () => {
      alive = false;
    };
  }, []);

  const copyAll = async () => {
    try {
      await navigator.clipboard.writeText(text);
      pushLog("INFO", "Copied recent log to clipboard.");
    } catch {
      pushLog("WARN", "Could not copy to clipboard.");
    }
  };

  const openFolder = () => {
    pushLog("INFO", dir ? `Log folder: ${dir}` : `Log file: ${path}`);
  };

  return (
    <Modal
      open
      onClose={onClose}
      title={`Recent log${path ? ` - ${path}` : ""}`}
      width={900}
      footer={
        <>
          <button className="tt-btn-ghost mr-auto" onClick={copyAll}>
            Copy all
          </button>
          <button className="tt-btn-ghost" onClick={openFolder}>
            Open log folder
          </button>
          <button className="tt-btn-primary" onClick={onClose}>
            Close
          </button>
        </>
      }
    >
      <pre className="tt-input h-[480px] w-full overflow-auto whitespace-pre-wrap break-words font-mono text-[12px] leading-relaxed">
        {text}
      </pre>
    </Modal>
  );
}
