"use client";

import { Download } from "lucide-react";

export type DownloadItem = {
  /** File name shown to the user and used as the download filename hint. */
  name: string;
  /** Browser-loadable URL that streams/downloads the file from the agent. */
  url: string;
  /** Optional trailing note, e.g. a size or item count. */
  note?: string;
};

/** Human-readable byte size for a download note. */
export function humanSize(bytes: number): string {
  if (!bytes || bytes < 0) return "";
  const units = ["B", "KB", "MB", "GB"];
  let n = bytes;
  let i = 0;
  while (n >= 1024 && i < units.length - 1) {
    n /= 1024;
    i += 1;
  }
  return `${n >= 10 || i === 0 ? Math.round(n) : n.toFixed(1)} ${units[i]}`;
}

/**
 * App-wide inline download panel. Renders a labeled, green-accented card with a
 * clickable download link per generated file, shown in the same section/window
 * where the output was produced. The agent's file responses set an attachment
 * Content-Disposition, so clicking a link saves the file with its real name.
 */
export function DownloadLinks({
  items,
  title = "Ready to download",
  className = "",
}: {
  items: DownloadItem[];
  title?: string;
  className?: string;
}) {
  if (!items.length) return null;
  return (
    <div
      className={`flex flex-col gap-2 rounded-lg border border-[var(--tt-success)]/40 bg-[var(--tt-success-bg)] p-3 ${className}`}
    >
      <h4 className="text-xs font-bold uppercase tracking-wide text-[var(--tt-success-hover)]">
        {title}
      </h4>
      <ul className="flex flex-col gap-1.5">
        {items.map((it) => (
          <li key={it.url + it.name}>
            <a
              href={it.url}
              download={it.name}
              target="_blank"
              rel="noopener noreferrer"
              className="group flex items-center gap-2 rounded-md border border-[var(--tt-outline)] bg-[var(--tt-surface-base)] px-2.5 py-1.5 text-sm text-[var(--tt-primary-soft)] transition-colors hover:border-[var(--tt-link-hover-border)] hover:text-[var(--tt-primary-bright)]"
              title={`Download ${it.name}`}
            >
              <Download className="h-4 w-4 shrink-0" />
              <span className="truncate">{it.name}</span>
              {it.note && (
                <span className="ml-auto shrink-0 text-xs text-[var(--tt-text-muted)]">
                  {it.note}
                </span>
              )}
            </a>
          </li>
        ))}
      </ul>
    </div>
  );
}
