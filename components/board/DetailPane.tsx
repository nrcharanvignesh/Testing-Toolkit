"use client";

import { useEffect, useMemo, useState } from "react";
import {
  RefreshCw,
  FileText,
  Download,
  ExternalLink,
  ImageIcon,
  Link as LinkIcon,
  Tag,
  User,
  Folder,
  GitBranch,
  Layers,
  Cpu,
  FileSpreadsheet,
  FileCheck2,
  FileCog,
  ExternalLink as OpenIcon,
} from "lucide-react";
import {
  agent,
  TC_TYPES,
  TC_DISPLAY_NAME,
  type TcType,
  type WorkItemDetail,
  type ArtifactFile,
  type Attachment,
  type WiId,
} from "@/lib/agent-client";
import { useAppState } from "@/lib/app-state";
import { COLOR_MUTED } from "@/lib/board-utils";

interface DetailPaneProps {
  activeWiId: WiId | null;
}

export function DetailPane({ activeWiId }: DetailPaneProps) {
  const { currentProject, settings, displayName, pushLog } = useAppState();
  const [mode, setMode] = useState<"detail" | "outputs">("detail");
  const [detail, setDetail] = useState<WorkItemDetail | null>(null);
  const [loading, setLoading] = useState(false);
  const [tagDraft, setTagDraft] = useState("");
  const [tagging, setTagging] = useState(false);

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

  // Open the work item in its source system: a JIRA issue (string key) opens
  // the JIRA browse URL; an ADO work item (numeric id) opens the ADO editor.
  const openInSource = () => {
    if (!detail) return;
    const isJiraKey = typeof detail.wi_id === "string";
    if (isJiraKey) {
      const base = (settings?.jira_url ?? "").replace(/\/+$/, "");
      if (!base) return;
      window.open(
        `${base}/browse/${encodeURIComponent(String(detail.wi_id))}`,
        "_blank",
        "noopener"
      );
      return;
    }
    if (!settings?.organization) return;
    const url = `https://dev.azure.com/${encodeURIComponent(
      settings.organization
    )}/_workitems/edit/${detail.wi_id}`;
    window.open(url, "_blank", "noopener");
  };

  // Tagging is ADO-only (numeric work-item id). JIRA issues use string keys and
  // are not taggable through this facade.
  const canTag = detail != null && typeof detail.wi_id === "number";

  const addTag = async () => {
    const tag = tagDraft.trim();
    if (!detail || !currentProject || !tag || tagging) return;
    setTagging(true);
    try {
      await agent.tagWorkItem(currentProject, detail.wi_id, tag);
      // Optimistic, case-insensitive local update so the UI reflects the add
      // without a full refetch.
      setDetail((prev) => {
        if (!prev) return prev;
        const exists = (prev.tags ?? []).some(
          (t) => t.toLowerCase() === tag.toLowerCase()
        );
        return exists ? prev : { ...prev, tags: [...(prev.tags ?? []), tag] };
      });
      setTagDraft("");
      pushLog("SUCCESS", `Tagged #${detail.wi_id} with "${tag}"`);
    } catch (e) {
      pushLog("ERROR", `Tag failed: ${(e as Error).message}`);
    } finally {
      setTagging(false);
    }
  };

  // Tabs: selected tab is gray/inset (not bright blue).
  const tabStyle = (active: boolean): React.CSSProperties =>
    active
      ? { background: "var(--tt-surface-high)", color: "var(--tt-text-primary)", borderColor: "var(--tt-outline)" }
      : { background: "transparent", color: "var(--tt-text-muted)" };

  return (
    <div className="tt-card flex h-full flex-col gap-2 p-2.5">
      <div className="flex items-center gap-1">
        <button
          className="rounded-md border px-3 py-1 text-xs font-semibold transition-colors"
          style={tabStyle(mode === "detail")}
          onClick={() => setMode("detail")}
        >
          Detail
        </button>
        <button
          className="rounded-md border px-3 py-1 text-xs font-semibold transition-colors"
          style={tabStyle(mode === "outputs")}
          onClick={() => setMode("outputs")}
        >
          Outputs
        </button>
        <div className="flex-1" />
        <button
          className="tt-btn-ghost !px-2.5 !py-1 !text-xs !gap-1.5"
          disabled={!detail}
          onClick={openInSource}
          title={detail && typeof detail.wi_id === "string" ? "Open in JIRA" : "Open in ADO"}
        >
          <OpenIcon className="h-3 w-3" />
          {detail && typeof detail.wi_id === "string" ? "JIRA" : "ADO"}
        </button>
      </div>

      <div className="min-h-0 flex-1 overflow-auto rounded-[10px] border border-[var(--tt-outline)] bg-[var(--tt-surface-base)] p-4">
        {mode === "detail" ? (
          <DetailContent
            loading={loading}
            detail={detail}
            hasItem={activeWiId != null}
            canTag={canTag}
            tagDraft={tagDraft}
            setTagDraft={setTagDraft}
            tagging={tagging}
            addTag={addTag}
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
  // ADO paths use backslash; normalise both separators to backslash then split.
  const normalised = p.replace(/\//g, "\\");
  const parts = normalised.split("\\").map((s) => s.trim()).filter(Boolean);
  return parts[parts.length - 1] ?? p;
}

function DetailContent({
  loading,
  detail,
  hasItem,
  canTag,
  tagDraft,
  setTagDraft,
  tagging,
  addTag,
}: {
  loading: boolean;
  detail: WorkItemDetail | null;
  hasItem: boolean;
  canTag: boolean;
  tagDraft: string;
  setTagDraft: (v: string) => void;
  tagging: boolean;
  addTag: () => void;
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

  // Derive type badge class for the header
  const typeBadgeClass = wiTypeBadgeClassDetail(detail.wi_type);

  return (
    <div className="flex flex-col gap-3 text-sm">
      {/* Title */}
      <div className="flex flex-col gap-1">
        <h3 className="text-base font-bold leading-snug text-[var(--tt-text-primary)] text-pretty">
          <span className="font-mono text-sm text-[var(--tt-primary)]">#{detail.wi_id}</span>
          {detail.title ? ` ${detail.title}` : ""}
        </h3>
        {/* Type + State badges inline */}
        <div className="flex flex-wrap items-center gap-1.5">
          {detail.wi_type && (
            <span className={`tt-badge ${typeBadgeClass}`}>{detail.wi_type}</span>
          )}
          {detail.state && (
            <span className={`tt-badge ${wiStateBadgeClassDetail(detail.state)}`}>
              {detail.state}
            </span>
          )}
          {detail.board_column && (
            <span className="tt-badge tt-badge-neutral">{detail.board_column}</span>
          )}
        </div>
      </div>

      {/* Property grid */}
      <div className="grid grid-cols-2 gap-x-3 gap-y-1.5 rounded-[8px] border border-[var(--tt-outline-soft)] bg-[var(--tt-surface-container)] px-3 py-2 text-xs">
        <PropRow icon={<User className="h-3 w-3" />} label="Assigned" value={detail.assigned_to || "—"} />
        <PropRow icon={<Folder className="h-3 w-3" />} label="Area" value={pathTail(detail.area_path) || "—"} />
        <PropRow icon={<GitBranch className="h-3 w-3" />} label="Iteration" value={pathTail(detail.iteration_path) || "—"} />
        <PropRow icon={<Cpu className="h-3 w-3" />} label="Column" value={detail.board_column || "—"} />
      </div>

      {/* Tags row */}
      <div className="flex flex-wrap items-center gap-1.5">
        <span className="flex items-center gap-1 text-xs text-[var(--tt-text-muted)]">
          <Tag className="h-3 w-3" /> Tags:
        </span>
        {detail.tags && detail.tags.length > 0 ? (
          detail.tags.map((tag) => (
            <span
              key={tag}
              className="tt-badge"
              style={{
                background: "rgba(91,168,255,0.10)",
                color: "var(--tt-primary)",
              }}
            >
              {tag}
            </span>
          ))
        ) : (
          <span className="text-xs text-[var(--tt-text-faint)]">None</span>
        )}
        {canTag && (
          <div className="flex items-center gap-1.5">
            <input
              type="text"
              value={tagDraft}
              onChange={(e) => setTagDraft(e.target.value)}
              onKeyDown={(e) => {
                if (
                  e.key === "Enter" &&
                  !e.nativeEvent.isComposing &&
                  e.keyCode !== 229
                ) {
                  e.preventDefault();
                  void addTag();
                }
              }}
              placeholder="Add tag…"
              aria-label="Add a tag to this work item"
              disabled={tagging}
              className="w-24 rounded-md border border-[var(--tt-outline)] bg-[var(--tt-surface-high)] px-2 py-0.5 text-xs text-[var(--tt-text-primary)] outline-none focus:border-[var(--tt-primary)]"
            />
            <button
              type="button"
              onClick={() => void addTag()}
              disabled={tagging || !tagDraft.trim()}
              className="tt-btn !px-2 !py-0.5 !text-[10px] disabled:opacity-50"
            >
              {tagging ? "Adding…" : "Add"}
            </button>
          </div>
        )}
      </div>

      <div className="border-t border-[var(--tt-outline)]" />

      {detail.description_html && (
        <Section title="Description" html={detail.description_html} />
      )}
      {detail.acceptance_html && (
        <Section title="Acceptance Criteria" html={detail.acceptance_html} />
      )}

      {detail.comments_html?.length > 0 && (
        <div className="flex flex-col gap-2">
          <h4 className="text-sm font-bold text-[var(--tt-text-primary)]">
            {detail.comments_html.length} Comment
            {detail.comments_html.length === 1 ? "" : "s"}
          </h4>
          {detail.comments_html.map(([who, when, html], i) => (
            <div
              key={i}
              className="flex flex-col gap-1.5 rounded-[10px] border border-[var(--tt-outline)] bg-[var(--tt-surface-container)] p-3"
            >
              <div className="flex items-center gap-2">
                <span
                  className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full text-[10px] font-bold text-white"
                  style={{ background: `hsl(${nameHue(who)} 55% 40%)` }}
                  aria-hidden="true"
                >
                  {initials(who)}
                </span>
                <span className="text-sm font-semibold text-[var(--tt-primary-soft)]">
                  {who}
                </span>
                <span className="text-xs text-[var(--tt-text-dim)]">
                  commented {fmtComment(when)}
                </span>
              </div>
              <div
                className="tt-html text-sm leading-relaxed text-[var(--tt-text-bright)] [&_a]:text-[var(--tt-primary)] [&_img]:my-2 [&_img]:max-w-full [&_img]:rounded-md [&_img]:border [&_img]:border-[var(--tt-outline)]"
                dangerouslySetInnerHTML={{ __html: html }}
              />
            </div>
          ))}
        </div>
      )}

      {detail.attachments?.length > 0 && (
        <div className="flex flex-col gap-2">
          <h4 className="text-sm font-bold text-[var(--tt-text-primary)]">
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
          <h4 className="text-sm font-bold text-[var(--tt-text-primary)]">
            Links ({detail.hyperlinks.length})
          </h4>
          <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
            {detail.hyperlinks.map(([label, url], i) => (
              <a
                key={`${label}-${i}`}
                href={url}
                target="_blank"
                rel="noopener noreferrer"
                className="flex items-center gap-2 rounded-[10px] border border-[var(--tt-outline)] bg-[var(--tt-surface-container)] p-2.5 text-sm text-[var(--tt-primary)] transition-colors hover:border-[var(--tt-border-strong)] hover:bg-[var(--tt-surface-high)]"
                title={url}
              >
                <LinkIcon className="h-4 w-4 shrink-0" />
                <span className="min-w-0 flex-1 truncate">{label || url}</span>
                <ExternalLink className="h-3.5 w-3.5 shrink-0 text-[var(--tt-text-dim)]" />
              </a>
            ))}
          </div>
        </div>
      )}

      {/* Related work items (desktop board_grid._render_detail 'Links' → related) */}
      {detail.related?.length > 0 && (
        <div className="flex flex-col gap-2">
          <h4 className="text-sm font-bold text-[var(--tt-text-primary)]">
            Related work items ({detail.related.length})
          </h4>
          <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
            {detail.related.map(([name, wid, url], i) => (
              <a
                key={`${wid}-${i}`}
                href={url}
                target="_blank"
                rel="noopener noreferrer"
                className="flex items-center gap-2 rounded-[10px] border border-[var(--tt-outline)] bg-[var(--tt-surface-container)] p-2.5 text-sm text-[var(--tt-primary)] transition-colors hover:border-[var(--tt-border-strong)] hover:bg-[var(--tt-surface-high)]"
                title={`Open ${name}${wid ? ` #${wid}` : ""}`}
              >
                <GitBranch className="h-4 w-4 shrink-0" />
                <span className="min-w-0 flex-1 truncate text-[var(--tt-text-primary)]">
                  {name}
                </span>
                {wid ? (
                  <span className="shrink-0 font-mono text-xs text-[var(--tt-text-muted)]">
                    #{wid}
                  </span>
                ) : null}
                <ExternalLink className="h-3.5 w-3.5 shrink-0 text-[var(--tt-text-dim)]" />
              </a>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Badge helpers for DetailPane (mirrors BoardGrid helpers)
// ---------------------------------------------------------------------------
function wiTypeBadgeClassDetail(t: string): string {
  const k = (t || "").toLowerCase();
  if (k.includes("story") || k.includes("user story")) return "tt-badge-story";
  if (k.includes("bug") || k.includes("issue")) return "tt-badge-bug";
  if (k.includes("task")) return "tt-badge-task";
  if (k.includes("epic")) return "tt-badge-epic";
  if (k.includes("feature")) return "tt-badge-feature";
  return "tt-badge-neutral";
}

function wiStateBadgeClassDetail(s: string): string {
  const k = (s || "").toLowerCase();
  if (k === "active" || k === "in progress" || k === "in review") return "tt-badge-success";
  if (k === "resolved" || k === "done" || k === "closed") return "tt-badge-info";
  if (k === "new" || k === "proposed" || k === "to do") return "tt-badge-warn";
  if (k === "removed") return "tt-badge-danger";
  return "tt-badge-neutral";
}

function nameHue(s: string): number {
  let h = 0;
  for (let i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) & 0xffff;
  return h % 360;
}

function PropRow({
  icon,
  label,
  value,
}: {
  icon: import("react").ReactNode;
  label: string;
  value: string;
}) {
  return (
    <>
      <dt className="flex items-center gap-1 font-medium text-[var(--tt-text-muted)]">
        {icon}
        {label}
      </dt>
      <dd className="truncate text-[var(--tt-text-primary)]" title={value}>
        {value}
      </dd>
    </>
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
      className="group flex flex-col overflow-hidden rounded-[10px] border border-[var(--tt-outline)] bg-[var(--tt-surface-container)] transition-colors hover:border-[var(--tt-border-strong)] hover:bg-[var(--tt-surface-high)]"
      title={`Download ${attachment.name}`}
    >
      <div className="flex h-24 items-center justify-center bg-[var(--tt-surface-deepest)]">
        {isImage ? (
          // eslint-disable-next-line @next/next/no-img-element
          <img
            src={attachment.downloadUrl || "/placeholder.svg"}
            alt={attachment.name}
            className="h-full w-full object-cover"
          />
        ) : (
          <FileText className="h-8 w-8 text-[var(--tt-text-dim)]" />
        )}
      </div>
      <div className="flex items-center gap-1.5 p-2">
        {isImage ? (
          <ImageIcon className="h-3.5 w-3.5 shrink-0 text-[var(--tt-text-dim)]" />
        ) : (
          <Download className="h-3.5 w-3.5 shrink-0 text-[var(--tt-text-dim)]" />
        )}
        <span className="min-w-0 flex-1 truncate text-xs text-[var(--tt-text-secondary)]">
          {attachment.name}
        </span>
        {attachment.size > 0 && (
          <span className="shrink-0 text-[10px] text-[var(--tt-text-dim)]">
            {fmtSize(attachment.size)}
          </span>
        )}
      </div>
      {attachment.comment ? (
        <p
          className="truncate px-2 pb-2 text-[10px] text-[var(--tt-text-muted)]"
          title={attachment.comment}
        >
          {attachment.comment}
        </p>
      ) : null}
    </a>
  );
}

function Section({ title, html }: { title: string; html: string }) {
  return (
    <div className="flex flex-col gap-1.5">
      <h4 className="text-sm font-bold text-[var(--tt-text-primary)]">{title}</h4>
      <div
        className="tt-html text-sm leading-relaxed text-[var(--tt-text-secondary)] [&_a]:text-[var(--tt-primary)] [&_img]:my-2 [&_img]:max-w-full [&_img]:rounded-md [&_img]:border [&_img]:border-[var(--tt-outline)]"
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
  // Legacy name kept for back-compat with older artifacts:
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

  // Legacy format: testcases_<review|template>_[<phase>_][<board>_]<stamp>
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

/** Generated artifacts pane. */
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

  // Parse + sort by stamp descending.
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

  // Right-click actions: load an artifact back into the Generate dialog for
  // regeneration with feedback, or push it to ADO.
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
      <h3 className="text-sm font-bold text-[var(--tt-text-primary)]">
        Generated artifacts{projectLabel ? ` - ${projectLabel}` : ""}
      </h3>
      <p className="text-xs text-[var(--tt-text-muted)]">
        {parsed.length} file(s) across {boardCount} board(s).
      </p>

      <div className="min-h-0 flex-1 overflow-auto rounded-[8px] border border-[var(--tt-outline)] bg-[var(--tt-surface-deepest)]">
        {parsed.length === 0 ? (
          <p style={{ color: COLOR_MUTED }} className="p-3 text-sm">
            No generated files yet. Generate test cases and they will appear
            here.
          </p>
        ) : (
          parsed.map((p) => {
            const f = p.file;
            const isSel = f.path === selected;
            const isPdf = /\.pdf$/i.test(f.name);
            const ArtIcon = isPdf ? FileText : p.tcType === "implementation"
              ? FileCog
              : p.tcType === "sit"
                ? FileCheck2
                : FileSpreadsheet;
            const phaseBadgeClass =
              p.tcType === "implementation" ? "tt-badge-info"
              : p.tcType === "sit"            ? "tt-badge-epic"
              : p.tcType === "uat"            ? "tt-badge-feature"
              : isPdf                         ? "tt-badge-neutral"
              :                                 "tt-badge-neutral";
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
                className="flex w-full items-center gap-2 px-3 py-1.5 text-left text-sm transition-colors"
                style={{
                  background: isSel ? "var(--tt-row-sel)" : "transparent",
                  color: isSel ? "#ffffff" : "var(--tt-text-secondary)",
                }}
                title={f.name}
              >
                {/* File type icon */}
                <ArtIcon
                  className="h-3.5 w-3.5 shrink-0"
                  style={{ color: isSel ? "rgba(255,255,255,0.7)" : "var(--tt-primary)" }}
                />
                {/* Board + group label */}
                <span className="min-w-0 flex-1 truncate">
                  {p.board || GENERAL_GROUP}
                </span>
                {/* Phase badge */}
                {p.phaseLabel && p.phaseLabel !== "General" && (
                  <span
                    className={`tt-badge ${phaseBadgeClass} shrink-0`}
                    style={isSel ? { background: "rgba(255,255,255,0.18)", color: "white" } : undefined}
                  >
                    {p.phaseLabel}
                  </span>
                )}
                {/* Timestamp chip */}
                <span
                  className="shrink-0 font-mono tabular-nums"
                  style={{
                    fontSize: "9px",
                    color: isSel ? "rgba(255,255,255,0.55)" : "var(--tt-text-faint)",
                  }}
                >
                  {p.when}
                </span>
              </button>
            );
          })
        )}
      </div>

      <div className="flex items-center gap-2">
        {selected ? (
          <a
            className="tt-btn-primary !px-4 !py-1.5 text-sm"
            href={agent.artifactDownloadUrl(selected)}
            download
            target="_blank"
            rel="noopener noreferrer"
          >
            Download
          </a>
        ) : (
          <button className="tt-btn-primary !px-4 !py-1.5 text-sm" disabled>
            Download
          </button>
        )}
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
          className="fixed z-50 min-w-[210px] overflow-hidden rounded-md border border-[var(--tt-outline)] bg-[var(--tt-surface-base)] py-1 shadow-xl"
          style={{ left: menu.x, top: menu.y }}
          onClick={(e) => e.stopPropagation()}
        >
          <ContextItem
            label="Download"
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
          <div className="my-1 border-t border-[var(--tt-outline)]" />
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
      className="block w-full px-3 py-1.5 text-left text-sm transition-colors hover:bg-[var(--tt-surface-high)]"
      style={{ color: danger ? "var(--tt-danger-hover)" : "var(--tt-text-bright)" }}
    >
      {label}
    </button>
  );
}
