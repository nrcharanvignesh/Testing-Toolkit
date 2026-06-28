import { NextResponse } from "next/server"

export const runtime = "nodejs"
export const dynamic = "force-dynamic"

// Release location that holds the update manifest (matches the installer).
const REPO = "nrcharanvignesh/Testing-Toolkit"
const REF = "parts"

/**
 * Hands the locally-running agent the read-only update token + release
 * coordinates so it can self-update without a reinstall.
 *
 * Why this exists: older / token-less installs never received an update token
 * (their update.json has no token), so the agent reports `configured:false` and
 * can NEVER self-update — historically forcing a manual reinstall. The deployed
 * web app already holds a server-side token (BUNDLE_READ_TOKEN / GITHUB_TOKEN)
 * to read the private repo, and the browser is authenticated to BOTH the
 * SSO-protected deployment and the local agent. So the browser fetches this
 * config and POSTs it straight to the agent's /update/config — bridging the
 * token to any install fully automatically, with no human in the loop.
 *
 * The token is read-only (contents:read, single repo) and is only returned to a
 * request that already passed the deployment's SSO, exactly like the installer.
 */
export async function GET() {
  const token = process.env.BUNDLE_READ_TOKEN ?? process.env.GITHUB_TOKEN

  if (!token) {
    return NextResponse.json(
      { configured: false, error: "no_token" },
      { status: 503, headers: { "Cache-Control": "no-store" } },
    )
  }

  return NextResponse.json(
    {
      configured: true,
      token,
      repo: REPO,
      ref: REF,
      manifest_url: `https://api.github.com/repos/${REPO}/contents/agent-update.json?ref=${REF}`,
    },
    { headers: { "Cache-Control": "no-store" } },
  )
}
