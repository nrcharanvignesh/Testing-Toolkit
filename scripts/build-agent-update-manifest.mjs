/**
 * build-agent-update-manifest.mjs
 *
 * Generates `agent-update.json`, the manifest the installed agent polls to
 * auto-update itself after a deployment.
 *
 * It walks agent-bundle/src, hashes every file, and emits a manifest whose
 * `version` is read from src/agent/version.py. Each entry points at the file in
 * the repo via the GitHub contents API at a chosen ref (branch), so the running
 * agent (which has a read-only token) can fetch changed files directly.
 *
 * Usage:
 *   node scripts/build-agent-update-manifest.mjs            # ref = project-deployment
 *   UPDATE_SRC_REF=main node scripts/build-agent-update-manifest.mjs
 *
 * Deploy flow (each time you ship an agent change):
 *   1. Bump AGENT_VERSION in agent-bundle/src/agent/version.py
 *   2. Commit/push the src changes to the src branch (UPDATE_SRC_REF)
 *   3. Run this script and commit the resulting agent-update.json to the
 *      `parts` branch (that is where install.py points the agent to look).
 * Installed agents poll every 60s, see the new version, pull the changed
 * files, and restart.
 */

import { createHash } from "node:crypto";
import {
  readFileSync,
  writeFileSync,
  readdirSync,
  statSync,
  existsSync,
} from "node:fs";
import { join, relative } from "node:path";

const REPO = "nrcharanvignesh/Testing-Toolkit";
const SRC_REF = process.env.UPDATE_SRC_REF || "project-deployment";

const ROOT = process.cwd();
const SRC_DIR = join(ROOT, "agent-bundle", "src");
const VERSION_FILE = join(SRC_DIR, "agent", "version.py");
const REQUIREMENTS_FILE = join(ROOT, "agent-bundle", "requirements.txt");
const EXTRA_WHEELS_DIR = join(ROOT, "agent-bundle", "extra-wheels");
const MCP_DIR = join(ROOT, "agent-bundle", "mcp_servers");
const OUT_FILE = join(ROOT, "agent-update.json");

function contentsUrl(repoPath) {
  return `https://api.github.com/repos/${REPO}/contents/${repoPath}?ref=${SRC_REF}`;
}

function readVersion() {
  const text = readFileSync(VERSION_FILE, "utf8");
  const m = text.match(/AGENT_VERSION\s*=\s*["']([^"']+)["']/);
  if (!m) throw new Error("Could not find AGENT_VERSION in version.py");
  return m[1];
}

function walk(dir, files = []) {
  for (const name of readdirSync(dir)) {
    if (name === "__pycache__" || name.endsWith(".pyc")) continue;
    const full = join(dir, name);
    const st = statSync(full);
    if (st.isDirectory()) walk(full, files);
    else files.push(full);
  }
  return files;
}

function main() {
  const version = readVersion();
  const files = walk(SRC_DIR).sort();

  const entries = files.map((full) => {
    const content = readFileSync(full);
    const hash = createHash("sha256").update(content).digest("hex");
    // installed-relative path (relative to src/), forward slashes
    const installedRel = relative(SRC_DIR, full).split("\\").join("/");
    // repo path for the GitHub contents API
    const repoPath = `agent-bundle/src/${installedRel}`;
    return { path: installedRel, url: contentsUrl(repoPath), hash };
  });

  // The bundle-root requirements.txt, overlaid so newly-added deps are picked
  // up by the offline installer without re-packing the 470 MB bundle.
  const requirements = existsSync(REQUIREMENTS_FILE)
    ? { url: contentsUrl("agent-bundle/requirements.txt") }
    : null;

  // Small, pure-Python wheels added after the bundle was built. The installer
  // drops these into the extracted wheelhouse so offline pip can find them.
  const extraWheels = existsSync(EXTRA_WHEELS_DIR)
    ? readdirSync(EXTRA_WHEELS_DIR)
        .filter((n) => n.endsWith(".whl"))
        .sort()
        .map((name) => ({
          name,
          url: contentsUrl(`agent-bundle/extra-wheels/${name}`),
        }))
    : [];

  const mcpFiles = existsSync(MCP_DIR)
    ? walk(MCP_DIR, [])
        .sort()
        .map((full) => {
          const content = readFileSync(full);
          const name = relative(MCP_DIR, full).split("\\").join("/");
          return {
            name,
            url: contentsUrl(`agent-bundle/mcp_servers/${name}`),
            hash: createHash("sha256").update(content).digest("hex"),
          };
        })
    : [];
  if (mcpFiles.length === 0) {
    throw new Error("agent-bundle/mcp_servers payload is missing");
  }

  const manifest = {
    version,
    ref: SRC_REF,
    generatedAt: new Date().toISOString(),
    files: entries,
    requirements,
    extraWheels,
    mcpFiles,
  };

  writeFileSync(OUT_FILE, JSON.stringify(manifest, null, 2) + "\n");
  console.log(
    `Wrote ${OUT_FILE}\n  version=${version} ref=${SRC_REF} files=${entries.length} extraWheels=${extraWheels.length} mcpFiles=${mcpFiles.length}`,
  );
  console.log(
    "Next: commit agent-update.json to the `parts` branch so installed agents can see it.",
  );
}

main();
