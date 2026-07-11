# Testing Toolkit Agent — Installer Guide

This folder is **fully self-contained**. The entire install — Python packages,
MCP server npm packages, and Node.js itself — works with zero internet access.
All required files are bundled in the git repository (`main` + `parts` branches)
so installs succeed on PwC networks where npmjs.org and nodejs.org are blocked.

## What is bundled

| Resource | Location in repo | Size |
|---|---|---|
| Python wheels (win-x64) | `agent-bundle/wheelhouse/` | ~40MB |
| MCP npm packages + all deps (205 pkgs) | `agent-bundle/mcp_servers/npm-cache.tar.gz` | 30MB |
| MCP package.json + lockfile | `agent-bundle/mcp_servers/package*.json` | 88KB |
| Node.js v20.18.0 win-x64 (split) | `parts` branch `node-bins/win-x64/` | 29MB |
| Node.js v20.18.0 linux-x64 (split) | `parts` branch `node-bins/linux-x64/` | 45MB |
| Node binary manifest | `agent-bundle/mcp_servers/node-bins.json` | 1KB |

## Prerequisites

| Requirement | Version | Notes |
|---|---|---|
| Python | 3.9+ | Bundled `runtime/` for Windows; system Python on macOS/Linux |
| Node.js | 20 LTS | **Auto-installed from the parts branch if not found in PATH** |
| Git access | — | Only github.com/nrcharanvignesh/Testing-Toolkit (for Node.js download) |

## Install

### Windows

```bat
install.cmd
```

### macOS / Linux

```bash
chmod +x install.sh && ./install.sh
```

Or directly:

```bash
python install.py
```

## What the installer does

1. Detects platform (Windows/macOS/Linux, amd64/arm64).
2. Creates a clean Python virtual environment under `~/TestingToolkitWeb/venv/`.
3. Installs Python packages from `wheelhouse/` (offline on Windows x64) or PyPI.
4. Installs `cryptography` from bundled wheels so the authenticated GenAI
   credential envelope can be opened. Failure does not block non-AI features;
   AI fails closed.
5. Installs Playwright from PyPI and downloads Chromium. Non-fatal; skip with `TT_OFFLINE_ONLY=1`.
6. **Installs MCP servers — fully offline:**
   a. Checks if `node` is already in PATH.
   b. If not found: downloads Node.js from the `parts` branch (reassembles split parts,
      verifies sha256, extracts to `~/TestingToolkitWeb/mcp_servers/node/`).
   c. Extracts the bundled `npm-cache.tar.gz` into a temp directory.
   d. Runs `npm ci --offline --cache <extracted-cache>` using the bundled lockfile.
      Falls back to online `npm install` if offline install fails for any reason.
   e. Verifies entry points for all three MCP servers.
7. Copies agent `src/` into `~/TestingToolkitWeb/agent/`, restricts `.env.enc`
   to the current user, and starts the credential migration.
8. On first load, authenticates/decrypts the release envelope and rewraps it into
   Windows DPAPI, macOS Keychain, or Linux Secret Service when available.
9. Registers a login auto-start entry (Task Scheduler / launchd / systemd).
10. Starts the agent on `http://127.0.0.1:7842` and waits for healthy status.

## MCP servers

### Bundled packages

| Server | npm package | Version | Entry point |
|---|---|---|---|
| ADO | `@azure-devops/mcp` | 2.7.0 | `dist/index.js` |
| JIRA (Atlassian) | `mcp-atlassian` | 2.1.0 | `dist/index.js` |
| Playwright | `@playwright/mcp` | 0.0.77 | `cli.js` |

Installed to: `~/TestingToolkitWeb/mcp_servers/node_modules/`

### Credentials

Configure in the app under **Settings**:

| Server | Settings fields |
|---|---|
| ADO MCP | Organization URL + PAT (same as core ADO integration) |
| JIRA MCP | JIRA URL + Email + API Token (same as core JIRA integration) |
| Playwright MCP | None — starts headless automatically |

### Centrally managed GenAI credential

The user does not enter or view the GenAI URL/API key. Release owners keep
`LLM_UPSTREAM_BASE_URL` and `LLM_UPSTREAM_API_KEY` in Vercel project variables and
seal them before publishing:

```bash
python tools/seal_env.py --from-vercel-env
python tools/seal_env.py --verify
```

The tool reads only named process variables and prints metadata only. It writes
`src/.env.enc` atomically with owner-only permissions. The v2 format uses
AES-256-GCM, a randomized salt/nonce, Scrypt (`N=32768, r=8, p=1`), strict field
validation, and authenticated metadata. Never pass credentials on a command line,
put them in a plaintext `.env`, print them, or attach `.env.enc` to a ticket.

At runtime, `ai_credential_protection` in diagnostics reports only a non-secret
state such as `os-bound`, `release-envelope`, or `invalid-envelope`. A changed
release envelope is authenticated before it can replace an existing OS-bound
credential. Rotate by updating the Vercel variables, resealing, publishing a new
agent version, and verifying the state after upgrade.

### Manual install (if automatic install failed)

```bash
# Extract the bundled npm cache first (preferred -- fully offline)
cd ~/TestingToolkitWeb
mkdir -p mcp_servers
tar xzf /path/to/agent-bundle/mcp_servers/npm-cache.tar.gz -C /tmp/tt-npm-cache
npm ci --prefix mcp_servers --cache /tmp/tt-npm-cache/cache --offline

# Or online install as fallback
npm install --prefix mcp_servers \
  @azure-devops/mcp mcp-atlassian @playwright/mcp
```

### Verify

Open the app and click the **Layers (AI Stack)** icon in the activity bar.
The ADO, JIRA, and Playwright rows show `Connected` when the servers start.

## Installer options

| Flag | Effect |
|---|---|
| `--no-start` | Install only; do not launch the agent |
| `--no-autostart` | Skip login auto-start registration |

## Environment variables

| Variable | Default | Effect |
|---|---|---|
| `TT_INSTALL_DIR` | `~/TestingToolkitWeb` | Override install root |
| `TT_LOG_DIR` | `$TT_INSTALL_DIR/logs` | Override log directory |
| `TT_OFFLINE_ONLY` | unset | Set to `1` to skip Playwright browser download |
| `TT_UPDATE_TOKEN` | unset | GitHub PAT for auto-update from the `parts` branch |

## Node.js binary distribution

Node.js win-x64 and linux-x64 binaries are stored as 10MB parts on the `parts`
branch under `node-bins/`. The installer downloads and reassembles them only
when `node` is absent from PATH. Checksums are in `mcp_servers/node-bins.json`.

| Platform | Archive | Parts | SHA-256 |
|---|---|---|---|
| win-x64 | `node-v20.18.0-win-x64.zip` | 3 | `f5cea434...` |
| linux-x64 | `node-v20.18.0-linux-x64.tar.gz` | 5 | `24a5d58a...` |

macOS users: install Node.js via `brew install node` (Homebrew is not
blocked on PwC networks) or ask IT to install the pkg from the App Catalog.

## Troubleshoot

| Symptom | Fix |
|---|---|
| Agent did not start | Check `~/TestingToolkitWeb/logs/install-last.log` |
| MCP servers not found | Re-run installer; it will auto-install Node.js + npm packages |
| Node download failed | Ensure github.com is reachable; set `TT_INSTALL_DIR` if needed |
| E2E routes return 503 | Run `playwright install chromium` inside the venv |
| Import error at startup | Re-run the installer to get a clean venv |
