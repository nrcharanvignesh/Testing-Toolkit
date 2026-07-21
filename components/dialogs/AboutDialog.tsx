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
            An autonomous AI QA platform for Azure DevOps and Jira.
          </p>
          <p className="mt-3 text-sm leading-relaxed text-foreground">
            The AI agent studies your project knowledge base, discovers test
            cases via work item hierarchy, executes up to 3 user stories in
            parallel with fully isolated browser contexts, observes page state
            after every action, and produces PDF reports with video recordings.
          </p>

          <p className="mt-4 text-sm font-semibold text-foreground">Features:</p>
          <ul className="mt-1 list-disc space-y-0.5 pl-5 text-sm leading-relaxed text-foreground">
            <li>Autonomous E2E testing with KB-driven step discovery</li>
            <li>Up to 3 user stories in parallel (isolated browser contexts)</li>
            <li>Per-WI artifacts: PDF report, video recording, Excel append</li>
            <li>Self-healing locator waterfall (6 strategies + LLM recompile)</li>
            <li>Page observation with a11y tree analysis and confidence scoring</li>
            <li>Human review flow with per-TC approve/reject and sign-off</li>
            <li>Per-WI cancellation mid-run</li>
            <li>Board-driven work item selection with hyperlinked IDs</li>
            <li>Export board to Excel with audit sheets (coverage, traceability, defect density)</li>
            <li>Recursive Language Model test case generation</li>
            <li>Dense + lexical hybrid retrieval (BM25 + embeddings + reranker)</li>
            <li>Parent-child WI hierarchy TC discovery</li>
            <li>PDF packaging with KB-ready bundles</li>
            <li>OCR for scanned documents</li>
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
