"use client";

import { useEffect, useState } from "react";
import { RefreshCw, FileText } from "lucide-react";
import {
  agent,
  type WorkItemDetail,
  type ArtifactFile,
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
          <h4 className="text-sm font-bold text-[#edf0f5]">Comments</h4>
          {detail.comments_html.map(([who, when, html], i) => (
            <div key={i} className="flex flex-col gap-0.5">
              <div className="text-xs text-[#7abaff]">
                {who}{" "}
                <span className="text-[#7a7f8a]">{when}</span>
              </div>
              <div
                className="tt-html text-sm text-[#bfc4cc]"
                dangerouslySetInnerHTML={{ __html: html }}
              />
            </div>
          ))}
        </div>
      )}

      {detail.attachments?.length > 0 && (
        <div className="flex flex-col gap-1.5">
          <h4 className="text-sm font-bold text-[#edf0f5]">Attachments</h4>
          {detail.attachments.map((a, i) => (
            <a
              key={`${a.name}-${i}`}
              href={a.url}
              target="_blank"
              rel="noopener noreferrer"
              className="flex items-center gap-2 text-sm text-[#5ba8ff] hover:underline"
            >
              <FileText className="h-3.5 w-3.5" /> {a.name}
            </a>
          ))}
        </div>
      )}

      {detail.hyperlinks?.length > 0 && (
        <div className="flex flex-col gap-1.5">
          <h4 className="text-sm font-bold text-[#edf0f5]">Links</h4>
          {detail.hyperlinks.map(([label, url], i) => (
            <a
              key={`${label}-${i}`}
              href={url}
              target="_blank"
              rel="noopener noreferrer"
              className="text-sm text-[#5ba8ff] hover:underline"
            >
              {label || url}
            </a>
          ))}
        </div>
      )}
    </div>
  );
}

function Section({ title, html }: { title: string; html: string }) {
  return (
    <div className="flex flex-col gap-1.5">
      <h4 className="text-sm font-bold text-[#edf0f5]">{title}</h4>
      <div
        className="tt-html text-sm leading-relaxed text-[#bfc4cc] [&_img]:max-w-full [&_a]:text-[#5ba8ff]"
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

function kindLabel(kind: string): string {
  if (kind === "testcases") return "Implementation";
  if (kind === "packets") return "Packets";
  return kind;
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
  const [files, setFiles] = useState<ArtifactFile[]>([]);
  const [loading, setLoading] = useState(false);
  const [selected, setSelected] = useState<string>("");

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

  const openSelected = () => {
    if (!selected) return;
    window.open(agent.artifactDownloadUrl(selected), "_blank", "noopener");
  };

  const deleteSelected = async () => {
    if (!selected) return;
    try {
      await agent.deleteArtifact(selected);
      pushLog("SUCCESS", "Deleted artifact.");
      setSelected("");
      refresh();
    } catch (e) {
      pushLog("ERROR", `Delete failed: ${(e as Error).message}`);
    }
  };

  if (!project)
    return (
      <p style={{ color: COLOR_MUTED }} className="text-sm">
        Select a project to view generated artifacts.
      </p>
    );

  return (
    <div className="flex h-full flex-col gap-2">
      <h3 className="text-sm font-bold text-[#edf0f5]">
        Generated artifacts{projectLabel ? ` - ${projectLabel}` : ""}
      </h3>
      <p className="text-xs text-[#8a8f99]">
        {files.length} file(s) across 1 board(s).
      </p>

      <div className="min-h-0 flex-1 overflow-auto rounded-[8px] border border-[#2d313c] bg-[#0d1017]">
        {files.length === 0 ? (
          <p style={{ color: COLOR_MUTED }} className="p-3 text-sm">
            No generated files yet. Generate test cases or package PDFs to see
            artifacts here.
          </p>
        ) : (
          files.map((f) => {
            const isSel = f.path === selected;
            return (
              <button
                key={f.path}
                onClick={() => setSelected(f.path)}
                onDoubleClick={openSelected}
                className="block w-full truncate px-3 py-1.5 text-left text-sm"
                style={{
                  background: isSel ? "#16466e" : "transparent",
                  color: isSel ? "#ffffff" : "#cfd4dc",
                }}
                title={f.name}
              >
                <span className="text-[#8a8f99]">▸ </span>
                {`[${kindLabel(f.kind)}] ${fmtTime(f.modified)}  ${f.name}`}
              </button>
            );
          })
        )}
      </div>

      <div className="flex items-center gap-2">
        <button
          className="tt-btn-primary !px-4 !py-1.5 text-sm"
          disabled={!selected}
          onClick={openSelected}
        >
          Open
        </button>
        <button
          className="tt-btn-danger !px-4 !py-1.5 text-sm"
          disabled={!selected}
          onClick={deleteSelected}
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
    </div>
  );
}
