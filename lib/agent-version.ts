/**
 * agent-version.ts
 * Minimum-version handshake between the web app and the local agent.
 *
 * The web app is the orchestrator: it knows which agent contract it was built
 * against. REQUIRED_AGENT_VERSION is that floor. On connect the app compares the
 * agent's reported /health version against this floor and, if the agent is
 * older, blocks the whole app UNCONDITIONALLY — independent of GitHub, the
 * update manifest, or whether auto-update is configured. This is the guarantee
 * that a stale agent can never silently keep running against a newer web build.
 *
 * BUMP THIS whenever a web change requires a matching agent change (new/changed
 * endpoint, response shape, behavior). Keep it equal to the lowest agent
 * AGENT_VERSION that this web build is known to work with.
 */
export const REQUIRED_AGENT_VERSION = "3.33.3";

/** Parse a dotted version ("1.7.0") into numeric parts. Missing/garbage -> 0s. */
function parseVersion(v: string | null | undefined): number[] {
  if (!v) return [0, 0, 0];
  return v
    .trim()
    .split(".")
    .map((p) => {
      const n = parseInt(p, 10);
      return Number.isFinite(n) ? n : 0;
    });
}

/**
 * Compare two dotted versions. Returns -1 if a<b, 0 if equal, 1 if a>b.
 * Compares part-by-part so "1.10.0" > "1.9.0".
 */
export function compareVersions(a: string | null, b: string | null): number {
  const pa = parseVersion(a);
  const pb = parseVersion(b);
  const len = Math.max(pa.length, pb.length);
  for (let i = 0; i < len; i++) {
    const x = pa[i] ?? 0;
    const y = pb[i] ?? 0;
    if (x < y) return -1;
    if (x > y) return 1;
  }
  return 0;
}

/**
 * True when the agent version is strictly older than the floor the web app
 * requires. An "unknown" version (older agents that don't report one) is treated
 * as outdated so it gets caught too. We never block on a NEWER agent.
 */
export function isAgentOutdated(
  agentVersion: string | null | undefined
): boolean {
  if (!agentVersion || agentVersion === "unknown") return true;
  return compareVersions(agentVersion, REQUIRED_AGENT_VERSION) < 0;
}
