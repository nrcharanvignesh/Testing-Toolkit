"use client";

import { useMemo, useState } from "react";
import { RefreshCw } from "lucide-react";
import { useAppState } from "@/lib/app-state";
import type { WorkItemRow } from "@/lib/agent-client";
import {
  ALL,
  COLOR_MUTED,
  COLOR_WARN,
  NO_COLUMN,
  NO_ITER,
  UNASSIGNED,
  groupRowsByColumn,
  stateColor,
  typeColor,
  uniqueSorted,
} from "@/lib/board-utils";
import { DetailPane } from "./DetailPane";

export function BoardGrid() {
  const {
    boardView,
    boardLoading,
    currentProject,
    currentBoard,
    displayName,
    selected,
    setSelected,
    toggleSelected,
  } = useAppState();

  const [activeWiId, setActiveWiId] = useState<number | null>(null);
  const [search, setSearch] = useState("");
  const [fType, setFType] = useState(ALL);
  const [fAssignee, setFAssignee] = useState(ALL);
  const [fSprint, setFSprint] = useState(ALL);
  const [fColumn, setFColumn] = useState(ALL);

  const rows = boardView?.rows ?? [];
  const columns = boardView?.columns ?? [];

  const filterOptions = useMemo(() => {
    const types = uniqueSorted(rows.map((r) => r.wi_type));
    const assignees = uniqueSorted(rows.map((r) => r.assigned_to || UNASSIGNED));
    const sprints = uniqueSorted(rows.map((r) => r.board_lane || NO_ITER));
    const known = new Set(columns.map((c) => c.name));
    // Preserve board column order but drop duplicate names (split columns can
    // repeat a display name) so the filter <option> keys stay unique.
    const cols = Array.from(new Set(columns.map((c) => c.name)));
    if (rows.some((r) => !r.board_column || !known.has(r.board_column)))
      cols.push(NO_COLUMN);
    return { types, assignees, sprints, cols };
  }, [rows, columns]);

  const passes = (r: WorkItemRow): boolean => {
    const needle = search.trim().toLowerCase();
    if (needle && !`#${r.wi_id} ${r.title.toLowerCase()}`.includes(needle))
      return false;
    if (fType !== ALL && r.wi_type !== fType) return false;
    if (fAssignee !== ALL && (r.assigned_to || UNASSIGNED) !== fAssignee)
      return false;
    if (fSprint !== ALL && (r.board_lane || NO_ITER) !== fSprint) return false;
    if (fColumn !== ALL) {
      const known = new Set(columns.map((c) => c.name));
      const rc =
        r.board_column && known.has(r.board_column) ? r.board_column : NO_COLUMN;
      if (rc !== fColumn) return false;
    }
    return true;
  };

  const groups = useMemo(() => {
    const visible = rows.filter(passes);
    return groupRowsByColumn(visible, columns);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [rows, columns, search, fType, fAssignee, fSprint, fColumn]);

  const visibleIds = groups.flatMap(([, rs]) => rs.map((r) => r.wi_id));

  const setAll = (on: boolean) => {
    const next = new Set(selected);
    for (const id of visibleIds) {
      if (on) next.add(id);
      else next.delete(id);
    }
    setSelected(next);
  };

  const toggleLane = (laneRows: WorkItemRow[], on: boolean) => {
    const next = new Set(selected);
    for (const r of laneRows) {
      if (on) next.add(r.wi_id);
      else next.delete(r.wi_id);
    }
    setSelected(next);
  };

  // Desktop header includes the full board name (with " / Stories"), e.g.
  // "Abbott - Abbott 2026 Enhancements / Stories Work Items" (H01).
  const headerLabel =
    currentProject && currentBoard
      ? `${displayName(currentProject)} - ${
          currentBoard.name || currentBoard.team_name
        } Work Items`
      : currentProject
        ? `${displayName(currentProject)} Work Items`
        : "Work Items";

  return (
    <div className="flex min-h-0 flex-1 gap-2">
      {/* Items pane */}
      <div className="tt-card flex min-w-0 flex-[3] flex-col gap-1.5 p-2.5">
        <div className="flex items-center justify-between">
          <h2 className="tt-header text-[15px]">{headerLabel}</h2>
          <span
            className="text-xs"
            style={{
              color: selected.size ? "#10b981" : COLOR_MUTED,
              fontWeight: selected.size ? 600 : 400,
            }}
          >
            {selected.size} selected
          </span>
        </div>

        {/* Filter row 1 */}
        <div className="flex items-center gap-2">
          <input
            className="tt-input flex-1"
            placeholder="Filter by id or title..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
          <button
            className="tt-btn-ghost shrink-0 !px-3 !py-1.5 text-xs"
            onClick={() => setAll(true)}
            disabled={!rows.length}
          >
            Select all
          </button>
          <button
            className="tt-btn-ghost shrink-0 !px-3 !py-1.5 text-xs"
            onClick={() => setAll(false)}
            disabled={!selected.size}
          >
            Clear
          </button>
        </div>

        {/* Filter row 2 */}
        <div className="grid grid-cols-4 gap-2">
          <FilterSelect label="Type" value={fType} onChange={setFType} options={filterOptions.types} />
          <FilterSelect label="Assignee" value={fAssignee} onChange={setFAssignee} options={filterOptions.assignees} />
          <FilterSelect label="Sprint" value={fSprint} onChange={setFSprint} options={filterOptions.sprints} />
          <FilterSelect label="Column" value={fColumn} onChange={setFColumn} options={filterOptions.cols} />
        </div>

        {/* Grid */}
        <div className="min-h-0 flex-1 overflow-auto rounded-[10px] border border-[#2d313c] bg-[#13161d]">
          {boardLoading ? (
            <div className="flex h-full items-center justify-center gap-2 text-sm text-muted-foreground">
              <RefreshCw className="h-4 w-4 animate-spin" /> Loading work items...
            </div>
          ) : !boardView ? (
            <EmptyHint text="Select a board to load its work items." />
          ) : groups.length === 0 ? (
            <EmptyHint
              text={rows.length ? "No items match the filters." : "No work items on this board."}
              warn
            />
          ) : (
            <table className="w-full border-collapse text-sm">
              <thead className="sticky top-0 z-10 bg-[#13161d]">
                <tr className="text-left text-xs text-[#bfc4cc]">
                  <th className="w-8 border-b border-[#2d313c] px-2 py-2" />
                  <th className="border-b border-[#2d313c] px-2 py-2 font-semibold">ID</th>
                  <th className="border-b border-[#2d313c] px-2 py-2 font-semibold">Title</th>
                  <th className="border-b border-[#2d313c] px-2 py-2 font-semibold">Type</th>
                  <th className="border-b border-[#2d313c] px-2 py-2 font-semibold">State</th>
                  <th className="border-b border-[#2d313c] px-2 py-2 font-semibold">Assignee</th>
                  <th className="border-b border-[#2d313c] px-2 py-2 font-semibold">Sprint</th>
                </tr>
              </thead>
              <tbody>
                {groups.map(([lane, laneRows]) => {
                  const checkedCount = laneRows.filter((r) =>
                    selected.has(r.wi_id)
                  ).length;
                  const allChecked = checkedCount === laneRows.length;
                  const someChecked = checkedCount > 0 && !allChecked;
                  return (
                    <LaneGroup
                      key={lane}
                      lane={lane}
                      laneRows={laneRows}
                      allChecked={allChecked}
                      someChecked={someChecked}
                      selected={selected}
                      activeWiId={activeWiId}
                      onToggleLane={(on) => toggleLane(laneRows, on)}
                      onToggleRow={toggleSelected}
                      onActivate={setActiveWiId}
                    />
                  );
                })}
              </tbody>
            </table>
          )}
        </div>
        {boardView && (
          <p className="text-xs" style={{ color: COLOR_MUTED }}>
            {rows.length} work item(s) in {columns.length} column(s). Tick items
            to select; click to view details.
          </p>
        )}
      </div>

      {/* Detail pane */}
      <div className="flex min-w-0 flex-[2] flex-col">
        <DetailPane activeWiId={activeWiId} />
      </div>
    </div>
  );
}

function LaneGroup({
  lane,
  laneRows,
  allChecked,
  someChecked,
  selected,
  activeWiId,
  onToggleLane,
  onToggleRow,
  onActivate,
}: {
  lane: string;
  laneRows: WorkItemRow[];
  allChecked: boolean;
  someChecked: boolean;
  selected: Set<number>;
  activeWiId: number | null;
  onToggleLane: (on: boolean) => void;
  onToggleRow: (id: number, on: boolean) => void;
  onActivate: (id: number) => void;
}) {
  return (
    <>
      <tr className="tt-group-row">
        <td className="px-2 py-1.5">
          <input
            type="checkbox"
            className="tt-check"
            checked={allChecked}
            ref={(el) => {
              if (el) el.indeterminate = someChecked;
            }}
            onChange={(e) => onToggleLane(e.target.checked)}
          />
        </td>
        <td colSpan={6} className="px-2 py-1.5">
          <span className="tt-group-tri">▼</span>
          <span className="text-sm font-semibold text-[#cfd4dc]">
            {lane}
          </span>{" "}
          <span className="text-xs text-[#8a8f99]">({laneRows.length})</span>
        </td>
      </tr>
      {laneRows.map((r) => {
        const tc = typeColor(r.wi_type);
        const isActive = r.wi_id === activeWiId;
        return (
          <tr
            key={r.wi_id}
            onClick={() => onActivate(r.wi_id)}
            className={`cursor-pointer border-b border-[#1e2128] transition-colors ${
              isActive ? "tt-row-selected" : "hover:bg-[#1a1d26]"
            }`}
          >
            <td className="px-2 py-1.5" onClick={(e) => e.stopPropagation()}>
              <input
                type="checkbox"
                className="tt-check"
                checked={selected.has(r.wi_id)}
                onChange={(e) => onToggleRow(r.wi_id, e.target.checked)}
              />
            </td>
            <td className="px-2 py-1.5 font-semibold text-[#5ba8ff]">
              {r.wi_id}
            </td>
            <td
              className="max-w-0 truncate px-2 py-1.5 text-[#edf0f5]"
              title={r.title}
            >
              {r.title}
            </td>
            <td className="px-2 py-1.5 text-sm" style={{ color: tc ?? "#bfc4cc" }}>
              {r.wi_type}
            </td>
            <td
              className="px-2 py-1.5 text-sm"
              style={{ color: stateColor(r.state) }}
            >
              {r.state || "n/a"}
            </td>
            <td className="truncate px-2 py-1.5 text-sm text-[#bfc4cc]">
              {r.assigned_to}
            </td>
            <td className="truncate px-2 py-1.5 text-sm text-[#bfc4cc]">
              {r.board_lane}
            </td>
          </tr>
        );
      })}
    </>
  );
}

function FilterSelect({
  label,
  value,
  onChange,
  options,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  options: string[];
}) {
  return (
    <select
      className="tt-input cursor-pointer text-sm"
      value={value}
      onChange={(e) => onChange(e.target.value)}
      aria-label={`Filter by ${label}`}
    >
      <option value={ALL}>{`${label}: ${ALL}`}</option>
      {options.map((o) => (
        <option key={o} value={o}>
          {o}
        </option>
      ))}
    </select>
  );
}

function EmptyHint({ text, warn }: { text: string; warn?: boolean }) {
  return (
    <div className="flex h-full items-center justify-center p-6 text-center">
      <p className="text-sm" style={{ color: warn ? COLOR_WARN : COLOR_MUTED }}>
        {text}
      </p>
    </div>
  );
}
