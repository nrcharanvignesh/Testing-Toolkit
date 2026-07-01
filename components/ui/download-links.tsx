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
      className={`flex flex-col gap-2 rounded-lg border border-[#1aab5c]/40 bg-[#0d2a1c] p-3 ${className}`}
    >
      <h4 className="text-xs font-bold uppercase tracking-wide text-[#22c46a]">
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
              className="group flex items-center gap-2 rounded-md border border-[#2d313c] bg-[#13161d] px-2.5 py-1.5 text-sm text-[#7abaff] transition-colors hover:border-[#3a6ea5] hover:text-[#a9d0ff]"
              title={`Download ${it.name}`}
            >
              <Download className="h-4 w-4 shrink-0" />
              <span className="truncate">{it.name}</span>
              {it.note && (
                <span className="ml-auto shrink-0 text-xs text-[#8a8f99]">
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
