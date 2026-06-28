"use client";

import { useEffect, useRef } from "react";
import { CheckSquare, Minus, Square, X } from "lucide-react";
import { useAgent } from "@/lib/agent-context";
import { useAppState } from "@/lib/app-state";
import { ActivityBar } from "./ActivityBar";
import { NavPanel } from "./NavPanel";
import { StatusBar } from "./StatusBar";
import { BoardGrid } from "@/components/board/BoardGrid";
import { ActionBar } from "@/components/board/ActionBar";
import { LogPanel } from "@/components/board/LogPanel";
import { DialogHost } from "@/components/dialogs/DialogHost";

export function AppShell() {
  const { status } = useAgent();
  const { navVisible, logVisible, settings, reloadProjects } = useAppState();
  const bootstrapped = useRef(false);

  // Bootstrap: once connected & configured, load the project list (desktop
  // main.py _bootstrap -> reload_projects).
  useEffect(() => {
    if (
      status === "connected" &&
      settings?.configured &&
      !bootstrapped.current
    ) {
      bootstrapped.current = true;
      reloadProjects();
    }
  }, [status, settings?.configured, reloadProjects]);

  return (
    <div className="flex h-full flex-col overflow-hidden">
      {/* OS-style teal title bar (desktop parity: G01) */}
      <div className="tt-titlebar flex h-7 shrink-0 items-center justify-between pl-2.5 pr-1 select-none">
        <div className="flex items-center gap-1.5">
          <CheckSquare className="h-3.5 w-3.5" strokeWidth={2.5} />
          <span className="text-[12px] font-semibold tracking-tight">
            Testing Toolkit
          </span>
        </div>
        <div className="flex items-center gap-0.5">
          <span className="tt-titlebar-btn" aria-hidden>
            <Minus className="h-3 w-3" />
          </span>
          <span className="tt-titlebar-btn" aria-hidden>
            <Square className="h-2.5 w-2.5" />
          </span>
          <span className="tt-titlebar-btn is-close" aria-hidden>
            <X className="h-3 w-3" />
          </span>
        </div>
      </div>
      <div className="flex min-h-0 flex-1 overflow-hidden">
        {navVisible ? <NavPanel /> : <ActivityBar />}
        <main className="flex min-h-0 flex-1 flex-col gap-2 overflow-hidden px-2 py-2">
          <BoardGrid />
          <ActionBar />
          {logVisible && <LogPanel />}
        </main>
      </div>
      <StatusBar />
      <DialogHost />
    </div>
  );
}
