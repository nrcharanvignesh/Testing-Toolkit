"use client";

import {
  useMemo,
  useRef,
  useState,
  type MouseEvent,
  type PointerEvent as ReactPointerEvent,
} from "react";
import useSWR from "swr";
import {
  RefreshCw,
  PanelRightOpen,
  PanelRightClose,
  ChevronLeft,
  ChevronRight,
  ChevronDown,
  ArrowUp,
  ArrowDown,
  ArrowUpDown,
  Inbox,
  Play,
  CheckCircle2,
  XCircle,
  Archive,
  Download,
} from "lucide-react";
import { useAppState } from "@/lib/app-state";
import { usePreferences, getPreferences, setSizePref } from "@/lib/preferences";
import {
  useBoardColumns,
  BOARD_COLUMNS,
  COLLAPSED_WIDTH,
  type BoardColumnId,
} from "@/lib/board-columns";
import { useCollapsedLanes } from "@/lib/board-lanes";
import { ResizeHandle } from "@/components/ui/resizer";
import {
  agent,
  type WorkItemRow,
  type WiId,
  type E2ELastRun,
  type E2ETestCase,
  type SettingsResponse,
} from "@/lib/agent-client";
import { exportSingleBoard } from "@/lib/export-board";
import { showToast } from "@/lib/toast";
import {
  ALL,
  COLOR_MUTED,
  COLOR_WARN,
  NO_COLUMN,
  NO_ITER,
  UNASSIGNED,
  groupRowsByColumn,
  testCaseCountsByWorkItem,
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
    settings,
  } = useAppState();

  const { prefs, togglePanel, setPanel } = usePreferences();
  const detailVisible = prefs.panels.detail;

  // Persisted per-column widths + collapsed flags (Excel-like resizing and
  // header carets). Restored automatically from localStorage on next launch.
  const {
    state: colState,
    width: colWidth,
    isCollapsed: colCollapsed,
    setWidth: setColWidth,
    toggleCollapsed: toggleColCollapsed,
  } = useBoardColumns();

  // Persisted collapsed state for row groups (swim lanes). Clicking a lane
  // header hides its rows; restored automatically on next launch.
  const {
    isCollapsed: laneCollapsed,
    toggle: toggleLaneCollapsed,
    setAll: setAllLanesCollapsed,
  } = useCollapsedLanes();

  // Clicking a work item activates it and auto-opens the detail panel.
  const activateRow = (id: WiId) => {
    setActiveWiId(id);
    setPanel("detail", true);
  };

  // Clicking an empty area of the grid clears the active item and hides the
  // detail panel. Only fires when the click lands on the container itself
  // (empty space below the rows), not on a row/cell.
  const clearOnEmptyClick = (e: MouseEvent) => {
    if (e.target === e.currentTarget) {
      setActiveWiId(null);
      setPanel("detail", false);
    }
  };
  const [detailWidth, setDetailWidth] = useState(
    () => getPreferences().sizes.detailWidth
  );

  const [activeWiId, setActiveWiId] = useState<WiId | null>(null);
  const [search, setSearch] = useState("");
  const [fType, setFType] = useState(ALL);
  const [fAssignee, setFAssignee] = useState(ALL);
  const [fSprint, setFSprint] = useState(ALL);
  const [fColumn, setFColumn] = useState(ALL);
  const [fKpiBucket, setFKpiBucket] = useState(ALL);
  const [exporting, setExporting] = useState(false);
  const [exportProgress, setExportProgress] = useState("");

  // Column sort state: click a header to sort ASC, click again for DESC.
  const [sortCol, setSortCol] = useState<BoardColumnId | null>(null);
  const [sortDir, setSortDir] = useState<"asc" | "desc">("asc");

  const handleSort = (col: BoardColumnId) => {
    if (sortCol === col) {
      setSortDir((d) => (d === "asc" ? "desc" : "asc"));
    } else {
      setSortCol(col);
      setSortDir("asc");
    }
  };

  const rows = useMemo(() => boardView?.rows ?? [], [boardView?.rows]);
  const columns = useMemo(() => boardView?.columns ?? [], [boardView?.columns]);

  // Generated Tests + Last Run data (desktop parity: cols 6 & 7).
  // This is generation traceability, not implementation or execution coverage.
  const { data: testCases } = useSWR<E2ETestCase[]>(
    currentProject ? ["board-coverage", currentProject] : null,
    ([, proj]: [string, string]) => agent.e2eTestCases(proj),
    { revalidateOnFocus: false, shouldRetryOnError: false }
  );
  // Last Run: map parent WI id -> pass/fail from the latest execution run.
  // Shares SWR cache key with CoverageBar so there's only one request.
  const { data: lastRun } = useSWR<E2ELastRun | null>(
    currentProject ? ["e2e-last-run", currentProject] : null,
    ([, proj]: [string, string]) => agent.e2eLastRun(proj),
    { refreshInterval: 60_000, revalidateOnFocus: false, shouldRetryOnError: false }
  );

  const testCounts = useMemo(
    () => testCaseCountsByWorkItem(rows, testCases ?? []),
    [rows, testCases]
  );
  const hasCoverageData = testCases !== undefined;

  const runStatus = useMemo(() => {
    const m = new Map<string, "pass" | "fail">();
    for (const r of lastRun?.results ?? []) {
      const id = String(r.tc_id);
      const st = (r.status || "").toLowerCase();
      if (st === "fail" || st === "error") m.set(id, "fail");
      else if (st === "pass" && m.get(id) !== "fail") m.set(id, "pass");
    }
    return m;
  }, [lastRun]);
  const hasRunData = !!lastRun;

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

  const kpiBucketCols = useMemo(
    () => fKpiBucket !== ALL ? columnsForBucket(columns, fKpiBucket) : null,
    [columns, fKpiBucket]
  );

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
    if (kpiBucketCols) {
      const rc = r.board_column || NO_COLUMN;
      if (!kpiBucketCols.has(rc)) return false;
    }
    return true;
  };

  const groups = useMemo(() => {
    const visible = rows.filter(passes);
    const grouped = groupRowsByColumn(visible, columns);
    if (!sortCol) return grouped;
    const dir = sortDir === "asc" ? 1 : -1;
    const cmp = buildRowComparator(sortCol, dir, testCounts, runStatus);
    return grouped.map(([lane, laneRows]) => [lane, [...laneRows].sort(cmp)] as const);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [rows, columns, search, fType, fAssignee, fSprint, fColumn, fKpiBucket, sortCol, sortDir, testCounts, runStatus]);

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

  const headerLabel = (() => {
    if (!currentProject) return "Work Items";
    const project = displayName(currentProject);
    if (!currentBoard) return `${project} Work Items`;
    let board = (currentBoard.team_name || currentBoard.name || "").trim();
    if (board.toLowerCase().startsWith(project.toLowerCase())) {
      const stripped = board.slice(project.length).replace(/^[\s\-–—:]+/, "");
      if (stripped) board = stripped;
    }
    const base = board
      ? `${project} - ${board} Work Items`
      : `${project} Work Items`;
    return rows.length ? `${base} (${rows.length})` : base;
  })();

  return (
    <div className="flex min-h-0 flex-1 gap-2">
      {/* Items pane */}
      <div className="tt-card flex min-w-0 flex-1 flex-col gap-1.5 p-2.5">
        <div className="flex items-center justify-between gap-2">
          <div className="flex items-center gap-3">
            <h2 className="tt-header text-[15px]">{headerLabel}</h2>
            {columns.length > 0 && (
              <KpiTiles
                columns={columns}
                rows={rows}
                activeBucket={fKpiBucket}
                onSelect={(b) => setFKpiBucket(b === fKpiBucket ? ALL : b)}
              />
            )}
          </div>
          <div className="flex items-center gap-2">
            <span
              className="text-xs"
              style={{
                color: selected.size ? "var(--tt-success)" : COLOR_MUTED,
                fontWeight: selected.size ? 600 : 400,
              }}
            >
              {selected.size} selected
            </span>
            <button
              className="tt-btn-ghost flex shrink-0 items-center justify-center rounded-md !p-0 relative"
              style={{ height: 28, minWidth: 28, paddingInline: exportProgress ? 8 : 0 }}
              onClick={() => {
                if (exporting) return;
                setExporting(true);
                setExportProgress("");
                const projectName = currentProject ? displayName(currentProject) : "Project";
                const boardName = currentBoard?.team_name || currentBoard?.name || "Board";
                const visibleRows = groups.flatMap(([, rs]) => rs);
                const kpiCounts: Record<string, number> = {};
                for (const b of KPI_BUCKETS) {
                  const bucketCols = columnsForBucket(columns, b.label);
                  let count = 0;
                  for (const r of rows) {
                    const col = r.board_column || NO_COLUMN;
                    if (bucketCols.has(col)) count++;
                  }
                  kpiCounts[b.label] = count;
                }
                void exportSingleBoard({
                  projectName,
                  boardName,
                  rows: visibleRows,
                  kpiCounts,
                  filters: { type: fType, assignee: fAssignee, sprint: fSprint, column: fColumn, search },
                  settings,
                  testCases: testCases ?? [],
                  lastRun: lastRun ?? null,
                  fetchDetail: currentProject
                    ? (wiId) => agent.workItemDetail(currentProject, wiId)
                    : undefined,
                  onProgress: (done, total, phase) => {
                    setExportProgress(`${phase}: ${done}/${total}`);
                  },
                }).then(() => {
                  showToast(`Exported 1 board from ${projectName} to Excel`);
                }).finally(() => {
                  setExporting(false);
                  setExportProgress("");
                });
              }}
              disabled={boardLoading || !boardView || rows.length === 0 || exporting}
              title={exporting ? exportProgress : "Export board to Excel"}
              aria-label="Export board to Excel"
            >
              {exporting ? (
                <RefreshCw className="h-3.5 w-3.5 animate-spin" strokeWidth={2} />
              ) : (
                <Download className="h-3.5 w-3.5" strokeWidth={2} />
              )}
              {exportProgress && (
                <span className="ml-1 text-[10px] whitespace-nowrap" style={{ color: COLOR_MUTED }}>
                  {exportProgress}
                </span>
              )}
            </button>
            <button
              className="tt-btn-ghost flex shrink-0 items-center gap-1.5 !px-3 !py-1.5 text-xs"
              onClick={() => togglePanel("detail")}
              title={
                detailVisible ? "Hide the detail panel" : "Show the detail panel"
              }
            >
              {detailVisible ? (
                <PanelRightClose className="h-3.5 w-3.5" strokeWidth={2} />
              ) : (
                <PanelRightOpen className="h-3.5 w-3.5" strokeWidth={2} />
              )}
              {detailVisible ? "Hide details" : "Show details"}
            </button>
          </div>
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
          <button
            className="tt-btn-ghost shrink-0 !px-3 !py-1.5 text-xs"
            onClick={() => {
              const laneNames = groups.map(([lane]) => lane);
              const allCollapsed =
                laneNames.length > 0 && laneNames.every((l) => laneCollapsed(l));
              setAllLanesCollapsed(laneNames, !allCollapsed);
            }}
            disabled={!groups.length}
            title="Collapse or expand all row groups"
          >
            {groups.length > 0 && groups.every(([lane]) => laneCollapsed(lane))
              ? "Expand all"
              : "Collapse all"}
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
        <div
          className="min-h-0 flex-1 overflow-auto rounded-[10px] border border-[var(--tt-outline)] bg-[var(--tt-surface-base)]"
          onClick={clearOnEmptyClick}
        >
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
            <table className="w-full table-fixed border-collapse text-sm">
              <colgroup>
                <col style={{ width: 32 }} />
                {BOARD_COLUMNS.map((c) => (
                  <col key={c.id} style={{ width: colWidth(c.id) }} />
                ))}
              </colgroup>
              <thead className="sticky top-0 z-10 bg-[var(--tt-surface-base)]">
                <tr className="text-left text-xs text-[var(--tt-text-secondary)]">
                  <th className="border-b border-[var(--tt-outline)] px-2 py-2" />
                  {BOARD_COLUMNS.map((c) => (
                    <ColumnHeader
                      key={c.id}
                      id={c.id}
                      label={c.label}
                      collapsed={colCollapsed(c.id)}
                      currentWidth={colWidth(c.id)}
                      sortDir={sortCol === c.id ? sortDir : null}
                      onSort={() => handleSort(c.id)}
                      onResize={(px, commit) =>
                        setColWidth(c.id, px, commit)
                      }
                      onToggleCollapsed={() => toggleColCollapsed(c.id)}
                    />
                  ))}
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
                      collapsed={laneCollapsed(lane)}
                      selected={selected}
                      activeWiId={activeWiId}
                      testCounts={testCounts}
                      collapsedCols={colState.collapsed}
                      hasCoverageData={hasCoverageData}
                      runStatus={runStatus}
                      hasRunData={hasRunData}
                      settings={settings}
                      onToggleCollapsed={() => toggleLaneCollapsed(lane)}
                      onToggleLane={(on) => toggleLane(laneRows, on)}
                      onToggleRow={toggleSelected}
                      onActivate={activateRow}
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

      {/* Detail pane — hidden by default, toggled from the Work Items header */}
      {detailVisible && (
        <>
          <ResizeHandle
            axis="x"
            value={detailWidth}
            min={300}
            max={900}
            invert
            onChange={setDetailWidth}
            onCommit={(v) => setSizePref("detailWidth", v)}
            ariaLabel="Resize detail panel"
          />
          <div
            className="flex shrink-0 flex-col"
            style={{ width: detailWidth }}
          >
            <DetailPane activeWiId={activeWiId} />
          </div>
        </>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Sort comparator
// ---------------------------------------------------------------------------
function buildRowComparator(
  col: BoardColumnId,
  dir: number,
  testCounts: Map<string, number>,
  runStatus: Map<string, "pass" | "fail">,
): (a: WorkItemRow, b: WorkItemRow) => number {
  return (a, b) => {
    let av: string | number;
    let bv: string | number;
    switch (col) {
      case "id":
        av = typeof a.wi_id === "number" ? a.wi_id : String(a.wi_id);
        bv = typeof b.wi_id === "number" ? b.wi_id : String(b.wi_id);
        if (typeof av === "number" && typeof bv === "number")
          return (av - bv) * dir;
        return String(av).localeCompare(String(bv)) * dir;
      case "title":
        return (a.title || "").localeCompare(b.title || "", undefined, { sensitivity: "base" }) * dir;
      case "type":
        return (a.wi_type || "").localeCompare(b.wi_type || "", undefined, { sensitivity: "base" }) * dir;
      case "state":
        return (a.state || "").localeCompare(b.state || "", undefined, { sensitivity: "base" }) * dir;
      case "assignee":
        return (a.assigned_to || "").localeCompare(b.assigned_to || "", undefined, { sensitivity: "base" }) * dir;
      case "sprint":
        return (a.board_lane || "").localeCompare(b.board_lane || "", undefined, { sensitivity: "base" }) * dir;
      case "tests":
        av = testCounts.get(String(a.wi_id)) ?? 0;
        bv = testCounts.get(String(b.wi_id)) ?? 0;
        return ((av as number) - (bv as number)) * dir;
      case "lastRun": {
        const rank = (s: "pass" | "fail" | undefined): number =>
          s === "pass" ? 2 : s === "fail" ? 1 : 0;
        av = rank(runStatus.get(String(a.wi_id)));
        bv = rank(runStatus.get(String(b.wi_id)));
        return ((av as number) - (bv as number)) * dir;
      }
      default:
        return 0;
    }
  };
}

// ---------------------------------------------------------------------------
// Resizable / collapsible column header
// ---------------------------------------------------------------------------
const TESTS_HINT =
  "Total test cases traced to this work item: tool-generated plus those already linked in the tracker (ADO 'Tested By' / JIRA test links)";

function ColumnHeader({
  id,
  label,
  collapsed,
  currentWidth,
  sortDir,
  onSort,
  onResize,
  onToggleCollapsed,
}: {
  id: BoardColumnId;
  label: string;
  collapsed: boolean;
  currentWidth: number;
  sortDir: "asc" | "desc" | null;
  onSort: () => void;
  /** commit=false during a live drag, commit=true once on pointer-up. */
  onResize: (px: number, commit: boolean) => void;
  onToggleCollapsed: () => void;
}) {
  const drag = useRef<{ x: number; w: number } | null>(null);

  const onPointerDown = (e: ReactPointerEvent<HTMLSpanElement>) => {
    if (collapsed) return;
    e.preventDefault();
    e.stopPropagation();
    e.currentTarget.setPointerCapture(e.pointerId);
    drag.current = { x: e.clientX, w: currentWidth };
  };
  const onPointerMove = (e: ReactPointerEvent<HTMLSpanElement>) => {
    if (!drag.current) return;
    onResize(drag.current.w + (e.clientX - drag.current.x), false);
  };
  const endDrag = (e: ReactPointerEvent<HTMLSpanElement>) => {
    if (!drag.current) return;
    const finalW = drag.current.w + (e.clientX - drag.current.x);
    drag.current = null;
    try {
      e.currentTarget.releasePointerCapture(e.pointerId);
    } catch {
      /* already released */
    }
    onResize(finalW, true);
  };

  return (
    <th
      className="group/th relative border-b border-[var(--tt-outline)] px-2 py-2 font-semibold"
      style={collapsed ? { width: COLLAPSED_WIDTH, padding: 0 } : undefined}
      title={collapsed ? `Expand ${label}` : id === "tests" ? TESTS_HINT : label}
    >
      {collapsed ? (
        <button
          type="button"
          onClick={onToggleCollapsed}
          aria-label={`Expand ${label} column`}
          className="flex h-full w-full items-center justify-center py-2 text-[var(--tt-text-muted)] hover:text-[var(--tt-text-primary)]"
        >
          <ChevronRight className="h-3.5 w-3.5" />
        </button>
      ) : (
        <div className="flex items-center gap-1">
          <button
            type="button"
            onClick={onSort}
            className="flex min-w-0 flex-1 items-center gap-1 truncate hover:text-[var(--tt-text-primary)]"
            aria-label={`Sort by ${label}${sortDir === "asc" ? " descending" : " ascending"}`}
            title={`Sort by ${label}`}
          >
            <span className="truncate">{label}</span>
            {sortDir === "asc" ? (
              <ArrowUp className="h-3 w-3 shrink-0 text-[var(--tt-primary)]" />
            ) : sortDir === "desc" ? (
              <ArrowDown className="h-3 w-3 shrink-0 text-[var(--tt-primary)]" />
            ) : (
              <ArrowUpDown className="h-3 w-3 shrink-0 opacity-0 group-hover/th:opacity-40" />
            )}
          </button>
          <button
            type="button"
            onClick={onToggleCollapsed}
            aria-label={`Collapse ${label} column`}
            title={`Collapse ${label} column`}
            className="shrink-0 text-[var(--tt-text-faint)] hover:text-[var(--tt-text-primary)]"
          >
            <ChevronLeft className="h-3 w-3" />
          </button>
          {/* Drag-to-resize handle on the column's right edge. */}
          <span
            role="separator"
            aria-orientation="vertical"
            aria-label={`Resize ${label} column`}
            onPointerDown={onPointerDown}
            onPointerMove={onPointerMove}
            onPointerUp={endDrag}
            onPointerCancel={endDrag}
            className="absolute right-0 top-0 h-full w-1.5 cursor-col-resize hover:bg-[var(--tt-info)]/50"
          />
        </div>
      )}
    </th>
  );
}

// ---------------------------------------------------------------------------
// Badge helpers — map WI type/state strings to CSS class names
// ---------------------------------------------------------------------------
function wiTypeBadgeClass(t: string): string {
  const k = (t || "").toLowerCase();
  if (k.includes("story") || k.includes("user story")) return "tt-badge-story";
  if (k.includes("bug") || k.includes("issue")) return "tt-badge-bug";
  if (k.includes("task")) return "tt-badge-task";
  if (k.includes("epic")) return "tt-badge-epic";
  if (k.includes("feature")) return "tt-badge-feature";
  return "tt-badge-neutral";
}

function wiTypeBorderClass(t: string): string {
  const k = (t || "").toLowerCase();
  if (k.includes("story") || k.includes("user story")) return "tt-wi-type-story";
  if (k.includes("bug") || k.includes("issue")) return "tt-wi-type-bug";
  if (k.includes("task")) return "tt-wi-type-task";
  if (k.includes("epic")) return "tt-wi-type-epic";
  if (k.includes("feature")) return "tt-wi-type-feature";
  return "";
}

/** Title text color by work-item type (desktop board_grid._type_color). */
function titleTypeColor(t: string): string {
  const k = (t || "").toLowerCase();
  if (k.includes("bug") || k.includes("defect") || k.includes("issue"))
    return "var(--tt-type-bug)";
  if (k.includes("story") || k.includes("enhancement"))
    return "var(--tt-type-story)";
  if (k.includes("test case") || k.includes("case")) return "var(--tt-primary)";
  if (k.includes("task")) return "var(--tt-type-task)";
  if (k.includes("epic")) return "var(--tt-type-epic)";
  if (k.includes("feature")) return "var(--tt-type-feature)";
  return "var(--tt-text-primary)";
}

  /**
   * Total test cases traced to a work item: tool-generated + linked in the
   * tracker (ADO "Tested By" / JIRA test links). Execution status remains in
   * Last Run.
   */
  function CoverageCell({
  count,
  hasData,
  }: {
  count: number;
  hasData: boolean;
  }) {
  if (count > 0)
  return (
  <span style={{ color: "var(--tt-success)" }}>
  {count} {count === 1 ? "test" : "tests"}
  </span>
  );
  if (!hasData)
  return <span style={{ color: "var(--tt-text-faint)" }}>—</span>;
  return <span style={{ color: "var(--tt-text-muted)" }}>None</span>;
  }

/** Last-Run cell — "Pass" (green) / "Fail" (red) / "-" (no result). */
function LastRunCell({
  status,
  hasData,
}: {
  status: "pass" | "fail" | null;
  hasData: boolean;
}) {
  if (!hasData) return <span style={{ color: "var(--tt-text-faint)" }}>—</span>;
  if (status === "pass")
    return <span style={{ color: "var(--tt-success)" }}>Pass</span>;
  if (status === "fail")
    return <span style={{ color: "var(--tt-danger)" }}>Fail</span>;
  return <span style={{ color: "var(--tt-text-muted)" }}>-</span>;
}

function wiStateBadgeClass(s: string): string {
  const k = (s || "").toLowerCase();
  if (k === "active" || k === "in progress" || k === "in review") return "tt-badge-success";
  if (k === "resolved" || k === "done" || k === "closed") return "tt-badge-info";
  if (k === "new" || k === "proposed" || k === "to do") return "tt-badge-warn";
  if (k === "removed") return "tt-badge-danger";
  return "tt-badge-neutral";
}

function LaneGroup({
  lane,
  laneRows,
  allChecked,
  someChecked,
  collapsed,
  selected,
  activeWiId,
  testCounts,
  collapsedCols,
  hasCoverageData,
  runStatus,
  hasRunData,
  settings,
  onToggleCollapsed,
  onToggleLane,
  onToggleRow,
  onActivate,
}: {
  lane: string;
  laneRows: WorkItemRow[];
  allChecked: boolean;
  someChecked: boolean;
  collapsed: boolean;
  selected: Set<WiId>;
  activeWiId: WiId | null;
  testCounts: Map<string, number>;
  collapsedCols: Partial<Record<BoardColumnId, boolean>>;
  hasCoverageData: boolean;
  runStatus: Map<string, "pass" | "fail">;
  hasRunData: boolean;
  settings: SettingsResponse | null;
  onToggleCollapsed: () => void;
  onToggleLane: (on: boolean) => void;
  onToggleRow: (id: WiId, on: boolean) => void;
  onActivate: (id: WiId) => void;
}) {
  // Placeholder shown in a collapsed column's cell.
  const dot = (on: boolean | undefined) =>
    on ? <span className="text-[var(--tt-text-faint)]">·</span> : null;
  return (
    <>
      <tr className="tt-group-row border-t border-[var(--tt-outline)] first:border-t-0">
        <td className="px-2 py-2">
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
        <td colSpan={8} className="px-2 py-2">
          <button
            type="button"
            onClick={onToggleCollapsed}
            aria-expanded={!collapsed}
            aria-label={`${collapsed ? "Expand" : "Collapse"} ${lane} group`}
            className="flex w-full items-center gap-2 text-left"
          >
            {collapsed ? (
              <ChevronRight className="h-3.5 w-3.5 text-[var(--tt-text-muted)]" />
            ) : (
              <ChevronDown className="h-3.5 w-3.5 text-[var(--tt-text-muted)]" />
            )}
            <span className="text-xs font-bold uppercase tracking-wide text-[var(--tt-text-secondary)]">
              {lane}
            </span>
            <span className="tt-badge tt-badge-neutral">{laneRows.length}</span>
          </button>
        </td>
      </tr>
      {!collapsed &&
        laneRows.map((r) => {
        const isActive = r.wi_id === activeWiId;
        const typeBadgeClass = wiTypeBadgeClass(r.wi_type);
        const typeBorderClass = wiTypeBorderClass(r.wi_type);
        const stateBadgeClass = wiStateBadgeClass(r.state);
        return (
          <tr
            key={r.wi_id}
            onClick={() => onActivate(r.wi_id)}
            tabIndex={0}
            aria-selected={isActive}
            aria-label={`View details for work item ${r.wi_id}${r.title ? `: ${r.title}` : ""}`}
            onKeyDown={(e) => {
              // Keyboard activation (WCAG 2.1.1). Only when the row itself is
              // focused — don't hijack Space/Enter from the inner checkbox.
              if (
                e.target === e.currentTarget &&
                (e.key === "Enter" || e.key === " ")
              ) {
                e.preventDefault();
                onActivate(r.wi_id);
              }
            }}
            className={`cursor-pointer border-b border-[var(--tt-outline-soft)] border-l-[3px] transition-colors outline-none focus-visible:ring-2 focus-visible:ring-[var(--tt-focus,#3a6ea5)] focus-visible:ring-inset ${typeBorderClass} ${
              isActive ? "tt-row-selected" : "hover:bg-[var(--tt-surface-container)]"
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
            {/* ID cell — clickable link to source system */}
            <td className="truncate whitespace-nowrap px-2 py-1.5">
              {collapsedCols.id ? (
                dot(true)
              ) : (
                <WiIdLink wiId={r.wi_id} settings={settings} />
              )}
            </td>
            {/* Title — colored by work-item type (desktop parity: green
                stories, red bugs, blue test cases) */}
            <td
              className="truncate px-2 py-1.5 text-sm font-medium"
              style={{ color: titleTypeColor(r.wi_type) }}
              title={r.title}
            >
              {collapsedCols.title ? dot(true) : r.title}
            </td>
            {/* Type badge */}
            <td className="truncate whitespace-nowrap px-2 py-1.5">
              {collapsedCols.type ? (
                dot(true)
              ) : (
                <span className={`tt-badge ${typeBadgeClass}`}>{r.wi_type}</span>
              )}
            </td>
            {/* State badge */}
            <td className="truncate whitespace-nowrap px-2 py-1.5">
              {collapsedCols.state ? (
                dot(true)
              ) : (
                <span className={`tt-badge ${stateBadgeClass}`}>
                  {r.state || "n/a"}
                </span>
              )}
            </td>
            <td className="truncate whitespace-nowrap px-2 py-1.5 text-xs text-[var(--tt-text-secondary)]">
              {collapsedCols.assignee ? dot(true) : r.assigned_to || "—"}
            </td>
            <td className="truncate whitespace-nowrap px-2 py-1.5 text-xs text-[var(--tt-text-muted)]">
              {collapsedCols.sprint ? dot(true) : r.board_lane || "—"}
            </td>
            {/* Generated-test traceability, not implementation coverage. */}
            <td
              className="truncate whitespace-nowrap px-2 py-1.5 text-xs"
              title="Runnable generated test cases traced to this work item"
            >
              {collapsedCols.tests ? (
                dot(true)
              ) : (
                <CoverageCell
                  count={testCounts.get(String(r.wi_id)) ?? 0}
                  hasData={hasCoverageData}
                />
              )}
            </td>
            {/* Last Run */}
            <td className="truncate whitespace-nowrap px-2 py-1.5 text-xs">
              {collapsedCols.lastRun ? (
                dot(true)
              ) : (
                <LastRunCell
                  status={
                    hasRunData ? runStatus.get(String(r.wi_id)) ?? null : null
                  }
                  hasData={hasRunData}
                />
              )}
            </td>
          </tr>
        );
      })}
    </>
  );
}

// ---------------------------------------------------------------------------
// KPI Tiles — 5 aggregate buckets dynamically mapped from board columns
// ---------------------------------------------------------------------------
// Each bucket matches column names by keyword. Order matters: first match wins.
// Works across ADO and JIRA boards — classification is purely by column name content.
// ponytail: keyword matching; upgrade to configurable user-defined bucket map if
// customers need per-board overrides.
const KPI_BUCKETS = [
  {
    label: "Backlog",
    icon: Inbox,
    color: "var(--tt-text-secondary)",
    bg: "var(--tt-surface-container)",
    match: [
      "backlog", "new", "to do", "todo", "open", "reopened",
      "estimation", "ready for development", "in backlog",
      "selected for development", "funnel", "icebox", "parking lot",
    ],
  },
  {
    label: "Active",
    icon: Play,
    color: "var(--tt-primary)",
    bg: "color-mix(in srgb, var(--tt-primary) 12%, transparent)",
    match: [
      "active", "in development", "development", "in dev",
      "blocked in dev", "ready for qa", "in qa", "blocked in qa",
      "ready for acceptance", "in acceptance", "blocked in acceptance",
      "in progress", "in review", "code review",
      "in testing", "testing", "uat", "doing", "wip",
      "ready for review", "peer review", "blocked",
    ],
  },
  {
    label: "Passed",
    icon: CheckCircle2,
    color: "var(--tt-success)",
    bg: "color-mix(in srgb, var(--tt-success) 12%, transparent)",
    match: ["passed", "verified", "validated", "approved"],
  },
  {
    label: "Failed",
    icon: XCircle,
    color: "var(--tt-danger)",
    bg: "color-mix(in srgb, var(--tt-danger) 12%, transparent)",
    match: ["failed", "rejected", "won't do", "wont do", "cancelled"],
  },
  {
    label: "Closed",
    icon: Archive,
    color: "var(--tt-text-muted)",
    bg: "color-mix(in srgb, var(--tt-text-muted) 12%, transparent)",
    match: ["closed", "accepted", "done", "resolved", "removed", "released", "completed"],
  },
] as const;

// Match priority: specific terminal states first, then broad Active last.
// Display order (KPI_BUCKETS array) stays Backlog, Active, Passed, Failed, Closed.
const CLASSIFY_ORDER = ["Backlog", "Passed", "Failed", "Closed", "Active"] as const;
const CLASSIFY_MAP = new Map(KPI_BUCKETS.map((b) => [b.label, b.match]));

function classifyColumn(columnName: string): string {
  const lower = columnName.toLowerCase();
  for (const label of CLASSIFY_ORDER) {
    const keywords = CLASSIFY_MAP.get(label)!;
    if (keywords.some((kw) => lower.includes(kw))) return label;
  }
  return "Active";
}

/** Returns the set of board column names that belong to a given KPI bucket. */
function columnsForBucket(columns: { name: string }[], bucket: string): Set<string> {
  const result = new Set<string>();
  for (const c of columns) {
    if (classifyColumn(c.name) === bucket) result.add(c.name);
  }
  return result;
}

function KpiTiles({
  columns,
  rows,
  activeBucket,
  onSelect,
}: {
  columns: { name: string }[];
  rows: WorkItemRow[];
  activeBucket: string;
  onSelect: (bucket: string) => void;
}) {
  const tiles = useMemo(() => {
    const bucketCounts = new Map<string, number>();
    const known = new Set(columns.map((c) => c.name));
    for (const r of rows) {
      const col = r.board_column && known.has(r.board_column) ? r.board_column : NO_COLUMN;
      const bucket = classifyColumn(col);
      bucketCounts.set(bucket, (bucketCounts.get(bucket) ?? 0) + 1);
    }
    return KPI_BUCKETS.map((b) => ({
      ...b,
      count: bucketCounts.get(b.label) ?? 0,
    }));
  }, [columns, rows]);

  return (
    <div className="flex flex-wrap items-center gap-2">
      {tiles.map((t) => {
        const Icon = t.icon;
        const isActive = activeBucket === t.label;
        return (
          <button
            key={t.label}
            type="button"
            onClick={() => onSelect(t.label)}
            className="flex items-center gap-2 rounded-lg border px-3 py-1.5 text-xs font-medium transition-all hover:shadow-sm"
            style={{
              borderColor: isActive ? t.color : "var(--tt-outline)",
              background: isActive ? t.bg : "var(--tt-surface-base)",
              boxShadow: isActive ? `0 0 0 1px ${t.color}` : undefined,
            }}
            title={`${t.label}: ${t.count} work item(s) — click to filter`}
          >
            <Icon className="h-3.5 w-3.5" style={{ color: t.color }} />
            <span style={{ color: isActive ? t.color : "var(--tt-text-primary)" }}>
              {t.label}
            </span>
            <span
              className="rounded-full px-1.5 py-0.5 text-[10px] font-bold"
              style={{ background: t.bg, color: t.color }}
            >
              {t.count}
            </span>
          </button>
        );
      })}
    </div>
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

function WiIdLink({
  wiId,
  settings,
}: {
  wiId: WiId;
  settings: SettingsResponse | null;
}) {
  const isJira = typeof wiId === "string";
  let href: string | null = null;
  if (isJira) {
    const base = (settings?.jira_url ?? "").replace(/\/+$/, "");
    if (base) href = `${base}/browse/${encodeURIComponent(String(wiId))}`;
  } else if (settings?.organization) {
    href = `https://dev.azure.com/${encodeURIComponent(settings.organization)}/_workitems/edit/${wiId}`;
  }
  if (href) {
    return (
      <a
        href={href}
        target="_blank"
        rel="noopener noreferrer"
        className="font-mono text-xs font-bold text-[var(--tt-primary)] underline decoration-transparent hover:decoration-current"
        onClick={(e) => e.stopPropagation()}
        title={`Open ${wiId} in ${isJira ? "Jira" : "Azure DevOps"}`}
      >
        {wiId}
      </a>
    );
  }
  return (
    <span className="font-mono text-xs font-bold text-[var(--tt-primary)]">
      {wiId}
    </span>
  );
}
