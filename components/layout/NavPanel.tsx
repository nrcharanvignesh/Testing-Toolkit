"use client";

import {
  Wrench,
  HelpCircle,
  Settings,
  Brain,
  ChevronLeft,
} from "lucide-react";
import { useAppState } from "@/lib/app-state";
import { Dropdown } from "@/components/ui/dropdown";

export function NavPanel() {
  const {
    projects,
    currentProject,
    selectProject,
    reloadProjects,
    displayName,
    boards,
    currentBoard,
    selectBoard,
    reloadBoards,
    setNavVisible,
    openDialog,
    setLogVisible,
  } = useAppState();

  return (
    <div className="tt-rail flex w-56 shrink-0 flex-col gap-2 p-2">
      {/* Projects */}
      <div className="flex items-center justify-between px-1">
        <span className="text-sm font-semibold text-[#bfc4cc]">Projects</span>
        <button className="tt-btn-ghost !px-2 !py-1 text-xs" onClick={reloadProjects}>
          Refresh
        </button>
      </div>
      <div className="tt-input min-h-0 flex-1 overflow-auto !p-1">
        {projects.length === 0 ? (
          <p className="px-2 py-1.5 text-xs text-muted-foreground">
            No projects. Configure ADO in Settings, then Refresh.
          </p>
        ) : (
          projects.map((full) => (
            <div
              key={full}
              role="button"
              tabIndex={0}
              data-selected={full === currentProject}
              onClick={() => selectProject(full)}
              onKeyDown={(e) => e.key === "Enter" && selectProject(full)}
              className="tt-list-item truncate text-sm"
              title={full}
            >
              {displayName(full)}
            </div>
          ))
        )}
      </div>

      {/* Boards */}
      <div className="flex items-center justify-between px-1">
        <span className="text-sm font-semibold text-[#bfc4cc]">Boards</span>
        <button
          className="tt-btn-ghost !px-2 !py-1 text-xs"
          onClick={() => reloadBoards()}
        >
          Refresh
        </button>
      </div>
      <div className="tt-input min-h-0 flex-1 overflow-auto !p-1">
        {boards.length === 0 ? (
          <p className="px-2 py-1.5 text-xs text-muted-foreground">
            {currentProject ? "No boards found." : "Select a project."}
          </p>
        ) : (
          boards.map((b) => (
            <div
              key={b.id || b.label}
              role="button"
              tabIndex={0}
              data-selected={b.label === currentBoard?.label}
              onClick={() => selectBoard(b)}
              onKeyDown={(e) => e.key === "Enter" && selectBoard(b)}
              className="tt-list-item truncate text-sm"
              title={b.label}
            >
              {b.team_name}
            </div>
          ))
        )}
      </div>

      {/* Bottom buttons */}
      <div className="flex items-center gap-1">
        <Dropdown
          align="left"
          items={[
            { label: "Bulk Defects...", onClick: () => openDialog("defects") },
            { label: "Retrieval preview...", onClick: () => openDialog("retrieval") },
          ]}
          trigger={({ toggle, ref }) => (
            <button
              ref={ref}
              onClick={toggle}
              title="Tools"
              className="tt-btn-ghost h-8 w-8 !p-0"
            >
              <Wrench className="h-[18px] w-[18px]" strokeWidth={2} />
            </button>
          )}
        />
        <Dropdown
          align="left"
          items={[
            { label: "View log", onClick: () => setLogVisible(true) },
            { label: "About", separatorBefore: true, onClick: () => openDialog("settings") },
          ]}
          trigger={({ toggle, ref }) => (
            <button
              ref={ref}
              onClick={toggle}
              title="Help"
              className="tt-btn-ghost h-8 w-8 !p-0"
            >
              <HelpCircle className="h-[18px] w-[18px]" strokeWidth={2} />
            </button>
          )}
        />
        <button
          title="Settings"
          className="tt-btn-ghost h-8 w-8 !p-0"
          onClick={() => openDialog("settings")}
        >
          <Settings className="h-[18px] w-[18px]" strokeWidth={2} />
        </button>
        <button
          title="Project KB"
          className="tt-btn-ghost h-8 w-8 !p-0 disabled:opacity-40"
          disabled={!currentProject}
          onClick={() => openDialog("kb")}
        >
          <Brain className="h-[18px] w-[18px]" strokeWidth={2} />
        </button>
        <button
          title="Hide navigator"
          className="tt-btn-ghost h-8 w-8 !p-0"
          onClick={() => setNavVisible(false)}
        >
          <ChevronLeft className="h-[18px] w-[18px]" strokeWidth={2} />
        </button>
      </div>
    </div>
  );
}
