"use client";

import { useEffect, useState } from "react";
import { RefreshCw, Upload, ExternalLink, FileText } from "lucide-react";
import {
  agent,
  type WorkItemDetail,
  type ArtifactFile,
} from "@/lib/agent-client";
import { useAppState } from "@/lib/app-state";
import { COLOR_MUTED, stateColor } from "@/lib/board-utils";

interface DetailPaneProps {
  activeWiId: number | null;
}

export function DetailPane({ activeWiId }: DetailPaneProps) {
  const { currentProject, settings, openDialog, setGenerateCtx, pushLog } =
    useAppState();
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

  return (
    <div className="tt-card flex h-full flex-col gap-2 p-2.5">
      <div className="flex items-center gap-2">
        <button
          className="tt-btn-ghost !px-3 !py-1 text-xs"
          data-active={mode === "detail"}
          onClick={() => setMode("detail")}
        >
          Detail
        </button>
        <button
          className="tt-btn-ghost !px-3 !py-1 text-xs"
          data-active={mode === "outputs"}
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
          <ExternalLink className="h-3.5 w-3.5" /> Open in ADO
        </button>
      </div>

      <div className="min-h-0 flex-1 overflow-auto rounded-[10px] border border-[#2d313c] bg-[#13161d] p-4">
        {mode === "detail" ? (
          <DetailContent loading={loading} detail={detail} hasItem={activeWiId != null} />
        ) : (
          <OutputsContent
            project={currentProject}
            onRegenerate={(path) => {
              setGenerateCtx({ tcType: "", xlsxPath: path });
              openDialog("generate");
            }}
            onUpload={() => openDialog("upload")}
          />
        )}
      </div>
    </div>
  );
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

  return (
    <div className="flex flex-col gap-4 text-sm">
      <div className="flex flex-col gap-1">
        <div className="flex items-center gap-2">
          <span className="tt-card-id font-bold">#{detail.wi_id}</span>
          <span
            className="tt-state-pill"
            style={{
              color: stateColor(detail.state),
              borderColor: stateColor(detail.state),
            }}
          >
            {detail.state || "n/a"}
          </span>
          <span className="text-xs text-muted-foreground">{detail.wi_type}</span>
        </div>
        <h3 className="text-base font-semibold text-[#edf0f5]">
          {detail.title}
        </h3>
        <div className="flex flex-wrap gap-x-4 gap-y-0.5 text-xs text-muted-foreground">
          {detail.assigned_to && <span>Assignee: {detail.assigned_to}</span>}
          {detail.iteration_path && <span>Sprint: {detail.iteration_path}</span>}
          {detail.area_path && <span>Area: {detail.area_path}</span>}
        </div>
      </div>

      {detail.description_html && (
        <Section title="Description" html={detail.description_html} />
      )}
      {detail.acceptance_html && (
        <Section title="Acceptance Criteria" html={detail.acceptance_html} />
      )}

      {detail.comments_html?.length > 0 && (
        <div className="flex flex-col gap-2">
          <h4 className="text-xs font-bold uppercase tracking-wide text-[#7abaff]">
            Comments
          </h4>
          {detail.comments_html.map(([who, when, html], i) => (
            <div key={i} className="rounded-lg border border-[#2d313c] p-2">
              <div className="mb-1 text-xs text-muted-foreground">
                {who} · {when}
              </div>
              <div
                className="tt-html prose-invert text-sm"
                dangerouslySetInnerHTML={{ __html: html }}
              />
            </div>
          ))}
        </div>
      )}

      {detail.attachments?.length > 0 && (
        <div className="flex flex-col gap-1.5">
          <h4 className="text-xs font-bold uppercase tracking-wide text-[#7abaff]">
            Attachments
          </h4>
          {detail.attachments.map((a) => (
            <a
              key={a.url}
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
          <h4 className="text-xs font-bold uppercase tracking-wide text-[#7abaff]">
            Links
          </h4>
          {detail.hyperlinks.map(([label, url]) => (
            <a
              key={url}
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
      <h4 className="text-xs font-bold uppercase tracking-wide text-[#7abaff]">
        {title}
      </h4>
      <div
        className="tt-html text-sm leading-relaxed text-[#bfc4cc] [&_img]:max-w-full [&_a]:text-[#5ba8ff]"
        dangerouslySetInnerHTML={{ __html: html }}
      />
    </div>
  );
}

function OutputsContent({
  project,
  onRegenerate,
  onUpload,
}: {
  project: string;
  onRegenerate: (path: string) => void;
  onUpload: () => void;
}) {
  const [files, setFiles] = useState<ArtifactFile[]>([]);
  const [loading, setLoading] = useState(false);

  const refresh = () => {
    if (!project) return;
    setLoading(true);
    agent
      .listArtifacts(project)
      .then(setFiles)
      .catch(() => setFiles([]))
      .finally(() => setLoading(false));
  };

  useEffect(refresh, [project]);

  if (!project)
    return (
      <p style={{ color: COLOR_MUTED }} className="text-sm">
        Select a project to view generated outputs.
      </p>
    );

  return (
    <div className="flex flex-col gap-2">
      <div className="flex items-center justify-between">
        <h4 className="text-xs font-bold uppercase tracking-wide text-[#7abaff]">
          Generated Outputs
        </h4>
        <button className="tt-btn-ghost !px-2 !py-1 text-xs" onClick={refresh}>
          <RefreshCw className={`h-3.5 w-3.5 ${loading ? "animate-spin" : ""}`} />
        </button>
      </div>
      {files.length === 0 ? (
        <p style={{ color: COLOR_MUTED }} className="text-sm">
          No generated files yet. Generate test cases or package PDFs to see
          outputs here.
        </p>
      ) : (
        files.map((f) => (
          <div
            key={f.path}
            className="flex items-center gap-2 rounded-lg border border-[#2d313c] bg-[#1a1d26] px-3 py-2"
          >
            <FileText className="h-4 w-4 shrink-0 text-[#5ba8ff]" />
            <div className="min-w-0 flex-1">
              <div className="truncate text-sm text-[#edf0f5]">{f.name}</div>
              <div className="text-xs text-muted-foreground">{f.kind}</div>
            </div>
            <button
              className="tt-btn-ghost !px-2 !py-1 text-xs"
              onClick={() => onRegenerate(f.path)}
            >
              <RefreshCw className="h-3.5 w-3.5" /> Regenerate
            </button>
            <button
              className="tt-btn-ghost !px-2 !py-1 text-xs"
              onClick={onUpload}
            >
              <Upload className="h-3.5 w-3.5" /> Upload
            </button>
          </div>
        ))
      )}
    </div>
  );
}
