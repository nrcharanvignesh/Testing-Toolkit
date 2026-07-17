"use client";

import { Modal } from "@/components/ui/modal";
import { REQUIRED_AGENT_VERSION } from "@/lib/agent-version";

const APP_NAME = "Testing Toolkit";

/**
 * AboutDialog
 * Shows the app name, version, a short description, and the feature list.
 */
export function AboutDialog({ onClose }: { onClose: () => void }) {
  return (
    <Modal
      open
      onClose={onClose}
      title={`About ${APP_NAME}`}
      width={520}
      footer={
        <button className="tt-btn-primary" onClick={onClose}>
          OK
        </button>
      }
    >
      <div className="flex gap-4">
        {/* eslint-disable-next-line @next/next/no-img-element -- static icon, no optimization needed */}
        <img
          src="/icons/app_icon_64.png"
          alt=""
          aria-hidden
          width={64}
          height={64}
          className="h-16 w-16 shrink-0 rounded-lg"
          onError={(e) => {
            (e.currentTarget as HTMLImageElement).style.display = "none";
          }}
        />
        <div className="min-w-0">
          <h2 className="text-xl font-semibold text-foreground">{APP_NAME}</h2>
          <p className="mt-0.5 text-sm text-muted-foreground">
            Version {REQUIRED_AGENT_VERSION}
          </p>

          <p className="mt-4 text-sm leading-relaxed text-foreground">
            A unified Azure DevOps and Jira testing toolkit.
          </p>
          <p className="mt-3 text-sm leading-relaxed text-foreground">
            Browse boards, select work items, generate Test Cases with a
            Recursive Language Model over the project knowledge base, export
            boards to Excel, or package work items as PDFs.
          </p>

          <p className="mt-4 text-sm font-semibold text-foreground">Features:</p>
          <ul className="mt-1 list-disc space-y-0.5 pl-5 text-sm leading-relaxed text-foreground">
            <li>Board-driven work item selection with hyperlinked IDs</li>
            <li>Export board to Excel (filtered state, KPIs, hyperlinks)</li>
            <li>Export all boards as a multi-sheet workbook</li>
            <li>Recursive Language Model test case generation</li>
            <li>Per-client template support</li>
            <li>PDF packaging with KB-ready bundles</li>
            <li>Dense + lexical hybrid retrieval</li>
            <li>OCR for scanned documents</li>
            <li>Offline-first architecture</li>
          </ul>

          <p className="mt-4 text-xs leading-relaxed text-muted-foreground">
            Credentials stored in OS keyring. TLS uses a combined CA bundle for
            corporate proxy compatibility.
          </p>
        </div>
      </div>
    </Modal>
  );
}
