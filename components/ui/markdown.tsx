"use client";

import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

/**
 * Markdown — renders assistant chat content as formatted markdown (GFM):
 * tables, lists, headings, bold/italic, inline code and fenced code blocks,
 * blockquotes and links. Styled with the app design tokens so it reads well in
 * both light and dark themes. Links open in a new tab.
 */
export function Markdown({ children }: { children: string }) {
  return (
    <div className="tt-markdown text-sm leading-relaxed text-[var(--tt-text-secondary)]">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          p: ({ children }) => <p className="mb-2 last:mb-0">{children}</p>,
          h1: ({ children }) => (
            <h1 className="mb-2 mt-1 text-base font-bold text-[var(--tt-text-primary)]">
              {children}
            </h1>
          ),
          h2: ({ children }) => (
            <h2 className="mb-2 mt-1 text-sm font-bold text-[var(--tt-text-primary)]">
              {children}
            </h2>
          ),
          h3: ({ children }) => (
            <h3 className="mb-1.5 mt-1 text-sm font-semibold text-[var(--tt-text-primary)]">
              {children}
            </h3>
          ),
          ul: ({ children }) => (
            <ul className="mb-2 ml-4 list-disc space-y-1">{children}</ul>
          ),
          ol: ({ children }) => (
            <ol className="mb-2 ml-4 list-decimal space-y-1">{children}</ol>
          ),
          li: ({ children }) => <li className="pl-0.5">{children}</li>,
          strong: ({ children }) => (
            <strong className="font-semibold text-[var(--tt-text-primary)]">
              {children}
            </strong>
          ),
          a: ({ children, href }) => (
            <a
              href={href}
              target="_blank"
              rel="noopener noreferrer"
              className="text-[var(--tt-primary)] underline underline-offset-2 hover:opacity-80"
            >
              {children}
            </a>
          ),
          blockquote: ({ children }) => (
            <blockquote className="mb-2 border-l-2 border-[var(--tt-outline)] pl-3 text-[var(--tt-text-muted)]">
              {children}
            </blockquote>
          ),
          code: ({ className, children }) => {
            const isBlock = (className ?? "").includes("language-");
            if (isBlock) {
              return (
                <code className="block overflow-x-auto rounded-md border border-[var(--tt-outline)] bg-[var(--tt-surface-high)] p-2 font-mono text-xs text-[var(--tt-text-primary)]">
                  {children}
                </code>
              );
            }
            return (
              <code className="rounded bg-[var(--tt-surface-high)] px-1 py-0.5 font-mono text-[12px] text-[var(--tt-text-primary)]">
                {children}
              </code>
            );
          },
          pre: ({ children }) => <pre className="mb-2">{children}</pre>,
          table: ({ children }) => (
            <div className="mb-2 overflow-x-auto">
              <table className="w-full border-collapse text-xs">
                {children}
              </table>
            </div>
          ),
          thead: ({ children }) => (
            <thead className="bg-[var(--tt-surface-high)]">{children}</thead>
          ),
          th: ({ children }) => (
            <th className="border border-[var(--tt-outline)] px-2 py-1 text-left font-semibold text-[var(--tt-text-primary)]">
              {children}
            </th>
          ),
          td: ({ children }) => (
            <td className="border border-[var(--tt-outline)] px-2 py-1 align-top">
              {children}
            </td>
          ),
          hr: () => (
            <hr className="my-3 border-[var(--tt-outline-soft)]" />
          ),
        }}
      >
        {children}
      </ReactMarkdown>
    </div>
  );
}
