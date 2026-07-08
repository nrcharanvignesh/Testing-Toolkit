# Testing Toolkit Agent — Installer Guide

This folder is **self-contained**. The core Python install uses only the files
here (`wheelhouse/`, `models/`, `src/`, `runtime/`) and never contacts the
public internet during the Python setup step — safe for locked-down corporate
networks.

Three optional features require an internet connection during setup:
- **Playwright** (E2E browser automation) — pulled from PyPI
- **MCP servers** (ADO, JIRA, Playwright MCP) — pulled from npm
- These are non-fatal: the agent starts and all core routes work without them.

## Prerequisites

| Requirement | Version | Notes |
|---|---|---|
| Python | 3.9+ | Bundled in `runtime/` for Windows; system Python used on macOS/Linux |
| Node.js | 18+ | Required for MCP servers (ADO, JIRA, Playwright MCP). Download from https://nodejs.org |

## Install

### Windows

Double-click `install.cmd`, or run it from a terminal:

```bat
install.cmd
```

### macOS / Linux

Run `install.sh` from a terminal inside this folder:

```bash
chmod +x install.sh
./install.sh
```

Or directly:

```bash
python install.py
```

## What the installer does

1. Detects platform (Windows/macOS/Linux, amd64/arm64).
2. Creates a clean Python virtual environment under `~/TestingToolkitWeb/venv/`.
3. Installs Python packages from `wheelhouse/` (offline on Windows x64) or
   from PyPI on other platforms.
4. **Installs Playwright** from PyPI and downloads the Chromium browser binary.
   Non-fatal — skipped on `TT_OFFLINE_ONLY=1`.
5. **Installs MCP servers** into `~/TestingToolkitWeb/mcp_servers/`:
   - `@azure-devops/mcp` — Azure DevOps integration
   - `@atlassian/jira-mcp` — Jira integration
   - `@playwright/mcp` — Playwright browser automation via MCP
   Non-fatal — skipped when Node.js/npm not found or `TT_OFFLINE_ONLY=1`.
6. Copies agent `src/` and ONNX `models/` into `~/TestingToolkitWeb/agent/`.
7. Registers a login auto-start entry (Task Scheduler / launchd / systemd).
8. Starts the agent on `http://127.0.0.1:7842` and waits for it to report healthy.

## MCP servers

The MCP (Model Context Protocol) servers give the AI chat assistant live access
to ADO, JIRA, and a browser. They are installed automatically when Node.js is
present. After install they live under:

```
~/TestingToolkitWeb/mcp_servers/node_modules/
  @azure-devops/mcp/
  @atlassian/jira-mcp/
  @playwright/mcp/
```

### Credentials

Configure credentials in the app under **Settings**:

| Server | Settings fields |
|---|---|
| ADO MCP | Organization URL, PAT — same as the core ADO integration |
| JIRA MCP | JIRA URL, Email, API Token — same as the core JIRA integration |
| Playwright MCP | No credentials required; starts headless automatically |

### Manual install (if npm is not found during setup)

```bash
cd ~/TestingToolkitWeb
npm install --prefix mcp_servers @azure-devops/mcp @atlassian/jira-mcp @playwright/mcp
```

### Verify

After install, open the app and click the **Layers (AI Stack)** icon in the
activity bar. The MCP row shows which servers are running and healthy.

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
| `TT_OFFLINE_ONLY` | unset | Set to `1` to skip all online steps (playwright, MCP) |
| `TT_ENFORCE_DENSE` | `1` | Set to `0` to allow install without bundled ONNX models |
| `TT_UPDATE_TOKEN` | unset | GitHub PAT for auto-update from the `parts` branch |

## Platform support

The bundled `wheelhouse/` currently ships **Windows x64** binary wheels.
To add offline support for other platforms, build platform wheels with:

```bash
pip download -r requirements.txt -d wheelhouse \
  --platform <tag> \
  --python-version 311 \
  --only-binary=:all:
```

And add a portable Python runtime under `runtime/<os>-<arch>/`.
The installer auto-detects OS/arch with no further code changes needed.

## Troubleshoot

- **Agent did not start**: check `~/TestingToolkitWeb/logs/install-last.log`
- **MCP servers not found**: ensure Node.js 18+ is installed and reachable in
  PATH, then re-run the installer or run the manual npm command above.
- **E2E routes return 503**: run `playwright install chromium` inside the venv.
- **Import error at startup**: re-run the installer to get a clean venv.
