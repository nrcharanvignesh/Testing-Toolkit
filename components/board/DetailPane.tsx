"use client";

import { useEffect, useMemo, useState } from "react";
import {
  RefreshCw,
  FileText,
  Download,
  ExternalLink,
  ImageIcon,
  Link as LinkIcon,
} from "lucide-react";
import {
  agent,
  TC_TYPES,
  TC_DISPLAY_NAME,
  type TcType,
  type WorkItemDetail,
  type ArtifactFile,
  type Attachment,
} from "@/lib/agent-client";
import { useAppState } from "@/lib/app-state";
import { COLOR_MUTED } from "@/lib/board-utils";

interface DetailPaneProps {
  activeWiId: number | null;
}

export function DetailPane({ activeWiId }: DetailPaneProps) {
  const { currentProject, settings, displayName, pushLog } = useAppState();
  const [mode, setMode] = useState<"detail" | "outputs">("detail");
  const [detail, setDetail] = useState<WorkItemDetail | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (activeWiId == null || !currentProject) {
      setDetail(null);
      return;
    }
    let cancelled = false;
    setLoading(true);
    agent
      .workItemDetail(currentProject, activeWiId)
      .then((d) => {
        if (!cancelled) setDetail(d);
      })
      .catch((e) => {
        if (!cancelled) {
          setDetail(null);
          pushLog("ERROR", `Load detail failed: ${(e as Error).message}`);
        }
      })
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
  }, [activeWiId, currentProject, pushLog]);

  const openInAdo = () => {
    if (!detail || !settings?.organization) return;
    const url = `https://dev.azure.com/${encodeURIComponent(
      settings.organization
    )}/_workitems/edit/${detail.wi_id}`;
    window.open(url, "_blank", "noopener");
  };

  // Desktop tabs: selected tab is gray/inset (not bright blue) — D01.
  const tabStyle = (active: boolean): React.CSSProperties =>
    active
      ? { background: "#252830", color: "#edf0f5", borderColor: "#2d313c" }
      : { background: "transparent", color: "#8a8f99" };

  return (
    <div className="tt-card flex h-full flex-col gap-2 p-2.5">
      <div className="flex items-center gap-2">
        <button
          className="rounded-md border px-3 py-1 text-xs font-medium"
          style={tabStyle(mode === "detail")}
          onClick={() => setMode("detail")}
        >
          Detail
        </button>
        <button
          className="rounded-md border px-3 py-1 text-xs font-medium"
          style={tabStyle(mode === "outputs")}
          onClick={() => setMode("outputs")}
        >
          Outputs
        </button>
        <div className="flex-1" />
        <button
          className="tt-btn-ghost !px-3 !py-1 text-xs"
          disabled={!detail}
          onClick={openInAdo}
        >
          Open in ADO
        </button>
      </div>

      <div className="min-h-0 flex-1 overflow-auto rounded-[10px] border border-[#2d313c] bg-[#13161d] p-4">
        {mode === "detail" ? (
          <DetailContent
            loading={loading}
            detail={detail}
            hasItem={activeWiId != null}
          />
        ) : (
          <OutputsContent
            project={currentProject}
            projectLabel={currentProject ? displayName(currentProject) : ""}
            pushLog={pushLog}
          />
        )}
      </div>
    </div>
  );
}

/** Leaf of a backslash/slash area or iteration path, e.g. ...\Abbott → Abbott. */
function pathTail(p: string): string {
  if (!p) return "";
  return p;
}

function DetailContent({
  loading,
  detail,
  hasItem,
}: {
  loading: boolean;
  detail: WorkItemDetail | null;
  hasItem: boolean;
}) {
  if (!hasItem)
    return (
      <p style={{ color: COLOR_MUTED }} className="text-sm leading-relaxed">
        Click a work item to see its full description, acceptance criteria,
        comments, inline images, attachments, and links.
      </p>
    );
  if (loading)
    return (
      <div className="flex items-center gap-2 text-sm text-muted-foreground">
        <RefreshCw className="h-4 w-4 animate-spin" /> Loading work item...
      </div>
    );
  if (!detail)
    return (
      <p style={{ color: COLOR_MUTED }} className="text-sm">
        Unable to load this work item.
      </p>
    );

  // Desktop metadata line: "User Story · State: Backlog · Column: ... · Assigned: ..."
  const metaParts: string[] = [];
  if (detail.wi_type) metaParts.push(detail.wi_type);
  if (detail.state) metaParts.push(`State: ${detail.state}`);
  if (detail.board_column) metaParts.push(`Column: ${detail.board_column}`);
  if (detail.assigned_to) metaParts.push(`Assigned: ${detail.assigned_to}`);

  return (
    <div className="flex flex-col gap-3 text-sm">
      {/* Title line: "#1536963 · E30" */}
      <div className="flex flex-col gap-1.5">
        <h3 className="text-[15px] font-bold text-[#edf0f5]">
          <span className="text-[#5ba8ff]">#{detail.wi_id}</span>
          {detail.title ? ` · ${detail.title}` : ""}
        </h3>
        {/* Metadata line */}
        <div className="text-xs text-[#bfc4cc]">{metaParts.join("  ·  ")}</div>
        {/* Area / Iteration / Tags */}
        <div className="flex flex-col gap-0.5 text-xs text-[#8a8f99]">
          <span>Area: {pathTail(detail.area_path) || "—"}</span>
          <span>Iteration: {pathTail(detail.iteration_path) || "—"}</span>
          <span>
            Tags: {detail.tags && detail.tags.length ? detail.tags.join(", ") : "—"}
          </span>
        </div>
      </div>

      <div className="border-t border-[#2d313c]" />

      {detail.description_html && (
        <Section title="Description" html={detail.description_html} />
      )}
      {detail.acceptance_html && (
        <Section title="Acceptance Criteria" html={detail.acceptance_html} />
      )}

      {detail.comments_html?.length > 0 && (
        <div className="flex flex-col gap-2">
          <h4 className="text-sm font-bold text-[#edf0f5]">
            {detail.comments_html.length} Comment
            {detail.comments_html.length === 1 ? "" : "s"}
          </h4>
          {detail.comments_html.map(([who, when, html], i) => (
            <div
              key={i}
              className="flex flex-col gap-1.5 rounded-[10px] border border-[#2d313c] bg-[#1a1d25] p-3"
            >
              <div className="flex items-center gap-2">
                <span
                  className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-[#2d313c] text-[10px] font-bold text-[#bfc4cc]"
                  aria-hidden="true"
                >
                  {initials(who)}
                </span>
                <span className="text-sm font-semibold text-[#7abaff]">
                  {who}
                </span>
                <span className="text-xs text-[#7a7f8a]">
                  commented {fmtComment(when)}
                </span>
              </div>
              <div
                className="tt-html text-sm leading-relaxed text-[#d6dae2] [&_a]:text-[#5ba8ff] [&_img]:my-2 [&_img]:max-w-full [&_img]:rounded-md [&_img]:border [&_img]:border-[#2d313c]"
                dangerouslySetInnerHTML={{ __html: html }}
              />
            </div>
          ))}
        </div>
      )}

      {detail.attachments?.length > 0 && (
        <div className="flex flex-col gap-2">
          <h4 className="text-sm font-bold text-[#edf0f5]">
            Attachments ({detail.attachments.length})
          </h4>
          <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
            {detail.attachments.map((a, i) => (
              <AttachmentCard key={`${a.name}-${i}`} attachment={a} />
            ))}
          </div>
        </div>
      )}

      {detail.hyperlinks?.length > 0 && (
        <div className="flex flex-col gap-2">
          <h4 className="text-sm font-bold text-[#edf0f5]">
            Links ({detail.hyperlinks.length})
          </h4>
          <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
            {detail.hyperlinks.map(([label, url], i) => (
              <a
                key={`${label}-${i}`}
                href={url}
                target="_blank"
                rel="noopener noreferrer"
                className="flex items-center gap-2 rounded-[10px] border border-[#2d313c] bg-[#1a1d25] p-2.5 text-sm text-[#5ba8ff] transition-colors hover:border-[#3a4150] hover:bg-[#20242d]"
                title={url}
              >
                <LinkIcon className="h-4 w-4 shrink-0" />
                <span className="min-w-0 flex-1 truncate">{label || url}</span>
                <ExternalLink className="h-3.5 w-3.5 shrink-0 text-[#7a7f8a]" />
              </a>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

/** Initials for a comment avatar, e.g. "Amy Domenick (US)" → "AD". */
function initials(name: string): string {
  const cleaned = name.replace(/\([^)]*\)/g, "").trim();
  const parts = cleaned.split(/\s+/).filter(Boolean);
  if (parts.length === 0) return "?";
  if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase();
  return (parts[0][0] + parts[parts.length - 1][0]).toUpperCase();
}

/** ADO comment dates are ISO strings; render a short, friendly date. */
function fmtComment(when: string): string {
  if (!when) return "";
  const d = new Date(when);
  if (Number.isNaN(d.getTime())) return when;
  return d.toLocaleDateString(undefined, {
    month: "short",
    day: "numeric",
    year: "numeric",
  });
}

const IMAGE_EXT_RE = /\.(png|jpe?g|gif|bmp|webp|svg|tiff?)$/i;

/** Format a byte count as a short human string. */
function fmtSize(bytes: number): string {
  if (!bytes || bytes < 0) return "";
  const units = ["B", "KB", "MB", "GB"];
  let n = bytes;
  let u = 0;
  while (n >= 1024 && u < units.length - 1) {
    n /= 1024;
    u++;
  }
  return `${n >= 10 || u === 0 ? Math.round(n) : n.toFixed(1)} ${units[u]}`;
}

function AttachmentCard({ attachment }: { attachment: Attachment }) {
  const isImage = IMAGE_EXT_RE.test(attachment.name);
  return (
    <a
      href={attachment.downloadUrl}
      target="_blank"
      rel="noopener noreferrer"
      download={attachment.name}
      className="group flex flex-col overflow-hidden rounded-[10px] border border-[#2d313c] bg-[#1a1d25] transition-colors hover:border-[#3a4150] hover:bg-[#20242d]"
      title={`Download ${attachment.name}`}
    >
      <div className="flex h-24 items-center justify-center bg-[#0f1218]">
        {isImage ? (
          // eslint-disable-next-line @next/next/no-img-element
          <img
            src={attachment.downloadUrl || "/placeholder.svg"}
            alt={attachment.name}
            className="h-full w-full object-cover"
          />
        ) : (
          <FileText className="h-8 w-8 text-[#7a7f8a]" />
        )}
      </div>
      <div className="flex items-center gap-1.5 p-2">
        {isImage ? (
          <ImageIcon className="h-3.5 w-3.5 shrink-0 text-[#7a7f8a]" />
        ) : (
          <Download className="h-3.5 w-3.5 shrink-0 text-[#7a7f8a]" />
        )}
        <span className="min-w-0 flex-1 truncate text-xs text-[#cfd4dc]">
          {attachment.name}
        </span>
        {attachment.size > 0 && (
          <span className="shrink-0 text-[10px] text-[#7a7f8a]">
            {fmtSize(attachment.size)}
          </span>
        )}
      </div>
    </a>
  );
}

function Section({ title, html }: { title: string; html: string }) {
  return (
    <div className="flex flex-col gap-1.5">
      <h4 className="text-sm font-bold text-[#edf0f5]">{title}</h4>
      <div
        className="tt-html text-sm leading-relaxed text-[#bfc4cc] [&_a]:text-[#5ba8ff] [&_img]:my-2 [&_img]:max-w-full [&_img]:rounded-md [&_img]:border [&_img]:border-[#2d313c]"
        dangerouslySetInnerHTML={{ __html: html }}
      />
    </div>
  );
}

function fmtTime(modified: number): string {
  // backend sends epoch seconds
  const d = new Date(modified * 1000);
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(
    d.getHours()
  )}:${pad(d.getMinutes())}`;
}

const GENERAL_GROUP = "General";
// The agent writes reviewer workbooks as:
//   review_<phase>_[<board>_]<YYYYMMDD_HHMMSS>.xlsx
// where <phase> is implementation|sit|uat|general and <board> is an optional
// hyphen-joined board token (no underscores — underscore is the delimiter).
const ARTIFACT_NAME_RE =
  /^review_([a-z]+)_(?:(.+)_)?(\d{8}_\d{6})$/i;
// Legacy desktop name kept for back-compat with older artifacts:
//   testcases_<review|template>_[<phase>_][<board>_]<YYYYMMDD_HHMMSS>.xlsx
const LEGACY_NAME_RE =
  /^testcases_(review|template)_(?:(.*)_)?(\d{8}_\d{6})$/;

interface ParsedArtifact {
  file: ArtifactFile;
  genKind: string; // "review" | "template" | "" (non-testcases)
  tcType: TcType | ""; // raw phase: implementation | sit | uat | "" (none)
  phaseLabel: string; // "Implementation" | "SIT" | "UAT" | "Legacy" | "PDF"
  board: string; // "" → General
  group: string;
  when: string; // "YYYY-MM-DD HH:MM"
  stamp: string; // sortable
}

function parseStamp(stamp: string): string {
  // 20260628_134501 → "2026-06-28 13:45"
  const m = /^(\d{4})(\d{2})(\d{2})_(\d{2})(\d{2})(\d{2})$/.exec(stamp);
  if (!m) return stamp;
  return `${m[1]}-${m[2]}-${m[3]} ${m[4]}:${m[5]}`;
}

// Restore a display board name from its filename token (hyphens → spaces).
function boardFromToken(token: string): string {
  return token.replace(/-+/g, " ").trim();
}

function parseArtifact(file: ArtifactFile): ParsedArtifact {
  const stem = file.name.replace(/\.[^.]+$/, "");

  // Current agent format: review_<phase>_[<board>_]<stamp>.xlsx
  const m = ARTIFACT_NAME_RE.exec(stem);
  if (m) {
    const phaseRaw = m[1].toLowerCase();
    const boardTok = m[2] ?? "";
    const stamp = m[3];
    const isTc = (TC_TYPES as readonly string[]).includes(phaseRaw);
    const board = boardFromToken(boardTok);
    return {
      file,
      // Any review_ workbook is a reviewable test-case artifact (enables the
      // regenerate/upload menu items), even the "general" phase.
      genKind: "review",
      tcType: (isTc ? phaseRaw : "") as TcType | "",
      phaseLabel: isTc ? TC_DISPLAY_NAME[phaseRaw as TcType] : "General",
      board,
      group: board || GENERAL_GROUP,
      when: parseStamp(stamp),
      stamp,
    };
  }

  // Legacy desktop format: testcases_<review|template>_[<phase>_][<board>_]<stamp>
  const lm = LEGACY_NAME_RE.exec(stem);
  if (lm) {
    const genKind = lm[1];
    const middle = lm[2] ?? "";
    const stamp = lm[3];
    let phase = "";
    let board = middle;
    for (const t of TC_TYPES) {
      if (middle === t) {
        phase = t;
        board = "";
        break;
      }
      if (middle.startsWith(t + "_")) {
        phase = t;
        board = middle.slice(t.length + 1);
        break;
      }
    }
    return {
      file,
      genKind,
      tcType: (phase as TcType) || "",
      phaseLabel: phase ? TC_DISPLAY_NAME[phase as TcType] : "General",
      board: boardFromToken(board),
      group: boardFromToken(board) || GENERAL_GROUP,
      when: parseStamp(stamp),
      stamp,
    };
  }

  // Unrecognized output (e.g. a packaged PDF): show as PDF/Legacy, no actions.
  const isPdf = /\.pdf$/i.test(file.name);
  return {
    file,
    genKind: "",
    tcType: "",
    phaseLabel: isPdf ? "PDF" : "Legacy",
    board: "",
    group: GENERAL_GROUP,
    when: fmtTime(file.modified),
    stamp: String(file.modified),
  };
}

/** Generated artifacts pane — desktop layout (O01-O04). */
function OutputsContent({
  project,
  projectLabel,
  pushLog,
}: {
  project: string;
  projectLabel: string;
  pushLog: (level: "INFO" | "SUCCESS" | "WARN" | "ERROR", t: string) => void;
}) {
  const { openDialog, setGenerateCtx } = useAppState();
  const [files, setFiles] = useState<ArtifactFile[]>([]);
  const [loading, setLoading] = useState(false);
  const [selected, setSelected] = useState<string>("");
  const [menu, setMenu] = useState<
    { x: number; y: number; art: ParsedArtifact } | null
  >(null);

  const refresh = () => {
    if (!project) return;
    setLoading(true);
    agent
      .listArtifacts(project)
      .then((fs) => {
        setFiles(fs);
        setSelected((cur) => (fs.some((f) => f.path === cur) ? cur : ""));
      })
      .catch(() => setFiles([]))
      .finally(() => setLoading(false));
  };

  useEffect(refresh, [project]);

  // Parse + sort by stamp descending, exactly like the desktop browser.
  const parsed = useMemo(
    () =>
      files
        .map(parseArtifact)
        .sort((a, b) => (a.stamp < b.stamp ? 1 : a.stamp > b.stamp ? -1 : 0)),
    [files]
  );
  const boardCount = useMemo(
    () => new Set(parsed.map((p) => p.group)).size,
    [parsed]
  );

  const openPath = (path: string) => {
    if (!path) return;
    window.open(agent.artifactDownloadUrl(path), "_blank", "noopener");
  };

  const deletePath = async (path: string) => {
    if (!path) return;
    try {
      await agent.deleteArtifact(path);
      pushLog("SUCCESS", "Deleted artifact.");
      setSelected((cur) => (cur === path ? "" : cur));
      refresh();
    } catch (e) {
      pushLog("ERROR", `Delete failed: ${(e as Error).message}`);
    }
  };

  // Right-click actions (desktop parity): load an artifact back into the
  // Generate dialog for regeneration with feedback, or push it to ADO.
  const loadRegen = (art: ParsedArtifact) => {
    setGenerateCtx({ tcType: art.tcType, loadArtifactPath: art.file.path });
    openDialog("generate");
  };
  const uploadToAdo = (art: ParsedArtifact) => {
    setGenerateCtx({ tcType: art.tcType, xlsxPath: art.file.path });
    openDialog("upload");
  };

  if (!project)
    return (
      <p style={{ color: COLOR_MUTED }} className="text-sm">
        Select a project to view generated artifacts.
      </p>
    );

  return (
    <div className="flex h-full flex-col gap-2" onClick={() => setMenu(null)}>
      <h3 className="text-sm font-bold text-[#edf0f5]">
        Generated artifacts{projectLabel ? ` - ${projectLabel}` : ""}
      </h3>
      <p className="text-xs text-[#8a8f99]">
        {parsed.length} file(s) across {boardCount} board(s).
      </p>

      <div className="min-h-0 flex-1 overflow-auto rounded-[8px] border border-[#2d313c] bg-[#0d1017]">
        {parsed.length === 0 ? (
          <p style={{ color: COLOR_MUTED }} className="p-3 text-sm">
            No generated files yet. Generate test cases and they will appear
            here.
          </p>
        ) : (
          parsed.map((p) => {
            const f = p.file;
            const isSel = f.path === selected;
            return (
              <button
                key={f.path}
                onClick={() => setSelected(f.path)}
                onDoubleClick={() => openPath(f.path)}
                onContextMenu={(e) => {
                  e.preventDefault();
                  setSelected(f.path);
                  setMenu({ x: e.clientX, y: e.clientY, art: p });
                }}
                className="block w-full truncate px-3 py-1.5 text-left text-sm"
                style={{
                  background: isSel ? "#16466e" : "transparent",
                  color: isSel ? "#ffffff" : "#cfd4dc",
                }}
                title={f.name}
              >
                <span className="text-[#8a8f99]">▸ </span>
                {`${p.board || GENERAL_GROUP} - ${p.when}${
                  p.phaseLabel && p.phaseLabel !== "General"
                    ? `  (${p.phaseLabel})`
                    : ""
                }`}
              </button>
            );
          })
        )}
      </div>

      <div className="flex items-center gap-2">
        <button
          className="tt-btn-primary !px-4 !py-1.5 text-sm"
          disabled={!selected}
          onClick={() => openPath(selected)}
        >
          Open
        </button>
        <button
          className="tt-btn-danger !px-4 !py-1.5 text-sm"
          disabled={!selected}
          onClick={() => deletePath(selected)}
        >
          Delete
        </button>
        <button
          className="tt-btn-ghost !px-4 !py-1.5 text-sm"
          onClick={refresh}
        >
          <RefreshCw className={`h-3.5 w-3.5 ${loading ? "animate-spin" : ""}`} />{" "}
          Refresh
        </button>
      </div>

      {menu && (
        <div
          className="fixed z-50 min-w-[210px] overflow-hidden rounded-md border border-[#2d313c] bg-[#1b1e26] py-1 shadow-xl"
          style={{ left: menu.x, top: menu.y }}
          onClick={(e) => e.stopPropagation()}
        >
          <ContextItem
            label="Open"
            onClick={() => {
              openPath(menu.art.file.path);
              setMenu(null);
            }}
          />
          {/* Regeneration + ADO upload only apply to reviewer test-case
              workbooks, not packaged PDFs / legacy outputs. */}
          {menu.art.genKind && (
            <>
              <ContextItem
                label="Load and Regenerate with feedback"
                onClick={() => {
                  loadRegen(menu.art);
                  setMenu(null);
                }}
              />
              <ContextItem
                label="Upload to ADO"
                onClick={() => {
                  uploadToAdo(menu.art);
                  setMenu(null);
                }}
              />
            </>
          )}
          <div className="my-1 border-t border-[#2d313c]" />
          <ContextItem
            label="Delete"
            danger
            onClick={() => {
              deletePath(menu.art.file.path);
              setMenu(null);
            }}
          />
        </div>
      )}
    </div>
  );
}

function ContextItem({
  label,
  onClick,
  danger,
}: {
  label: string;
  onClick: () => void;
  danger?: boolean;
}) {
  return (
    <button
      onClick={onClick}
      className="block w-full px-3 py-1.5 text-left text-sm transition-colors hover:bg-[#262a33]"
      style={{ color: danger ? "#f87171" : "#dfe3ea" }}
    >
      {label}
    </button>
  );
}
