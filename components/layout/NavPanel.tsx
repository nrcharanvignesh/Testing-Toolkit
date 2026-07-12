"use client";

import { useState } from "react";
import { RefreshCw, LayoutDashboard, KanbanSquare } from "lucide-react";

import { useAppState } from "@/lib/app-state";
import { useTheme } from "@/lib/theme";
import { Dropdown } from "@/components/ui/dropdown";
import { agent } from "@/lib/agent-client";
import { getPreferences, setSizePref } from "@/lib/preferences";
import { ResizeHandle } from "@/components/ui/resizer";
import { useAppUpdate } from "@/lib/use-app-update";
import { SourceLogo } from "@/components/ui/source-logo";
import { projectSourceType } from "@/lib/board-utils";

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
    pushLog,
    boardView,
    settings,
  } = useAppState();

  const { theme, toggleTheme } = useTheme();
  const { check: checkForUpdate, busy: updateBusy } = useAppUpdate(pushLog);
  const [width, setWidth] = useState(() => getPreferences().sizes.navWidth);

  async function onUpdateClick() {
    setLogVisible(true);
    await checkForUpdate();
  }

  async function openLogFolder() {
    setLogVisible(true);
    try {
      const { dir, path } = await agent.recentLog(1);
      pushLog("INFO", dir ? `Log folder: ${dir}` : `Log file: ${path}`);
    } catch (e) {
      pushLog("WARN", `Could not locate the log folder: ${(e as Error).message}`);
    }
  }

  return (
    <>
      <div
        className="tt-rail flex shrink-0 flex-col gap-3 p-2"
        style={{ width }}
      >
        {/* ── Projects ───────────────────────────────────────────── */}
        <div className="flex min-h-0 flex-1 flex-col">
          <div className="flex items-center justify-between px-1 pb-1.5">
            <span className="tt-section-header">Projects</span>
            <button
              className="tt-btn-ghost !px-1.5 !py-0.5 !text-[10px] !gap-1"
              onClick={reloadProjects}
              title="Refresh project list"
            >
              <RefreshCw className="h-2.5 w-2.5" />
              Refresh
            </button>
          </div>
          <div className="min-h-[60px] flex-1 overflow-auto rounded-[8px] border border-[var(--tt-outline-soft)] bg-[var(--tt-surface-base)] p-1">
            {projects.length === 0 ? (
              <p className="px-2 py-2 text-xs text-muted-foreground">
                No projects. Connect Azure DevOps or JIRA in Settings.
              </p>
            ) : (
              projects.map((full) => {
                const name = displayName(full);
                const isSelected = full === currentProject;
                const source = projectSourceType(full, {
                  jiraConfigured: settings?.jira_configured,
                  adoConfigured: settings?.ado_configured,
                });
                return (
                  <div
                    key={full}
                    role="button"
                    tabIndex={0}
                    data-selected={isSelected}
                    onClick={() => selectProject(full)}
                    onKeyDown={(e) => {
                      // ARIA button pattern: activate on Enter and Space.
                      if (e.key === "Enter" || e.key === " ") {
                        e.preventDefault();
                        selectProject(full);
                      }
                    }}
                    className="tt-list-item flex items-center gap-2 text-sm"
                    title={full}
                  >
                    {/* Work-item source brand mark (Azure DevOps / Jira) */}
                    <SourceLogo source={source} size={18} />
                    <span className="truncate">{name}</span>
                    {isSelected && (
                      <LayoutDashboard className="ml-auto h-3 w-3 shrink-0 opacity-60" />
                    )}
                  </div>
                );
              })
            )}
          </div>
        </div>

        {/* ── Boards ─────────────────────────────────────────────── */}
        <div className="flex min-h-0 flex-1 flex-col">
          <div className="flex items-center justify-between px-1 pb-1.5">
            <span className="tt-section-header">Boards</span>
            <button
              className="tt-btn-ghost !px-1.5 !py-0.5 !text-[10px] !gap-1"
              onClick={() => reloadBoards()}
              title="Refresh board list"
            >
              <RefreshCw className="h-2.5 w-2.5" />
              Refresh
            </button>
          </div>
          <div className="min-h-0 flex-1 overflow-auto rounded-[8px] border border-[var(--tt-outline-soft)] bg-[var(--tt-surface-base)] p-1">
            {boards.length === 0 ? (
              <p className="px-2 py-2 text-xs text-muted-foreground">
                {currentProject ? "No boards found." : "Select a project first."}
              </p>
            ) : (
              boards.map((b) => {
                const isSelected = b.label === currentBoard?.label;
                return (
                  <div
                    key={b.id || b.label}
                    role="button"
                    tabIndex={0}
                    data-selected={isSelected}
                    onClick={() => selectBoard(b)}
                    onKeyDown={(e) => {
                      // ARIA button pattern: activate on Enter and Space.
                      if (e.key === "Enter" || e.key === " ") {
                        e.preventDefault();
                        selectBoard(b);
                      }
                    }}
                    className="tt-list-item flex items-center gap-2 text-sm"
                    title={b.label}
                  >
                    <KanbanSquare
                      className="h-3.5 w-3.5 shrink-0"
                      style={{
                        color: isSelected
                          ? "white"
                          : "var(--tt-text-muted)",
                      }}
                    />
                    <span className="min-w-0 flex-1 truncate">{b.team_name}</span>
                    {/* Show WI count only for the currently loaded board */}
                    {isSelected && boardView && (
                      <span
                        className="shrink-0 rounded-full px-1.5 py-0.5 text-[9px] font-bold tabular-nums"
                        style={{
                          background: "rgba(255,255,255,0.18)",
                          color: "rgba(255,255,255,0.85)",
                        }}
                        title={`${boardView.rows.length} work items`}
                      >
                        {boardView.rows.length}
                      </span>
                    )}
                  </div>
                );
              })
            )}
          </div>
        </div>

        {/* ── Bottom toolbar ─────────────────────────────────────── */}
        <div className="grid grid-cols-2 gap-1 border-t border-[var(--tt-outline-soft)] pt-2 min-[300px]:grid-cols-3">
          <Dropdown
            className="w-full"
            align="left"
            direction="up"
            items={[
              { label: "Open log folder", onClick: () => openLogFolder() },
              { label: "View recent log...", onClick: () => openDialog("viewlog") },
              { label: "About", separatorBefore: true, onClick: () => openDialog("about") },
            ]}
            trigger={({ toggle, ref }) => (
              <NavLabelBtn
                ref={ref}
                onClick={toggle}
                title="Help & About"
                label="Help"
              />
            )}
          />
          <NavLabelBtn
            title="Settings"
            onClick={() => openDialog("settings")}
            label="Settings"
          />
          <NavLabelBtn
            title="Project Knowledge Base"
            disabled={!currentProject}
            onClick={() => openDialog("kb")}
            label="Project KB"
          />
          <NavLabelBtn
            title="Manage encrypted test-environment credentials for E2E automation"
            disabled={!currentProject}
            onClick={() => openDialog("credentials")}
            label="Credentials"
          />
          <NavLabelBtn
            title={theme === "dark" ? "Switch to light theme" : "Switch to dark theme"}
            aria-label={theme === "dark" ? "Switch to light theme" : "Switch to dark theme"}
            onClick={toggleTheme}
            label="Theme"
          />
          <NavLabelBtn
            title="Check for updates"
            disabled={updateBusy}
            onClick={() => void onUpdateClick()}
            label={updateBusy ? "Checking..." : "Update"}
          />
          <div className="col-span-full">
            <NavLabelBtn
              title="Collapse Navigation Bar"
              onClick={() => setNavVisible(false)}
              label="Collapse Navigation Bar"
            />
          </div>
        </div>
      </div>

      <ResizeHandle
        axis="x"
        value={width}
        min={180}
        max={480}
        onChange={setWidth}
        onCommit={(v) => setSizePref("navWidth", v)}
        ariaLabel="Resize navigator"
      />
    </>
  );
}

// ---------------------------------------------------------------------------
// Small reusable labeled icon button for the bottom toolbar
// ---------------------------------------------------------------------------
import React from "react";

/**
 * Text-only button for the EXPANDED nav bottom toolbar. Icons are intentionally
 * omitted here — the collapsed rail (ActivityBar) shows the icons instead, so
 * expanded mode stays text-only per product design.
 */
const NavLabelBtn = React.forwardRef<
  HTMLButtonElement,
  {
    title: string;
    onClick: () => void;
    label: string;
    disabled?: boolean;
    "aria-label"?: string;
  }
>(function NavLabelBtn({ title, onClick, label, disabled, ...rest }, ref) {
  return (
    <button
      ref={ref}
      title={title}
      aria-label={rest["aria-label"] ?? title}
      disabled={disabled}
      className="tt-btn-ghost flex h-9 w-full min-w-0 items-center justify-center !px-2 !text-[11px] disabled:opacity-40"
      onClick={onClick}
    >
      <span className="truncate">{label}</span>
    </button>
  );
});
