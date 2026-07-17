import ExcelJS from "exceljs";
import type { WorkItemRow, Board, SettingsResponse } from "./agent-client";

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

function autoFitColumns(ws: ExcelJS.Worksheet): void {
  ws.columns.forEach((col) => {
    let max = 10;
    col.eachCell?.({ includeEmpty: true }, (cell) => {
      const len = String(cell.value ?? "").length;
      if (len > max) max = len;
    });
    col.width = Math.min(max + 2, 60);
  });
}

const HEADER_FONT: Partial<ExcelJS.Font> = { bold: true, size: 11 };
const META_FONT: Partial<ExcelJS.Font> = { size: 10, color: { argb: "FF555555" } };

// ---------------------------------------------------------------------------
// Single board export
// ---------------------------------------------------------------------------

export interface ExportBoardOpts {
  projectName: string;
  boardName: string;
  rows: WorkItemRow[];
  kpiCounts: Record<string, number>;
  filters: { type: string; assignee: string; sprint: string; column: string; search: string };
  settings: SettingsResponse | null;
}

function buildBoardSheet(
  wb: ExcelJS.Workbook,
  sheetName: string,
  opts: ExportBoardOpts
): void {
  const ws = wb.addWorksheet(sheetName.slice(0, 31));
  const ts = formatTimestamp();

  // Row 1: project name
  ws.getRow(1).getCell(1).value = opts.projectName;
  ws.getRow(1).getCell(1).font = { bold: true, size: 13 };

  // Row 2: board name
  ws.getRow(2).getCell(1).value = opts.boardName;
  ws.getRow(2).getCell(1).font = { bold: true, size: 11 };

  // Row 3: timestamp
  ws.getRow(3).getCell(1).value = ts;
  ws.getRow(3).getCell(1).font = META_FONT;

  // Row 4: filters + KPIs
  const activeFilters: string[] = [];
  if (opts.filters.search) activeFilters.push(`Search: "${opts.filters.search}"`);
  if (opts.filters.type !== "All") activeFilters.push(`Type: ${opts.filters.type}`);
  if (opts.filters.assignee !== "All") activeFilters.push(`Assignee: ${opts.filters.assignee}`);
  if (opts.filters.sprint !== "All") activeFilters.push(`Sprint: ${opts.filters.sprint}`);
  if (opts.filters.column !== "All") activeFilters.push(`Column: ${opts.filters.column}`);

  const kpiParts = Object.entries(opts.kpiCounts).map(([k, v]) => `${k}: ${v}`);
  let row4 = activeFilters.length
    ? `Filters: ${activeFilters.join(", ")}`
    : "Filters: None";
  if (kpiParts.length) row4 += `  |  KPIs: ${kpiParts.join(", ")}`;
  ws.getRow(4).getCell(1).value = row4;
  ws.getRow(4).getCell(1).font = META_FONT;

  // Row 5: column headers
  const headers = ["ID", "Title", "Type", "State", "Board Column", "Assignee", "Sprint", "Area Path", "Tags"];
  const headerRow = ws.getRow(5);
  headers.forEach((h, i) => {
    const cell = headerRow.getCell(i + 1);
    cell.value = h;
    cell.font = HEADER_FONT;
    cell.fill = {
      type: "pattern",
      pattern: "solid",
      fgColor: { argb: "FF2B3A52" },
    };
    cell.font = { bold: true, size: 11, color: { argb: "FFFFFFFF" } };
  });

  // Data rows
  opts.rows.forEach((r) => {
    const url = wiUrl(r, opts.settings);
    const dataRow = ws.addRow([
      String(r.wi_id),
      r.title,
      r.wi_type,
      r.state,
      r.board_column,
      r.assigned_to || "",
      r.board_lane || r.iteration_path || "",
      r.area_path || "",
      (r.tags ?? []).join(", "),
    ]);
    // Make ID cell a hyperlink
    if (url) {
      const idCell = dataRow.getCell(1);
      idCell.value = { text: String(r.wi_id), hyperlink: url };
      idCell.font = { color: { argb: "FF0563C1" }, underline: true, size: 11 };
    }
  });

  // Freeze rows 1-4 (header area)
  ws.views = [{ state: "frozen", xSplit: 0, ySplit: 4, topLeftCell: "A5" }];

  // Autofilter on data header row (row 5)
  ws.autoFilter = {
    from: { row: 5, column: 1 },
    to: { row: 5 + opts.rows.length, column: headers.length },
  };

  autoFitColumns(ws);

  // Adjust row heights for the meta section
  ws.getRow(1).height = 22;
  ws.getRow(2).height = 18;
  ws.getRow(3).height = 16;
  ws.getRow(4).height = 16;
}

export async function exportSingleBoard(opts: ExportBoardOpts): Promise<void> {
  const wb = new ExcelJS.Workbook();
  buildBoardSheet(wb, opts.boardName || "Board", opts);
  const buf = await wb.xlsx.writeBuffer();
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
    cell.font = { bold: true, size: 11, color: { argb: "FFFFFFFF" } };
    cell.fill = {
      type: "pattern",
      pattern: "solid",
      fgColor: { argb: "FF2B3A52" },
    };
  });

  opts.boards.forEach((b, idx) => {
    const name = b.board.team_name || b.board.name || b.board.label;
    summary.addRow([name, b.rows.length]);

    // Each board gets its own sheet
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
