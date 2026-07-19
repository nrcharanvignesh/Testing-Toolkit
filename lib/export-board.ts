import ExcelJS from "exceljs";
import {
  agent,
  type WorkItemRow,
  type Board,
  type SettingsResponse,
  type E2ETestCase,
  type E2ELastRun,
  type WiId,
  type WorkItemDetail,
} from "./agent-client";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function fileTimestamp(): string {
  const now = new Date();
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}_${pad(now.getHours())}${pad(now.getMinutes())}`;
}

function formatTimestamp(): string {
  const now = new Date();
  const day = now.getDate();
  const suffix =
    day % 10 === 1 && day !== 11
      ? "st"
      : day % 10 === 2 && day !== 12
        ? "nd"
        : day % 10 === 3 && day !== 13
          ? "rd"
          : "th";
  const month = now.toLocaleString("en-US", { month: "long" });
  const year = now.getFullYear();
  const time = now.toLocaleString("en-US", {
    hour: "numeric",
    minute: "2-digit",
    hour12: true,
  });
  const tz =
    Intl.DateTimeFormat("en-US", { timeZoneName: "short" })
      .formatToParts(now)
      .find((p) => p.type === "timeZoneName")?.value ?? "";
  return `${day}${suffix} ${month}, ${year} ${time} ${tz}`.trim();
}

function wiUrl(
  row: WorkItemRow,
  settings: SettingsResponse | null
): string | null {
  const isJira = typeof row.wi_id === "string";
  if (isJira) {
    const base = (settings?.jira_url ?? "").replace(/\/+$/, "");
    if (!base) return null;
    return `${base}/browse/${encodeURIComponent(String(row.wi_id))}`;
  }
  const org = settings?.organization;
  if (!org) return null;
  return `https://dev.azure.com/${encodeURIComponent(org)}/_workitems/edit/${row.wi_id}`;
}

function wiUrlFromId(
  wiId: string | number,
  settings: SettingsResponse | null
): string | null {
  const isJira = typeof wiId === "string" && !/^\d+$/.test(wiId);
  if (isJira) {
    const base = (settings?.jira_url ?? "").replace(/\/+$/, "");
    if (!base) return null;
    return `${base}/browse/${encodeURIComponent(String(wiId))}`;
  }
  const org = settings?.organization;
  if (!org) return null;
  return `https://dev.azure.com/${encodeURIComponent(org)}/_workitems/edit/${wiId}`;
}

function autoFitColumns(ws: ExcelJS.Worksheet): void {
  ws.columns.forEach((col) => {
    let max = 10;
    col.eachCell?.({ includeEmpty: false }, (cell) => {
      const val = cell.value;
      const text = typeof val === "object" && val !== null && "text" in val
        ? String((val as { text: string }).text)
        : String(val ?? "");
      const len = text.length;
      if (len > max) max = len;
    });
    col.width = Math.min(max + 2, 50);
  });
  // Vertical fit: wrap text for data rows and let Excel auto-size row height
  ws.eachRow({ includeEmpty: false }, (row) => {
    row.alignment = { vertical: "top", wrapText: true };
  });
}

const META_FONT: Partial<ExcelJS.Font> = { size: 11, color: { argb: "FF555555" } };
const HEADER_FILL: ExcelJS.Fill = {
  type: "pattern",
  pattern: "solid",
  fgColor: { argb: "FF2B3A52" },
};
const HEADER_FONT_WHITE: Partial<ExcelJS.Font> = {
  bold: true,
  size: 11,
  color: { argb: "FFFFFFFF" },
};

// Conditional formatting colors
const CF_RED: Partial<ExcelJS.Fill> = {
  type: "pattern",
  pattern: "solid",
  fgColor: { argb: "FFFCE4EC" },
};
const CF_AMBER: Partial<ExcelJS.Fill> = {
  type: "pattern",
  pattern: "solid",
  fgColor: { argb: "FFFFF8E1" },
};
const CF_GREEN: Partial<ExcelJS.Fill> = {
  type: "pattern",
  pattern: "solid",
  fgColor: { argb: "FFE8F5E9" },
};
const CF_RED_FONT: Partial<ExcelJS.Font> = { color: { argb: "FFC62828" }, size: 11 };
const CF_AMBER_FONT: Partial<ExcelJS.Font> = { color: { argb: "FFF57F17" }, size: 11 };
const CF_GREEN_FONT: Partial<ExcelJS.Font> = { color: { argb: "FF2E7D32" }, size: 11 };

function applyHeaderRow(ws: ExcelJS.Worksheet, rowNum: number, headers: string[]): void {
  const row = ws.getRow(rowNum);
  headers.forEach((h, i) => {
    const cell = row.getCell(i + 1);
    cell.value = h;
    cell.font = HEADER_FONT_WHITE;
    cell.fill = HEADER_FILL;
  });
}

const META_BOLD: Partial<ExcelJS.Font> = { bold: true, size: 11 };

function applyMetaBlock(
  ws: ExcelJS.Worksheet,
  opts: ExportBoardOpts,
  ts: string
): void {
  const source = opts.settings?.jira_configured ? "JIRA" : "Azure DevOps Board";
  ws.getRow(1).getCell(1).value = "Project";
  ws.getRow(1).getCell(1).font = META_BOLD;
  ws.getRow(1).getCell(2).value = opts.projectName;
  ws.getRow(1).getCell(2).font = META_BOLD;

  ws.getRow(2).getCell(1).value = "Source";
  ws.getRow(2).getCell(1).font = META_BOLD;
  ws.getRow(2).getCell(2).value = source;
  ws.getRow(2).getCell(2).font = META_BOLD;

  ws.getRow(3).getCell(1).value = "Generated on";
  ws.getRow(3).getCell(1).font = META_BOLD;
  ws.getRow(3).getCell(2).value = ts;
  ws.getRow(3).getCell(2).font = META_BOLD;
}

// Coverage threshold: 0 = red, 1-2 = amber, 3+ = green
function coverageFmt(count: number): { fill: Partial<ExcelJS.Fill>; font: Partial<ExcelJS.Font> } {
  if (count === 0) return { fill: CF_RED, font: CF_RED_FONT };
  if (count <= 2) return { fill: CF_AMBER, font: CF_AMBER_FONT };
  return { fill: CF_GREEN, font: CF_GREEN_FONT };
}

// Pass/fail cell formatting
function statusFmt(status: string | null): { fill: Partial<ExcelJS.Fill>; font: Partial<ExcelJS.Font> } {
  if (!status) return { fill: CF_AMBER, font: CF_AMBER_FONT };
  const s = status.toLowerCase();
  if (s === "pass") return { fill: CF_GREEN, font: CF_GREEN_FONT };
  if (s === "fail" || s === "error") return { fill: CF_RED, font: CF_RED_FONT };
  return { fill: CF_AMBER, font: CF_AMBER_FONT };
}

// ---------------------------------------------------------------------------
// Single board export
// ---------------------------------------------------------------------------

// Relationship data fetched per-WI via streaming detail calls
export interface WiRelationships {
  // wi_id -> { parents: [id, title?], children: [id, title?], related: [id, title?] }
  parents: Map<string, Array<{ id: WiId; url: string }>>;
  children: Map<string, Array<{ id: WiId; url: string }>>;
  related: Map<string, Array<{ id: WiId; url: string }>>;
}

export interface ExportBoardOpts {
  projectName: string;
  boardName: string;
  rows: WorkItemRow[];
  kpiCounts: Record<string, number>;
  filters: { type: string; assignee: string; sprint: string; column: string; search: string };
  settings: SettingsResponse | null;
  testCases?: E2ETestCase[];
  lastRun?: E2ELastRun | null;
  // Streaming relationship fetch
  fetchDetail?: (wiId: WiId) => Promise<WorkItemDetail>;
  onProgress?: (done: number, total: number, phase: string) => void;
}

const _BOTTOM_TYPES = new Set(["bug", "defect", "issue", "impediment", "risk"]);

function sortRows(rows: WorkItemRow[]): WorkItemRow[] {
  if (!rows || rows.length === 0) return [];
  return [...rows].sort((a, b) => {
    const aType = (a.wi_type || "").toLowerCase();
    const bType = (b.wi_type || "").toLowerCase();
    const aBottom = _BOTTOM_TYPES.has(aType) ? 1 : 0;
    const bBottom = _BOTTOM_TYPES.has(bType) ? 1 : 0;
    if (aBottom !== bBottom) return aBottom - bBottom;
    const typeCompare = aType.localeCompare(bType);
    if (typeCompare !== 0) return typeCompare;
    const aId = typeof a.wi_id === "number" ? a.wi_id : parseInt(String(a.wi_id), 10) || 0;
    const bId = typeof b.wi_id === "number" ? b.wi_id : parseInt(String(b.wi_id), 10) || 0;
    return aId - bId;
  });
}

function buildBoardSheet(
  wb: ExcelJS.Workbook,
  sheetName: string,
  opts: ExportBoardOpts,
  rels?: WiRelationships
): void {
  const ws = wb.addWorksheet(sheetName.slice(0, 31));
  const ts = formatTimestamp();
  const source = opts.settings?.jira_configured ? "JIRA" : "Azure DevOps Board";

  // Meta block: Col A = label, Col B = value (all bold)
  ws.getRow(1).getCell(1).value = "Project";
  ws.getRow(1).getCell(1).font = META_BOLD;
  ws.getRow(1).getCell(2).value = opts.projectName;
  ws.getRow(1).getCell(2).font = META_BOLD;

  ws.getRow(2).getCell(1).value = "Source";
  ws.getRow(2).getCell(1).font = META_BOLD;
  ws.getRow(2).getCell(2).value = source;
  ws.getRow(2).getCell(2).font = META_BOLD;

  ws.getRow(3).getCell(1).value = "Board";
  ws.getRow(3).getCell(1).font = META_BOLD;
  ws.getRow(3).getCell(2).value = opts.boardName;
  ws.getRow(3).getCell(2).font = META_BOLD;

  ws.getRow(4).getCell(1).value = "Generated on";
  ws.getRow(4).getCell(1).font = META_BOLD;
  ws.getRow(4).getCell(2).value = ts;
  ws.getRow(4).getCell(2).font = META_BOLD;

  // Filters row: only show if at least one filter is genuinely active (not "all"/"(all)")
  const _isAll = (v: string) => !v || v.toLowerCase() === "all" || v.toLowerCase() === "(all)";
  const activeFilters: string[] = [];
  if (opts.filters.search) activeFilters.push(`Search: "${opts.filters.search}"`);
  if (!_isAll(opts.filters.type)) activeFilters.push(`Type: ${opts.filters.type}`);
  if (!_isAll(opts.filters.assignee)) activeFilters.push(`Assignee: ${opts.filters.assignee}`);
  if (!_isAll(opts.filters.sprint)) activeFilters.push(`Sprint: ${opts.filters.sprint}`);
  if (!_isAll(opts.filters.column)) activeFilters.push(`Column: ${opts.filters.column}`);

  let headerRowNum: number;
  if (activeFilters.length > 0) {
    ws.getRow(5).getCell(1).value = "Filters";
    ws.getRow(5).getCell(1).font = META_BOLD;
    ws.getRow(5).getCell(2).value = activeFilters.join(", ");
    ws.getRow(5).getCell(2).font = META_BOLD;
    headerRowNum = 6;
  } else {
    headerRowNum = 5;
  }

  // Column headers
  const hasRels = rels && (rels.parents.size > 0 || rels.children.size > 0);
  const headers = hasRels
    ? ["ID", "Title", "Type", "State", "Board Column", "Assignee", "Sprint", "Area Path", "Tags", "Linked TCs", "Parent", "Children"]
    : ["ID", "Title", "Type", "State", "Board Column", "Assignee", "Sprint", "Area Path", "Tags", "Linked TCs"];
  applyHeaderRow(ws, headerRowNum, headers);

  // Data rows (sorted by WI Type, then creation order)
  const sortedRows = sortRows(opts.rows);
  sortedRows.forEach((r) => {
    const url = wiUrl(r, opts.settings);
    const linked = r.linked_test_case_count ?? 0;
    const key = String(r.wi_id);
    const parentStr = hasRels ? (rels!.parents.get(key) ?? []).map((p) => String(p.id)).join(", ") : undefined;
    const childStr = hasRels ? (rels!.children.get(key) ?? []).map((c) => String(c.id)).join(", ") : undefined;
    const rowData: (string | number)[] = [
      String(r.wi_id),
      r.title,
      r.wi_type,
      r.state,
      r.board_column,
      r.assigned_to || "",
      r.board_lane || r.iteration_path || "",
      r.area_path || "",
      (r.tags ?? []).join(", "),
      linked,
    ];
    if (hasRels) {
      rowData.push(parentStr || "");
      rowData.push(childStr || "");
    }
    const dataRow = ws.addRow(rowData);
    if (url) {
      const idCell = dataRow.getCell(1);
      idCell.value = { text: String(r.wi_id), hyperlink: url };
      idCell.font = { color: { argb: "FF0563C1" }, underline: true, size: 11 };
    }
    // Conditional formatting on linked TCs
    const fmt = coverageFmt(linked);
    dataRow.getCell(10).fill = fmt.fill as ExcelJS.Fill;
    dataRow.getCell(10).font = fmt.font;
    // Hyperlink parent IDs
    if (hasRels && parentStr) {
      const parentLinks = rels!.parents.get(key) ?? [];
      if (parentLinks.length === 1 && parentLinks[0].url) {
        dataRow.getCell(11).value = { text: parentStr, hyperlink: parentLinks[0].url };
        dataRow.getCell(11).font = { color: { argb: "FF0563C1" }, underline: true, size: 11 };
      }
    }
    // Hyperlink child IDs
    if (hasRels && childStr) {
      const childLinks = rels!.children.get(key) ?? [];
      if (childLinks.length === 1 && childLinks[0].url) {
        dataRow.getCell(12).value = { text: childStr, hyperlink: childLinks[0].url };
        dataRow.getCell(12).font = { color: { argb: "FF0563C1" }, underline: true, size: 11 };
      } else if (childLinks.length > 1) {
        // Multiple children: hyperlink first, show all IDs as text
        dataRow.getCell(12).value = { text: childStr, hyperlink: childLinks[0].url };
        dataRow.getCell(12).font = { color: { argb: "FF0563C1" }, underline: true, size: 11 };
      }
    }
  });

  // Freeze through the header row (meta + header visible when scrolling)
  ws.views = [{ state: "frozen", xSplit: 0, ySplit: headerRowNum, topLeftCell: `A${headerRowNum + 1}` }];

  autoFitColumns(ws);
}

// ---------------------------------------------------------------------------
// Test Coverage sheet
// ---------------------------------------------------------------------------

export function buildTestCoverageSheet(
  wb: ExcelJS.Workbook,
  opts: ExportBoardOpts
): void {
  const testCases = opts.testCases ?? [];
  const lastRun = opts.lastRun;
  if (testCases.length === 0 && !lastRun) return;

  const ws = wb.addWorksheet("Test Coverage");
  const ts = formatTimestamp();
  applyMetaBlock(ws, opts, ts);

  // Build lookup: wi_id -> { tcCount, passCount, failCount }
  const tcByWi = new Map<string, { count: number; pass: number; fail: number }>();
  for (const tc of testCases) {
    const key = String(tc.wi_id);
    const entry = tcByWi.get(key) ?? { count: 0, pass: 0, fail: 0 };
    if (tc.step_count > 0) entry.count++;
    tcByWi.set(key, entry);
  }

  // Map last run results to parent WI via tc_id -> testCase -> wi_id
  const tcIdToWi = new Map<string, string>();
  for (const tc of testCases) {
    if (tc.tc_id) tcIdToWi.set(tc.tc_id, String(tc.wi_id));
    tcIdToWi.set(String(tc.index), String(tc.wi_id));
  }
  for (const r of lastRun?.results ?? []) {
    const wiId = tcIdToWi.get(r.tc_id) ?? r.tc_id;
    const entry = tcByWi.get(wiId) ?? { count: 0, pass: 0, fail: 0 };
    const st = (r.status || "").toLowerCase();
    if (st === "pass") entry.pass++;
    else if (st === "fail" || st === "error") entry.fail++;
    tcByWi.set(wiId, entry);
  }

  // Header row at row 4
  const headers = ["ID", "Title", "Type", "Test Cases", "Passed", "Failed", "Coverage Status"];
  applyHeaderRow(ws, 4, headers);

  // Data rows
  opts.rows.forEach((r) => {
    const key = String(r.wi_id);
    const tc = tcByWi.get(key) ?? { count: 0, pass: 0, fail: 0 };
    const linked = r.linked_test_case_count ?? 0;
    const totalTc = Math.max(tc.count, linked);
    const status = totalTc === 0 ? "No Tests" : tc.fail > 0 ? "Failing" : tc.pass > 0 ? "Passing" : "Not Run";

    const dataRow = ws.addRow([
      String(r.wi_id),
      r.title,
      r.wi_type,
      totalTc,
      tc.pass,
      tc.fail,
      status,
    ]);

    // Hyperlink
    const url = wiUrl(r, opts.settings);
    if (url) {
      dataRow.getCell(1).value = { text: String(r.wi_id), hyperlink: url };
      dataRow.getCell(1).font = { color: { argb: "FF0563C1" }, underline: true, size: 11 };
    }

    // Conditional formatting on Test Cases count
    const fmt = coverageFmt(totalTc);
    dataRow.getCell(4).fill = fmt.fill as ExcelJS.Fill;
    dataRow.getCell(4).font = fmt.font;

    // Conditional formatting on status
    const sFmt = statusFmt(status === "No Tests" ? "fail" : status === "Failing" ? "fail" : status === "Passing" ? "pass" : null);
    dataRow.getCell(7).fill = sFmt.fill as ExcelJS.Fill;
    dataRow.getCell(7).font = sFmt.font;
  });

  ws.views = [{ state: "frozen", xSplit: 0, ySplit: 4, topLeftCell: "A5" }];
  ws.autoFilter = {
    from: { row: 4, column: 1 },
    to: { row: 4 + opts.rows.length, column: headers.length },
  };
  autoFitColumns(ws);
}

// ---------------------------------------------------------------------------
// Traceability Matrix sheet
// ---------------------------------------------------------------------------

export function buildTraceabilitySheet(
  wb: ExcelJS.Workbook,
  opts: ExportBoardOpts
): void {
  const testCases = opts.testCases ?? [];
  const lastRun = opts.lastRun;
  if (testCases.length === 0) return;

  const ws = wb.addWorksheet("Traceability Matrix");
  const ts = formatTimestamp();
  applyMetaBlock(ws, opts, ts);

  // Group TCs by parent WI
  const wiTcMap = new Map<string, E2ETestCase[]>();
  for (const tc of testCases) {
    const key = String(tc.wi_id);
    const arr = wiTcMap.get(key) ?? [];
    arr.push(tc);
    wiTcMap.set(key, arr);
  }

  // Build run status lookup: tc_id -> status
  const runStatusMap = new Map<string, string>();
  for (const r of lastRun?.results ?? []) {
    runStatusMap.set(r.tc_id, r.status);
  }

  // Collect all unique TC titles as columns
  const allTcTitles = [...new Set(testCases.map((tc) => tc.title || `TC-${tc.index}`))];
  // ponytail: flat column list; pivot table if > 50 TCs becomes unwieldy
  const maxTcCols = Math.min(allTcTitles.length, 30);
  const tcColHeaders = allTcTitles.slice(0, maxTcCols);

  // Header
  const headers = ["ID", "Title", "Type", ...tcColHeaders];
  applyHeaderRow(ws, 4, headers);

  // Data rows
  opts.rows.forEach((r) => {
    const key = String(r.wi_id);
    const tcs = wiTcMap.get(key) ?? [];
    const tcStatusByTitle = new Map<string, string>();
    for (const tc of tcs) {
      const title = tc.title || `TC-${tc.index}`;
      const tcId = tc.tc_id || String(tc.index);
      const status = runStatusMap.get(tcId) ?? "—";
      tcStatusByTitle.set(title, status);
    }

    const rowData: (string | number)[] = [String(r.wi_id), r.title, r.wi_type];
    for (const colTitle of tcColHeaders) {
      rowData.push(tcStatusByTitle.get(colTitle) ?? "");
    }
    const dataRow = ws.addRow(rowData);

    // Hyperlink on ID
    const url = wiUrl(r, opts.settings);
    if (url) {
      dataRow.getCell(1).value = { text: String(r.wi_id), hyperlink: url };
      dataRow.getCell(1).font = { color: { argb: "FF0563C1" }, underline: true, size: 11 };
    }

    // Conditional formatting on TC status cells
    for (let i = 0; i < tcColHeaders.length; i++) {
      const cellVal = String(dataRow.getCell(4 + i).value ?? "");
      if (cellVal && cellVal !== "—") {
        const fmt = statusFmt(cellVal);
        dataRow.getCell(4 + i).fill = fmt.fill as ExcelJS.Fill;
        dataRow.getCell(4 + i).font = fmt.font;
      }
    }
  });

  ws.views = [{ state: "frozen", xSplit: 3, ySplit: 4, topLeftCell: "D5" }];
  autoFitColumns(ws);
}

// ---------------------------------------------------------------------------
// Defect Density sheet
// ---------------------------------------------------------------------------

export function buildDefectDensitySheet(
  wb: ExcelJS.Workbook,
  opts: ExportBoardOpts
): void {
  const bugs = opts.rows.filter(
    (r) => (r.wi_type || "").toLowerCase() === "bug"
  );
  if (bugs.length === 0) return;

  const ws = wb.addWorksheet("Defect Density");
  const ts = formatTimestamp();
  applyMetaBlock(ws, opts, ts);

  // Group by Board Column
  const byColumn = new Map<string, number>();
  for (const b of bugs) {
    const col = b.board_column || "(none)";
    byColumn.set(col, (byColumn.get(col) ?? 0) + 1);
  }

  // Group by Sprint
  const bySprint = new Map<string, number>();
  for (const b of bugs) {
    const sprint = b.board_lane || b.iteration_path || "(none)";
    bySprint.set(sprint, (bySprint.get(sprint) ?? 0) + 1);
  }

  // Section 1: By Column
  let rowNum = 5;
  applyHeaderRow(ws, rowNum, ["Board Column", "Bug Count"]);
  rowNum++;
  for (const [col, count] of [...byColumn.entries()].sort((a, b) => b[1] - a[1])) {
    const dataRow = ws.getRow(rowNum);
    dataRow.getCell(1).value = col;
    dataRow.getCell(2).value = count;
    const fmt = count >= 5 ? { fill: CF_RED, font: CF_RED_FONT }
      : count >= 2 ? { fill: CF_AMBER, font: CF_AMBER_FONT }
        : { fill: CF_GREEN, font: CF_GREEN_FONT };
    dataRow.getCell(2).fill = fmt.fill as ExcelJS.Fill;
    dataRow.getCell(2).font = fmt.font;
    rowNum++;
  }

  // Spacer
  rowNum += 2;

  // Section 2: By Sprint
  applyHeaderRow(ws, rowNum, ["Sprint / Iteration", "Bug Count"]);
  rowNum++;
  for (const [sprint, count] of [...bySprint.entries()].sort((a, b) => b[1] - a[1])) {
    const dataRow = ws.getRow(rowNum);
    dataRow.getCell(1).value = sprint;
    dataRow.getCell(2).value = count;
    const fmt = count >= 5 ? { fill: CF_RED, font: CF_RED_FONT }
      : count >= 2 ? { fill: CF_AMBER, font: CF_AMBER_FONT }
        : { fill: CF_GREEN, font: CF_GREEN_FONT };
    dataRow.getCell(2).fill = fmt.fill as ExcelJS.Fill;
    dataRow.getCell(2).font = fmt.font;
    rowNum++;
  }

  // Spacer + summary
  rowNum += 2;
  ws.getRow(rowNum).getCell(1).value = `Total bugs: ${bugs.length}`;
  ws.getRow(rowNum).getCell(1).font = { bold: true, size: 11 };

  ws.views = [{ state: "frozen", xSplit: 0, ySplit: 4, topLeftCell: "A5" }];
  autoFitColumns(ws);
}

// ---------------------------------------------------------------------------
// Execution Results sheet (last run dump)
// ---------------------------------------------------------------------------

export function buildExecutionHistorySheet(
  wb: ExcelJS.Workbook,
  opts: ExportBoardOpts
): void {
  const lastRun = opts.lastRun;
  if (!lastRun || lastRun.results.length === 0) return;

  const ws = wb.addWorksheet("Execution Results");
  const ts = formatTimestamp();
  applyMetaBlock(ws, opts, ts);

  // Run summary row (after meta block rows 1-3)
  const started = new Date(lastRun.started_at * 1000).toLocaleString();
  const finished = new Date(lastRun.finished_at * 1000).toLocaleString();
  ws.getRow(4).getCell(1).value =
    `Run: ${lastRun.run_id}  |  Started: ${started}  |  Finished: ${finished}  |  ` +
    `Total: ${lastRun.total}  Passed: ${lastRun.passed}  Failed: ${lastRun.failed}  Skipped: ${lastRun.skipped}`;
  ws.getRow(4).getCell(1).font = META_FONT;

  // Header
  const headers = ["TC ID", "Title", "Status", "Duration (ms)", "Parent WI"];
  applyHeaderRow(ws, 5, headers);

  // Map tc_id to parent WI
  const testCases = opts.testCases ?? [];
  const tcIdToWi = new Map<string, string>();
  for (const tc of testCases) {
    if (tc.tc_id) tcIdToWi.set(tc.tc_id, String(tc.wi_id));
    tcIdToWi.set(String(tc.index), String(tc.wi_id));
  }

  // Data rows, sorted by status (fail first) then duration desc
  const sorted = [...lastRun.results].sort((a, b) => {
    const aFail = (a.status || "").toLowerCase() === "fail" || (a.status || "").toLowerCase() === "error" ? 0 : 1;
    const bFail = (b.status || "").toLowerCase() === "fail" || (b.status || "").toLowerCase() === "error" ? 0 : 1;
    if (aFail !== bFail) return aFail - bFail;
    return b.duration_ms - a.duration_ms;
  });

  for (const r of sorted) {
    const parentWi = tcIdToWi.get(r.tc_id) ?? "";
    const dataRow = ws.addRow([
      r.tc_id,
      r.tc_title,
      r.status,
      r.duration_ms,
      parentWi,
    ]);

    // Conditional formatting on status
    const fmt = statusFmt(r.status);
    dataRow.getCell(3).fill = fmt.fill as ExcelJS.Fill;
    dataRow.getCell(3).font = fmt.font;

    // Hyperlink on parent WI
    if (parentWi) {
      const url = wiUrlFromId(parentWi, opts.settings);
      if (url) {
        dataRow.getCell(5).value = { text: parentWi, hyperlink: url };
        dataRow.getCell(5).font = { color: { argb: "FF0563C1" }, underline: true, size: 11 };
      }
    }
  }

  ws.views = [{ state: "frozen", xSplit: 0, ySplit: 5, topLeftCell: "A6" }];
  ws.autoFilter = {
    from: { row: 5, column: 1 },
    to: { row: 5 + sorted.length, column: headers.length },
  };
  autoFitColumns(ws);
}

// ---------------------------------------------------------------------------
// Streaming relationship fetcher (batched parallel)
// ---------------------------------------------------------------------------

const BATCH_SIZE = 5;

async function fetchRelationships(
  rows: WorkItemRow[],
  fetchDetail: (wiId: WiId) => Promise<WorkItemDetail>,
  onProgress?: (done: number, total: number, phase: string) => void
): Promise<WiRelationships> {
  const parents = new Map<string, Array<{ id: WiId; url: string }>>();
  const children = new Map<string, Array<{ id: WiId; url: string }>>();
  const related = new Map<string, Array<{ id: WiId; url: string }>>();
  const total = rows.length;
  let done = 0;

  for (let i = 0; i < rows.length; i += BATCH_SIZE) {
    const batch = rows.slice(i, i + BATCH_SIZE);
    const results = await Promise.allSettled(
      batch.map((r) => fetchDetail(r.wi_id))
    );
    for (let j = 0; j < results.length; j++) {
      const result = results[j];
      if (result.status !== "fulfilled") continue;
      const detail = result.value;
      const key = String(detail.wi_id);
      for (const [name, linkId, url] of detail.related) {
        const lower = name.toLowerCase();
        if (lower.includes("parent")) {
          const arr = parents.get(key) ?? [];
          arr.push({ id: linkId, url });
          parents.set(key, arr);
        } else if (lower.includes("child")) {
          const arr = children.get(key) ?? [];
          arr.push({ id: linkId, url });
          children.set(key, arr);
        } else {
          const arr = related.get(key) ?? [];
          arr.push({ id: linkId, url });
          related.set(key, arr);
        }
      }
    }
    done += batch.length;
    onProgress?.(Math.min(done, total), total, "Fetching relationships");
  }
  return { parents, children, related };
}

// ---------------------------------------------------------------------------
// Public: Single board export (all sheets) - streaming with progress
// ---------------------------------------------------------------------------

export async function exportSingleBoard(opts: ExportBoardOpts): Promise<void> {
  const { onProgress, fetchDetail } = opts;

  // Phase 1: Fetch relationships if detail fetcher provided
  let rels: WiRelationships = { parents: new Map(), children: new Map(), related: new Map() };
  if (fetchDetail && opts.rows.length > 0) {
    onProgress?.(0, opts.rows.length, "Fetching relationships");
    rels = await fetchRelationships(opts.rows, fetchDetail, onProgress);
  }

  // Phase 2: Build workbook (single main sheet only)
  onProgress?.(0, 1, "Building workbook");
  const wb = new ExcelJS.Workbook();
  buildBoardSheet(wb, opts.boardName || "Board", opts, rels);

  const buf = await wb.xlsx.writeBuffer();
  onProgress?.(1, 1, "Downloading");
  downloadBuffer(
    buf,
    `${opts.projectName}_${opts.boardName}_${fileTimestamp()}.xlsx`,
    opts.projectName
  );
}

// ---------------------------------------------------------------------------
// Multi-board workbook export (all boards for a project)
// ---------------------------------------------------------------------------

export interface ExportAllBoardsOpts {
  projectName: string;
  boards: Array<{
    board: Board;
    rows: WorkItemRow[];
  }>;
  settings: SettingsResponse | null;
}

export async function exportAllBoards(opts: ExportAllBoardsOpts): Promise<void> {
  const wb = new ExcelJS.Workbook();
  const summaryWs = wb.addWorksheet("Summary");
  const usedNames = new Set<string>(["Summary"]);
  const index: Array<{ sheetName: string; boardName: string; rowCount: number }> = [];
  const ignored: Array<{ boardName: string }> = [];

  opts.boards.forEach((b, idx) => {
    try {
      const rawName = b.board?.team_name || b.board?.name || b.board?.label || "";
      const shortName = _stripProjectPrefix(rawName || `Board ${idx + 1}`, opts.projectName);
      const rows = b.rows ?? [];
      if (rows.length === 0) {
        ignored.push({ boardName: shortName });
        return;
      }
      const sheetName = _safeSheetName(shortName, usedNames);
      usedNames.add(sheetName);
      try {
        buildBoardSheet(wb, sheetName, {
          projectName: opts.projectName,
          boardName: rawName,
          rows,
          kpiCounts: {},
          filters: { type: "All", assignee: "All", sprint: "All", column: "All", search: "" },
          settings: opts.settings,
        });
      } catch {
        // Sheet build failed — already added, move on
      }
      index.push({ sheetName, boardName: shortName, rowCount: rows.length });
    } catch {
      ignored.push({ boardName: `Board ${idx + 1}` });
    }
  });

  try {
    _populateSummary(summaryWs, opts.projectName, index.map(i => ({ ...i, project: opts.projectName })), ignored);
  } catch {
    summaryWs.getRow(1).getCell(2).value = `${opts.projectName} - Export Summary`;
  }

  const buf = await wb.xlsx.writeBuffer();
  downloadBuffer(buf, `${opts.projectName}_AllBoards_${fileTimestamp()}.xlsx`, opts.projectName);
}

// ---------------------------------------------------------------------------
// All projects export (one sheet per board across all projects)
// ---------------------------------------------------------------------------

export interface ExportAllProjectsOpts {
  projects: Array<{
    projectName: string;
    boards: Array<{
      board: Board;
      rows: WorkItemRow[];
    }>;
  }>;
  settings: SettingsResponse | null;
}

export async function exportAllProjects(opts: ExportAllProjectsOpts): Promise<void> {
  // Reuse the exact same per-project workbook logic as exportAllBoards,
  // just stitched across multiple projects into one combined workbook.
  const wb = new ExcelJS.Workbook();
  const summaryWs = wb.addWorksheet("Summary");
  const usedNames = new Set<string>(["Summary"]);
  const index: Array<{ sheetName: string; project: string; boardName: string; rowCount: number }> = [];
  const ignored: Array<{ boardName: string; project?: string }> = [];

  for (const project of opts.projects) {
    // Same forEach logic as exportAllBoards, per project
    project.boards.forEach((b, idx) => {
      try {
        const rawName = b.board?.team_name || b.board?.name || b.board?.label || "";
        const shortName = _stripProjectPrefix(rawName || `Board ${idx + 1}`, project.projectName);
        const rows = b.rows ?? [];
        if (rows.length === 0) {
          ignored.push({ boardName: shortName, project: project.projectName });
          return;
        }
        const sheetName = _safeSheetName(shortName, usedNames);
        usedNames.add(sheetName);
        try {
          buildBoardSheet(wb, sheetName, {
            projectName: project.projectName,
            boardName: rawName,
            rows,
            kpiCounts: {},
            filters: { type: "All", assignee: "All", sprint: "All", column: "All", search: "" },
            settings: opts.settings,
          });
        } catch {
          // Sheet build failed — already added, move on
        }
        index.push({ sheetName, project: project.projectName, boardName: shortName, rowCount: rows.length });
      } catch {
        ignored.push({ boardName: `Board ${idx + 1}`, project: project.projectName });
      }
    });
  }

  try {
    _populateSummary(summaryWs, null, index, ignored);
  } catch {
    summaryWs.getRow(1).getCell(2).value = "All Projects - Export Summary";
  }

  const buf = await wb.xlsx.writeBuffer();
  downloadBuffer(buf, `AllProjects_${fileTimestamp()}.xlsx`, "AllProjects");
}

// ---------------------------------------------------------------------------
// Sheet naming + Summary helpers
// ---------------------------------------------------------------------------

const _INVALID_SHEET_CHARS = /[\\/*?:\[\]]/g;

function _stripProjectPrefix(boardName: string, projectName: string): string {
  let name = boardName;
  if (projectName && name.toLowerCase().startsWith(projectName.toLowerCase())) {
    name = name.slice(projectName.length).replace(/^[\s\-–—:]+/, "").trim();
  }
  return name || boardName;
}

function _safeSheetName(raw: string, used: Set<string>): string {
  let name = raw.replace(_INVALID_SHEET_CHARS, "").trim().slice(0, 31);
  if (!name) name = "Sheet";
  if (used.has(name)) {
    let n = 2;
    const base = name.slice(0, 28);
    while (used.has(`${base} ${n}`)) n++;
    name = `${base} ${n}`;
  }
  return name;
}

function _populateSummary(
  ws: ExcelJS.Worksheet,
  projectName: string | null,
  index: Array<{ sheetName: string; project?: string; boardName: string; rowCount: number }>,
  ignored?: Array<{ boardName: string; project?: string }>,
): void {
  // Meta info in Col B so Col A (#) stays narrow
  ws.getRow(1).getCell(2).value = projectName ? `${projectName} - Export Summary` : "All Projects - Export Summary";
  ws.getRow(1).getCell(2).font = { bold: true, size: 14 };
  ws.getRow(2).getCell(2).value = `Generated: ${formatTimestamp()}`;
  ws.getRow(2).getCell(2).font = { italic: true, size: 11, color: { argb: "FF666666" } };
  ws.getRow(3).getCell(2).value = `Total sheets: ${index.length}`;

  const hasProject = index.some(i => i.project && i.project !== projectName);
  const headers = hasProject
    ? ["#", "Project", "Board", "Work Items", "Sheet Link"]
    : ["#", "Board", "Work Items", "Sheet Link"];

  const headerRow = ws.getRow(5);
  headers.forEach((h, col) => {
    headerRow.getCell(col + 1).value = h;
    headerRow.getCell(col + 1).fill = {
      type: "pattern", pattern: "solid",
      fgColor: { argb: "FF2563EB" },
    };
    headerRow.getCell(col + 1).font = { bold: true, size: 11, color: { argb: "FFFFFFFF" } };
  });

  index.forEach((entry, i) => {
    const row = ws.getRow(6 + i);
    let col = 1;
    row.getCell(col++).value = i + 1;
    if (hasProject) row.getCell(col++).value = entry.project || "";
    row.getCell(col++).value = entry.boardName;
    row.getCell(col++).value = entry.rowCount;
    const linkCell = row.getCell(col);
    linkCell.value = { text: entry.sheetName, hyperlink: `#'${entry.sheetName}'!A1` };
    linkCell.font = { color: { argb: "FF0563C1" }, underline: true, size: 11 };
  });

  // Ignored boards section (0 work items — no sheet created)
  if (ignored && ignored.length > 0) {
    const gapRow = 6 + index.length + 1;
    ws.getRow(gapRow).getCell(2).value = "Ignored (0 work items)";
    ws.getRow(gapRow).getCell(2).font = { bold: true, size: 11, color: { argb: "FF888888" } };
    ignored.forEach((entry, i) => {
      const row = ws.getRow(gapRow + 1 + i);
      row.getCell(2).value = entry.project ? `${entry.project} / ${entry.boardName}` : entry.boardName;
      row.getCell(2).font = { italic: true, size: 10, color: { argb: "FF999999" } };
      row.getCell(3).value = 0;
      row.getCell(3).font = { size: 10, color: { argb: "FF999999" } };
    });
  }

  // Set Col A to a fixed narrow width for the # column
  ws.getColumn(1).width = 5;
  ws.views = [{ state: "frozen", xSplit: 0, ySplit: 5, topLeftCell: "A6" }];
  autoFitColumns(ws);
  // Re-enforce Col A narrow width after autoFit
  ws.getColumn(1).width = 5;
}

// ---------------------------------------------------------------------------
// Browser download helper + save to agent Outputs folder
// ---------------------------------------------------------------------------

function downloadBuffer(buf: ExcelJS.Buffer, filename: string, project?: string): void {
  const blob = new Blob([buf], {
    type: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
  });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
  // Best-effort save to agent Outputs folder
  agent.uploadArtifact(blob, filename, project || "").catch(() => {});
}
