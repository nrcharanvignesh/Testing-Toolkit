import ExcelJS from "exceljs";
import type {
  WorkItemRow,
  Board,
  SettingsResponse,
  E2ETestCase,
  E2ELastRun,
  WiId,
  WorkItemDetail,
} from "./agent-client";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function formatTimestamp(): string {
  const now = new Date();
  const day = now.getDate();
  const suffix =
    day % 10 === 1 && day !== 11
      ? "st"
      : day % 10 === 2 && day !== 12
        ? "nd"
        : day % 10 === 3 && day !== 13
          ? "th"
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
  return `Generated on ${day}${suffix} ${month}, ${year} ${time} ${tz}`.trim();
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

const META_FONT: Partial<ExcelJS.Font> = { size: 10, color: { argb: "FF555555" } };
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

function applyMetaBlock(
  ws: ExcelJS.Worksheet,
  opts: ExportBoardOpts,
  sheetTitle: string,
  ts: string,
  description?: string
): void {
  const source = opts.settings?.jira_configured ? "JIRA" : "Azure DevOps Board";
  ws.getRow(1).getCell(1).value = description || sheetTitle;
  ws.getRow(1).getCell(1).font = META_FONT;
  ws.getRow(2).getCell(1).value = `Project: ${opts.projectName}`;
  ws.getRow(2).getCell(1).font = { bold: true, size: 13 };
  ws.getRow(3).getCell(1).value = `Source: ${source}`;
  ws.getRow(3).getCell(1).font = META_FONT;
  ws.getRow(4).getCell(1).value = ts;
  ws.getRow(4).getCell(1).font = META_FONT;
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

function buildBoardSheet(
  wb: ExcelJS.Workbook,
  sheetName: string,
  opts: ExportBoardOpts,
  rels?: WiRelationships
): void {
  const ws = wb.addWorksheet(sheetName.slice(0, 31));
  const ts = formatTimestamp();
  const source = opts.settings?.jira_configured ? "JIRA" : "Azure DevOps Board";

  // Row 1: sheet description
  ws.getRow(1).getCell(1).value = "Board Export - All work items for the selected board with current filter state";
  ws.getRow(1).getCell(1).font = META_FONT;

  // Row 2: "Project: {name}"
  ws.getRow(2).getCell(1).value = `Project: ${opts.projectName}`;
  ws.getRow(2).getCell(1).font = { bold: true, size: 13 };

  // Row 3: "Source: Azure DevOps Board" or "Source: JIRA"
  ws.getRow(3).getCell(1).value = `Source: ${source}`;
  ws.getRow(3).getCell(1).font = META_FONT;

  // Row 4: "Board: {name}"
  ws.getRow(4).getCell(1).value = `Board: ${opts.boardName}`;
  ws.getRow(4).getCell(1).font = { bold: true, size: 11 };

  // Row 5: timestamp
  ws.getRow(5).getCell(1).value = ts;
  ws.getRow(5).getCell(1).font = META_FONT;

  // Filters row: only show if at least one filter is active
  const activeFilters: string[] = [];
  if (opts.filters.search) activeFilters.push(`Search: "${opts.filters.search}"`);
  if (opts.filters.type !== "All") activeFilters.push(`Type: ${opts.filters.type}`);
  if (opts.filters.assignee !== "All") activeFilters.push(`Assignee: ${opts.filters.assignee}`);
  if (opts.filters.sprint !== "All") activeFilters.push(`Sprint: ${opts.filters.sprint}`);
  if (opts.filters.column !== "All") activeFilters.push(`Column: ${opts.filters.column}`);

  let headerRowNum: number;
  if (activeFilters.length > 0) {
    ws.getRow(6).getCell(1).value = `Filters: ${activeFilters.join(", ")}`;
    ws.getRow(6).getCell(1).font = META_FONT;
    headerRowNum = 7;
  } else {
    headerRowNum = 6;
  }

  // Column headers
  const hasRels = rels && (rels.parents.size > 0 || rels.children.size > 0);
  const headers = hasRels
    ? ["ID", "Title", "Type", "State", "Board Column", "Assignee", "Sprint", "Area Path", "Tags", "Linked TCs", "Parent", "Children"]
    : ["ID", "Title", "Type", "State", "Board Column", "Assignee", "Sprint", "Area Path", "Tags", "Linked TCs"];
  applyHeaderRow(ws, headerRowNum, headers);

  // Data rows
  opts.rows.forEach((r) => {
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
  });

  // Freeze through the header row (meta + header visible when scrolling)
  ws.views = [{ state: "frozen", xSplit: 0, ySplit: headerRowNum, topLeftCell: `A${headerRowNum + 1}` }];

  // Autofilter on data header row
  ws.autoFilter = {
    from: { row: headerRowNum, column: 1 },
    to: { row: headerRowNum + opts.rows.length, column: headers.length },
  };

  autoFitColumns(ws);
}

// ---------------------------------------------------------------------------
// Test Coverage sheet
// ---------------------------------------------------------------------------

function buildTestCoverageSheet(
  wb: ExcelJS.Workbook,
  opts: ExportBoardOpts
): void {
  const testCases = opts.testCases ?? [];
  const lastRun = opts.lastRun;
  if (testCases.length === 0 && !lastRun) return;

  const ws = wb.addWorksheet("Test Coverage");
  const ts = formatTimestamp();
  applyMetaBlock(ws, opts, "Test Coverage Report", ts, "Test Coverage - Work items mapped to generated test cases with last run pass/fail status");

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
  applyHeaderRow(ws, 5, headers);

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

  ws.views = [{ state: "frozen", xSplit: 0, ySplit: 5, topLeftCell: "A6" }];
  ws.autoFilter = {
    from: { row: 5, column: 1 },
    to: { row: 5 + opts.rows.length, column: headers.length },
  };
  autoFitColumns(ws);
}

// ---------------------------------------------------------------------------
// Traceability Matrix sheet
// ---------------------------------------------------------------------------

function buildTraceabilitySheet(
  wb: ExcelJS.Workbook,
  opts: ExportBoardOpts
): void {
  const testCases = opts.testCases ?? [];
  const lastRun = opts.lastRun;
  if (testCases.length === 0) return;

  const ws = wb.addWorksheet("Traceability Matrix");
  const ts = formatTimestamp();
  applyMetaBlock(ws, opts, "Traceability Matrix", ts, "Traceability Matrix - Work items cross-referenced with test cases showing pass/fail per cell");

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
  applyHeaderRow(ws, 5, headers);

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

  ws.views = [{ state: "frozen", xSplit: 3, ySplit: 5, topLeftCell: "D6" }];
  autoFitColumns(ws);
}

// ---------------------------------------------------------------------------
// Defect Density sheet
// ---------------------------------------------------------------------------

function buildDefectDensitySheet(
  wb: ExcelJS.Workbook,
  opts: ExportBoardOpts
): void {
  const bugs = opts.rows.filter(
    (r) => (r.wi_type || "").toLowerCase() === "bug"
  );
  if (bugs.length === 0) return;

  const ws = wb.addWorksheet("Defect Density");
  const ts = formatTimestamp();
  applyMetaBlock(ws, opts, "Defect Density Analysis", ts, "Defect Density - Bug count grouped by board column and sprint with percentage breakdown");

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
  let rowNum = 6;
  applyHeaderRow(ws, rowNum, ["Board Column", "Bug Count", "% of Total"]);
  rowNum++;
  for (const [col, count] of [...byColumn.entries()].sort((a, b) => b[1] - a[1])) {
    const pct = bugs.length > 0 ? Math.round((count / bugs.length) * 100) : 0;
    const dataRow = ws.getRow(rowNum);
    dataRow.getCell(1).value = col;
    dataRow.getCell(2).value = count;
    dataRow.getCell(3).value = `${pct}%`;
    // Conditional formatting: high density = red
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
  applyHeaderRow(ws, rowNum, ["Sprint / Iteration", "Bug Count", "% of Total"]);
  rowNum++;
  for (const [sprint, count] of [...bySprint.entries()].sort((a, b) => b[1] - a[1])) {
    const pct = bugs.length > 0 ? Math.round((count / bugs.length) * 100) : 0;
    const dataRow = ws.getRow(rowNum);
    dataRow.getCell(1).value = sprint;
    dataRow.getCell(2).value = count;
    dataRow.getCell(3).value = `${pct}%`;
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

  ws.views = [{ state: "frozen", xSplit: 0, ySplit: 5, topLeftCell: "A6" }];
  autoFitColumns(ws);
}

// ---------------------------------------------------------------------------
// Pivot-Ready Data sheet (fully denormalized)
// ---------------------------------------------------------------------------

function buildPivotDataSheet(
  wb: ExcelJS.Workbook,
  opts: ExportBoardOpts,
  rels?: WiRelationships
): void {
  const ws = wb.addWorksheet("Pivot Data");
  const ts = formatTimestamp();
  applyMetaBlock(ws, opts, "Pivot-Ready Data", ts, "Pivot Data - Denormalized flat table with all WI fields, test counts, and relationships for Excel pivot tables");

  // Build lookups
  const testCases = opts.testCases ?? [];
  const lastRun = opts.lastRun;

  const tcCountByWi = new Map<string, number>();
  for (const tc of testCases) {
    if (tc.step_count > 0) {
      const key = String(tc.wi_id);
      tcCountByWi.set(key, (tcCountByWi.get(key) ?? 0) + 1);
    }
  }

  const tcIdToWi = new Map<string, string>();
  for (const tc of testCases) {
    if (tc.tc_id) tcIdToWi.set(tc.tc_id, String(tc.wi_id));
    tcIdToWi.set(String(tc.index), String(tc.wi_id));
  }
  const passCountByWi = new Map<string, number>();
  const failCountByWi = new Map<string, number>();
  for (const r of lastRun?.results ?? []) {
    const wiId = tcIdToWi.get(r.tc_id) ?? r.tc_id;
    const st = (r.status || "").toLowerCase();
    if (st === "pass") passCountByWi.set(wiId, (passCountByWi.get(wiId) ?? 0) + 1);
    else if (st === "fail" || st === "error") failCountByWi.set(wiId, (failCountByWi.get(wiId) ?? 0) + 1);
  }

  // Header
  const hasRels = rels && (rels.parents.size > 0 || rels.children.size > 0);
  const headers = [
    "ID", "Title", "Type", "State", "Board Column", "Assignee",
    "Sprint", "Area Path", "Tags", "Linked TCs", "Generated TCs",
    "Last Run Passed", "Last Run Failed", "Coverage Status", "Is Bug",
    ...(hasRels ? ["Parent IDs", "Child IDs", "Related IDs"] : []),
  ];
  applyHeaderRow(ws, 5, headers);

  // Data
  opts.rows.forEach((r) => {
    const key = String(r.wi_id);
    const linked = r.linked_test_case_count ?? 0;
    const generated = tcCountByWi.get(key) ?? 0;
    const totalTc = Math.max(linked, generated);
    const pass = passCountByWi.get(key) ?? 0;
    const fail = failCountByWi.get(key) ?? 0;
    const status = totalTc === 0 ? "No Tests" : fail > 0 ? "Failing" : pass > 0 ? "Passing" : "Not Run";
    const isBug = (r.wi_type || "").toLowerCase() === "bug" ? "Yes" : "No";

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
      generated,
      pass,
      fail,
      status,
      isBug,
    ];
    if (hasRels) {
      rowData.push((rels!.parents.get(key) ?? []).map((p) => String(p.id)).join(", "));
      rowData.push((rels!.children.get(key) ?? []).map((c) => String(c.id)).join(", "));
      rowData.push((rels!.related.get(key) ?? []).map((rel) => String(rel.id)).join(", "));
    }

    const dataRow = ws.addRow(rowData);

    // Hyperlink
    const url = wiUrl(r, opts.settings);
    if (url) {
      dataRow.getCell(1).value = { text: String(r.wi_id), hyperlink: url };
      dataRow.getCell(1).font = { color: { argb: "FF0563C1" }, underline: true, size: 11 };
    }

    // Conditional formatting on coverage status (col 14)
    const sFmt = statusFmt(status === "No Tests" ? "fail" : status === "Failing" ? "fail" : status === "Passing" ? "pass" : null);
    dataRow.getCell(14).fill = sFmt.fill as ExcelJS.Fill;
    dataRow.getCell(14).font = sFmt.font;
  });

  ws.views = [{ state: "frozen", xSplit: 0, ySplit: 5, topLeftCell: "A6" }];
  ws.autoFilter = {
    from: { row: 5, column: 1 },
    to: { row: 5 + opts.rows.length, column: headers.length },
  };
  autoFitColumns(ws);
}

// ---------------------------------------------------------------------------
// Execution Results sheet (last run dump)
// ---------------------------------------------------------------------------

function buildExecutionHistorySheet(
  wb: ExcelJS.Workbook,
  opts: ExportBoardOpts
): void {
  const lastRun = opts.lastRun;
  if (!lastRun || lastRun.results.length === 0) return;

  const ws = wb.addWorksheet("Execution Results");
  const ts = formatTimestamp();
  applyMetaBlock(ws, opts, "Execution Results", ts, "Execution Results - E2E test run results sorted by failures first with duration and parent WI link");

  // Run summary row (after meta block rows 1-4)
  const started = new Date(lastRun.started_at * 1000).toLocaleString();
  const finished = new Date(lastRun.finished_at * 1000).toLocaleString();
  ws.getRow(5).getCell(1).value =
    `Run: ${lastRun.run_id}  |  Started: ${started}  |  Finished: ${finished}  |  ` +
    `Total: ${lastRun.total}  Passed: ${lastRun.passed}  Failed: ${lastRun.failed}  Skipped: ${lastRun.skipped}`;
  ws.getRow(5).getCell(1).font = META_FONT;

  // Header
  const headers = ["TC ID", "Title", "Status", "Duration (ms)", "Parent WI"];
  applyHeaderRow(ws, 6, headers);

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

  ws.views = [{ state: "frozen", xSplit: 0, ySplit: 6, topLeftCell: "A7" }];
  ws.autoFilter = {
    from: { row: 6, column: 1 },
    to: { row: 6 + sorted.length, column: headers.length },
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
// Relationships sheet
// ---------------------------------------------------------------------------

function buildRelationshipsSheet(
  wb: ExcelJS.Workbook,
  opts: ExportBoardOpts,
  rels: WiRelationships
): void {
  const hasAny = rels.parents.size > 0 || rels.children.size > 0 || rels.related.size > 0;
  if (!hasAny) return;

  const ws = wb.addWorksheet("Relationships");
  const ts = formatTimestamp();
  applyMetaBlock(ws, opts, "Relationships", ts, "Relationships - Parent/child/related work item linkage map");

  const headers = ["ID", "Title", "Type", "Parent IDs", "Child IDs", "Related IDs"];
  applyHeaderRow(ws, 5, headers);

  opts.rows.forEach((r) => {
    const key = String(r.wi_id);
    const parentIds = (rels.parents.get(key) ?? []).map((p) => String(p.id)).join(", ");
    const childIds = (rels.children.get(key) ?? []).map((c) => String(c.id)).join(", ");
    const relatedIds = (rels.related.get(key) ?? []).map((rel) => String(rel.id)).join(", ");

    if (!parentIds && !childIds && !relatedIds) return;

    const dataRow = ws.addRow([
      String(r.wi_id),
      r.title,
      r.wi_type,
      parentIds,
      childIds,
      relatedIds,
    ]);

    // Hyperlink on ID
    const url = wiUrl(r, opts.settings);
    if (url) {
      dataRow.getCell(1).value = { text: String(r.wi_id), hyperlink: url };
      dataRow.getCell(1).font = { color: { argb: "FF0563C1" }, underline: true, size: 11 };
    }
  });

  ws.views = [{ state: "frozen", xSplit: 0, ySplit: 5, topLeftCell: "A6" }];
  autoFitColumns(ws);
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

  // Phase 2: Build workbook
  onProgress?.(0, 6, "Building workbook");
  const wb = new ExcelJS.Workbook();
  buildBoardSheet(wb, opts.boardName || "Board", opts, rels);
  onProgress?.(1, 6, "Building workbook");
  buildTestCoverageSheet(wb, opts);
  onProgress?.(2, 6, "Building workbook");
  buildTraceabilitySheet(wb, opts);
  onProgress?.(3, 6, "Building workbook");
  buildDefectDensitySheet(wb, opts);
  buildPivotDataSheet(wb, opts, rels);
  onProgress?.(4, 6, "Building workbook");
  buildExecutionHistorySheet(wb, opts);
  buildRelationshipsSheet(wb, opts, rels);
  onProgress?.(5, 6, "Building workbook");

  const buf = await wb.xlsx.writeBuffer();
  onProgress?.(6, 6, "Downloading");
  downloadBuffer(
    buf,
    `${opts.projectName}_${opts.boardName}_${Date.now()}.xlsx`
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
  const ts = formatTimestamp();

  // Sheet 1: Summary
  const summary = wb.addWorksheet("Summary");
  summary.getRow(1).getCell(1).value = opts.projectName;
  summary.getRow(1).getCell(1).font = { bold: true, size: 14 };
  summary.getRow(2).getCell(1).value = ts;
  summary.getRow(2).getCell(1).font = META_FONT;
  summary.getRow(3).getCell(1).value = `Boards exported: ${opts.boards.length}`;
  summary.getRow(3).getCell(1).font = META_FONT;

  // Summary table header
  const sHeaders = ["Board Name", "Work Items"];
  const sHeaderRow = summary.getRow(5);
  sHeaders.forEach((h, i) => {
    const cell = sHeaderRow.getCell(i + 1);
    cell.value = h;
    cell.font = HEADER_FONT_WHITE;
    cell.fill = HEADER_FILL;
  });

  opts.boards.forEach((b, idx) => {
    const name = b.board.team_name || b.board.name || b.board.label;
    summary.addRow([name, b.rows.length]);

    const sheetName = (name || `Board ${idx + 1}`).slice(0, 31);
    buildBoardSheet(wb, sheetName, {
      projectName: opts.projectName,
      boardName: name,
      rows: b.rows,
      kpiCounts: {},
      filters: { type: "All", assignee: "All", sprint: "All", column: "All", search: "" },
      settings: opts.settings,
    });
  });

  summary.views = [{ state: "frozen", xSplit: 0, ySplit: 4, topLeftCell: "A5" }];
  autoFitColumns(summary);

  const buf = await wb.xlsx.writeBuffer();
  downloadBuffer(buf, `${opts.projectName}_AllBoards_${Date.now()}.xlsx`);
}

// ---------------------------------------------------------------------------
// Browser download helper
// ---------------------------------------------------------------------------

function downloadBuffer(buf: ExcelJS.Buffer, filename: string): void {
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
}
