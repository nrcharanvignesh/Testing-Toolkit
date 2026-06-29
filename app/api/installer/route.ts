import { NextResponse } from "next/server"
import type { NextRequest } from "next/server"
import { buildWindowsInstaller, buildUnixInstaller } from "@/lib/installer-template"

export const runtime = "nodejs"
export const dynamic = "force-dynamic"

// Repo + branch that hold the split bundle parts and manifest.
const REPO = "nrcharanvignesh/Testing-Toolkit"
const REF = "parts"

/**
 * Serves the tiny Windows installer (a windowless .vbs launcher) that the user
 * downloads and runs. Running a .vbs uses wscript.exe, which has no console
 * window, so there is no terminal flash; it starts the PowerShell worker fully
 * hidden. The worker fetches the bundle parts directly from GitHub in parallel,
 * so it never has to pass the project's SSO at install time.
 *
 * A GitHub token is injected here at download time, so it never lives in the
 * repo or in client-side source. A human downloads this file through the
 * browser (passing the project's SSO), and the running installer uses the
 * embedded token to fetch the parts from GitHub.
 *
 * Prefers BUNDLE_READ_TOKEN (intended to be a fine-grained, contents:read,
 * single-repo token - the safest choice to embed in a distributed file). Falls
 * back to GITHUB_TOKEN when BUNDLE_READ_TOKEN is not set. NOTE: GITHUB_TOKEN is
 * broadly privileged, so for production hardening prefer a dedicated read-only
 * token.
 */
export async function GET(req: NextRequest) {
  const token = process.env.BUNDLE_READ_TOKEN ?? process.env.GITHUB_TOKEN

  if (!token) {
    return new NextResponse(
      "Installer is not configured yet: no GitHub token is available. " +
        "Add a fine-grained, contents:read token for this repo as the " +
        "BUNDLE_READ_TOKEN environment variable, then redeploy.",
      { status: 503, headers: { "Content-Type": "text/plain" } },
    )
  }

  // Default to Windows for backward compatibility (?os=windows|mac|linux).
  const os = (req.nextUrl.searchParams.get("os") || "windows").toLowerCase()
  // ?fresh=1 (used by the reinstall flow) makes the installer ignore any
  // previously downloaded bundle parts and re-download everything from scratch.
  const fresh = req.nextUrl.searchParams.get("fresh") === "1"

  let script: string
  let filename: string
  if (os === "mac" || os === "linux") {
    script = buildUnixInstaller(REPO, REF, token, fresh)
    filename =
      os === "mac"
        ? "Testing-Toolkit-Installer.command"
        : "Testing-Toolkit-Installer.sh"
  } else {
    script = buildWindowsInstaller(REPO, REF, token, fresh)
    // A .vbs is run by wscript.exe, which has NO console window, so there is zero
    // terminal flash. The launcher starts the PowerShell worker fully hidden.
    filename = "Testing-Toolkit-Installer.vbs"
  }

  return new NextResponse(script, {
    status: 200,
    headers: {
      "Content-Type": "application/octet-stream",
      "Content-Disposition": `attachment; filename="${filename}"`,
      "Cache-Control": "no-store",
    },
  })
}
