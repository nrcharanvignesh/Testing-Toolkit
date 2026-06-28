"use client";

import { useState } from "react";
import {
  Stethoscope,
  CheckCircle2,
  AlertTriangle,
  XCircle,
  Loader2,
} from "lucide-react";
import { agent, type DoctorReport, type DoctorCheck } from "@/lib/agent-client";

/**
 * Diagnostics panel for the Settings dialog.
 *
 * Runs the agent's /doctor self-check and renders each result with a clear
 * pass/warn/fail badge plus the plain-language remediation the agent returns
 * for anything degraded. Also surfaces the REAL model execution provider
 * (CPU vs. an accelerator like CoreML/CUDA) reported by /doctor, so users can
 * confirm whether dense retrieval is actually GPU-accelerated.
 *
 * Fail-safe: older agents (< 2.3.0) 404 the endpoint, in which case the client
 * returns null and we show a short "not supported" note instead of an error.
 */
export function DiagnosticsSection() {
  const [report, setReport] = useState<DoctorReport | null>(null);
  const [busy, setBusy] = useState(false);
  const [ran, setRan] = useState(false);
  const [unsupported, setUnsupported] = useState(false);

  const run = async () => {
    setBusy(true);
    setRan(true);
    setUnsupported(false);
    try {
      const r = await agent.doctor();
      if (r === null) {
        setUnsupported(true);
        setReport(null);
      } else {
        setReport(r);
      }
    } catch {
      setUnsupported(true);
      setReport(null);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="mt-5 border-t border-border pt-4">
      <div className="flex items-center justify-between gap-3">
        <div>
          <h3 className="text-sm font-semibold text-foreground">Diagnostics</h3>
          <p className="mt-0.5 text-[11px] leading-relaxed text-muted-foreground">
            Check dense retrieval, models, the active GPU/CPU execution provider,
            disk space, and updates on this machine.
          </p>
        </div>
        <button
          className="tt-btn-ghost flex shrink-0 items-center gap-2"
          onClick={() => void run()}
          disabled={busy}
        >
          {busy ? (
            <Loader2 className="h-3.5 w-3.5 animate-spin" strokeWidth={2} />
          ) : (
            <Stethoscope className="h-3.5 w-3.5" strokeWidth={2} />
          )}
          {busy ? "Running..." : "Run diagnostics"}
        </button>
      </div>

      {ran && unsupported && (
        <p className="mt-3 text-[11px] leading-relaxed text-amber-300/90">
          This agent version doesn&apos;t support diagnostics. Reinstall or update
          the agent to enable them.
        </p>
      )}

      {report && (
        <div className="mt-3 flex flex-col gap-1.5">
          <OverallBadge status={report.status} />
          <div className="tt-input flex flex-col divide-y divide-border !p-0 text-xs">
            {report.checks.map((c) => (
              <CheckRow key={c.id} check={c} />
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

function OverallBadge({ status }: { status: DoctorReport["status"] }) {
  const map = {
    pass: { text: "All checks passed", cls: "text-[#7fd1b9]" },
    warn: { text: "Some optional checks need attention", cls: "text-amber-300" },
    fail: { text: "One or more required checks failed", cls: "text-red-300" },
  } as const;
  const m = map[status];
  return (
    <div className="flex items-center gap-2">
      <StatusIcon status={status} />
      <span className={`text-xs font-medium ${m.cls}`}>{m.text}</span>
    </div>
  );
}

function CheckRow({ check }: { check: DoctorCheck }) {
  return (
    <div className="flex items-start gap-2 p-2.5">
      <StatusIcon status={check.status} className="mt-0.5" />
      <div className="min-w-0 flex-1">
        <div className="flex items-center justify-between gap-2">
          <span className="font-medium text-foreground">{check.label}</span>
        </div>
        {check.detail && (
          <p className="mt-0.5 break-words text-[11px] leading-relaxed text-muted-foreground">
            {check.detail}
          </p>
        )}
        {check.fix && check.status !== "pass" && (
          <p className="mt-0.5 break-words text-[11px] leading-relaxed text-amber-300/80">
            {check.fix}
          </p>
        )}
      </div>
    </div>
  );
}

function StatusIcon({
  status,
  className = "",
}: {
  status: "pass" | "warn" | "fail";
  className?: string;
}) {
  const cls = `h-3.5 w-3.5 shrink-0 ${className}`;
  if (status === "pass")
    return <CheckCircle2 className={`${cls} text-[#7fd1b9]`} strokeWidth={2} />;
  if (status === "fail")
    return <XCircle className={`${cls} text-red-400`} strokeWidth={2} />;
  return <AlertTriangle className={`${cls} text-amber-400`} strokeWidth={2} />;
}
