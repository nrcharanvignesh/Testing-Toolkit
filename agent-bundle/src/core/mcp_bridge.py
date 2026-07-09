"""
mcp_bridge.py
MCP server lifecycle management and tool routing for the Testing Toolkit.

Manages long-running MCP server subprocesses (Node.js), converts MCP tool
schemas to Claude tool_use format, and routes tool calls to each server.

Bundled MCP servers (deployed by install.py from repo-committed assets,
fully offline, PwC-safe -- no npmjs.org or nodejs.org access needed):
  - ADO        @azure-devops/mcp  v2.7.0  - Azure DevOps work items, boards, PRs
  - JIRA       mcp-atlassian      v2.1.0  - Atlassian Jira + Confluence issues
  - Playwright @playwright/mcp    v0.0.77 - Browser automation via Playwright

install.py extracts a pre-built node_modules tarball (16MB) and the
Node.js v20.18.0 binary (win-x64 / linux-x64 / darwin-arm64) from split
parts committed directly to the main branch under agent-bundle/mcp_servers/.
No npm execution or network access is required during install.

Graceful degradation: if any MCP server fails to start the bridge returns an
empty tool list for that server and logs a warning. All other servers and the
custom tools in chat_tools.py remain available as the guaranteed fallback.

ponytail: single event loop thread for all MCP servers; dedicated loops per
server if contention becomes an issue.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import subprocess
import sys
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------
# Install-dir resolution (must match install.py INSTALL_DIR constant)
# ---------------------------------------------------------------------
def _install_dir() -> Path:
    """~/TestingToolkitWeb — the single root where install.py writes everything."""
    return Path(
        os.environ.get("TT_INSTALL_DIR", Path.home() / "TestingToolkitWeb")
    ).expanduser()


def _mcp_servers_dir() -> Path:
    """Locally-installed MCP servers (npm --prefix).

    install.py writes these under ~/TestingToolkitWeb/mcp_servers/ so the agent
    never needs a globally-installed npm package. Falls back to a path relative
    to this file when running from source during development.
    """
    local = _install_dir() / "mcp_servers"
    if local.is_dir():
        return local
    mei = getattr(sys, "_MEIPASS", "")
    if mei:
        return Path(mei) / "mcp_servers"
    # Dev fallback: src/mcp_servers/ sibling of src/core/
    return Path(__file__).resolve().parent.parent / "mcp_servers"


# ---------------------------------------------------------------------
# Node.js resolution
# ---------------------------------------------------------------------
def _node_exe() -> Path | None:
    """Find node executable.

    Search order:
      1. install.py-extracted bundle: mcp_servers/node/node.exe  (Windows)
         or mcp_servers/node/bin/node  (Linux/macOS)
      2. Legacy flat placement: mcp_servers/node.exe or mcp_servers/node
      3. System PATH
    """
    base = _mcp_servers_dir()
    for candidate in (
        base / "node" / "node.exe",        # win extracted by install.py
        base / "node" / "bin" / "node",    # posix extracted by install.py
        base / "node.exe",                 # legacy flat
        base / "node",                     # legacy flat
    ):
        if candidate.exists():
            return candidate
    which = shutil.which("node")
    return Path(which) if which else None


# ---------------------------------------------------------------------
# Per-server entry-point resolution
# (locally-installed first, then global npm, then None)
# ---------------------------------------------------------------------
def _npm_local_entry(pkg_scope: str, pkg_name: str, dist_index: str) -> Path | None:
    """Look up a locally-installed MCP server entry point.

    Resolves under <mcp_servers_dir>/node_modules/<pkg_scope>/<pkg_name>/dist/index.js
    where dist_index may be e.g. 'dist/index.js' or 'dist/server.js'.
    """
    base = _mcp_servers_dir() / "node_modules"
    entry = base / pkg_scope / pkg_name / dist_index
    if entry.exists():
        return entry
    return None


def _npm_global_entry(pkg_scope: str, pkg_name: str, dist_index: str) -> Path | None:
    """Look up a globally-installed MCP server entry point via AppData or `npm prefix -g`."""
    appdata = os.environ.get("APPDATA", "")
    if appdata:
        entry = (
            Path(appdata) / "npm" / "node_modules" / pkg_scope / pkg_name / dist_index
        )
        if entry.exists():
            return entry
    npm = shutil.which("npm")
    if npm:
        _sp_kwargs: dict[str, Any] = {}
        if sys.platform == "win32":
            _si = subprocess.STARTUPINFO()
            _si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            _si.wShowWindow = 0
            _sp_kwargs = {
                "startupinfo": _si,
                "creationflags": subprocess.CREATE_NO_WINDOW,
            }
        try:
            result = subprocess.run(
                [npm, "prefix", "-g"],
                capture_output=True, text=True, timeout=10,
                **_sp_kwargs,
            )
            prefix = result.stdout.strip()
            if prefix:
                entry = Path(prefix) / "node_modules" / pkg_scope / pkg_name / dist_index
                if entry.exists():
                    return entry
        except (OSError, subprocess.TimeoutExpired):
            pass
    return None


def _resolve_mcp_entry(pkg_scope: str, pkg_name: str, dist_index: str) -> Path | None:
    """Local install first, then global npm fallback."""
    return (
        _npm_local_entry(pkg_scope, pkg_name, dist_index)
        or _npm_global_entry(pkg_scope, pkg_name, dist_index)
    )


def _ado_entry() -> Path | None:
    # @azure-devops/mcp -> node_modules/@azure-devops/mcp/dist/index.js
    return _resolve_mcp_entry("@azure-devops", "mcp", "dist/index.js")


def _jira_entry() -> Path | None:
    # mcp-atlassian (the real Atlassian-backed package, not @atlassian/jira-mcp
    # which is 404 on npm) -> node_modules/mcp-atlassian/dist/index.js
    # ponytail: flat-scope package; _npm_local_entry expects scope/name so we
    # check directly here then fall back to the generic resolver.
    base = _mcp_servers_dir() / "node_modules"
    direct = base / "mcp-atlassian" / "dist" / "index.js"
    if direct.exists():
        return direct
    # Global npm fallback
    appdata = os.environ.get("APPDATA", "")
    if appdata:
        g = Path(appdata) / "npm" / "node_modules" / "mcp-atlassian" / "dist" / "index.js"
        if g.exists():
            return g
    return None


def _playwright_entry() -> Path | None:
    # @playwright/mcp ships its main entry at cli.js (not dist/index.js)
    base = _mcp_servers_dir() / "node_modules"
    entry = base / "@playwright" / "mcp" / "cli.js"
    if entry.exists():
        return entry
    return _resolve_mcp_entry("@playwright", "mcp", "cli.js")


# ---------------------------------------------------------------------
# Schema conversion
# ---------------------------------------------------------------------
def mcp_tool_to_claude_schema(mcp_tool: Any) -> dict[str, Any]:
    """Convert an MCP Tool object to Claude tool_use format."""
    schema = getattr(mcp_tool, "inputSchema", None)
    if schema is None:
        schema = {"type": "object", "properties": {}}
    elif hasattr(schema, "model_dump"):
        schema = schema.model_dump(exclude_none=True)
    elif not isinstance(schema, dict):
        schema = {"type": "object", "properties": {}}
    return {
        "name": mcp_tool.name,
        "description": getattr(mcp_tool, "description", "") or "",
        "input_schema": schema,
    }


# ---------------------------------------------------------------------
# Server config
# ---------------------------------------------------------------------
@dataclass(slots=True)
class MCPServerConfig:
    """Configuration for one MCP server subprocess."""
    server_id: str
    command: str
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    tool_filter: set[str] | None = None


# ---------------------------------------------------------------------
# Single server manager
# ---------------------------------------------------------------------
class MCPServerManager:
    """Manages a single MCP server subprocess and its stdio session."""

    def __init__(self, config: MCPServerConfig) -> None:
        self._config = config
        self._session: Any = None
        self._exit_stack: Any = None
        self._tools: list[dict[str, Any]] = []
        self._tool_names: set[str] = set()
        self._healthy = False

    @property
    def server_id(self) -> str:
        return self._config.server_id

    @property
    def is_healthy(self) -> bool:
        return self._healthy and self._session is not None

    @property
    def tools(self) -> list[dict[str, Any]]:
        return self._tools

    @property
    def tool_names(self) -> set[str]:
        return self._tool_names

    async def start(self) -> bool:
        """Start the MCP server subprocess and initialize the stdio session."""
        try:
            from mcp import ClientSession, StdioServerParameters
            from mcp.client.stdio import stdio_client
            from contextlib import AsyncExitStack

            params = StdioServerParameters(
                command=self._config.command,
                args=self._config.args,
                env=self._config.env or None,
            )

            self._exit_stack = AsyncExitStack()
            transport = await self._exit_stack.enter_async_context(
                stdio_client(params)
            )
            read_stream, write_stream = transport

            self._session = await self._exit_stack.enter_async_context(
                ClientSession(read_stream, write_stream)
            )
            await self._session.initialize()

            response = await self._session.list_tools()
            raw_tools = response.tools if response else []

            self._tools = []
            self._tool_names = set()
            for t in raw_tools:
                if self._config.tool_filter and t.name not in self._config.tool_filter:
                    continue
                schema = mcp_tool_to_claude_schema(t)
                self._tools.append(schema)
                self._tool_names.add(t.name)

            self._healthy = True
            log.info(
                "[INFO] MCP server '%s' started: %d tools",
                self._config.server_id, len(self._tools),
            )
            return True

        except Exception as e:
            log.warning(
                "[WARN] MCP server '%s' failed to start: %s",
                self._config.server_id, e,
            )
            self._healthy = False
            await self._cleanup()
            return False

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> str:
        """Invoke a tool by name. Returns a JSON-serialisable string result."""
        if not self.is_healthy or name not in self._tool_names:
            return json.dumps({"error": f"Tool '{name}' unavailable"})
        try:
            result = await self._session.call_tool(name, arguments)
            parts: list[str] = []
            for block in (result.content if result else []):
                if hasattr(block, "text"):
                    parts.append(block.text)
                elif hasattr(block, "data"):
                    parts.append(str(block.data))
            return "\n".join(parts) if parts else json.dumps({"result": "ok"})
        except Exception as e:
            log.warning("[WARN] MCP tool '%s' call failed: %s", name, e)
            return json.dumps({"error": str(e)})

    async def stop(self) -> None:
        self._healthy = False
        await self._cleanup()

    async def _cleanup(self) -> None:
        if self._exit_stack:
            try:
                await self._exit_stack.aclose()
            except Exception:
                pass
            self._exit_stack = None
            self._session = None


# ---------------------------------------------------------------------
# Bridge (orchestrates all servers)
# ---------------------------------------------------------------------
class MCPBridge:
    """Orchestrates multiple MCP servers. Thread-safe interface for
    AgenticStreamWorker to query tools and invoke them."""

    def __init__(self) -> None:
        self._servers: dict[str, MCPServerManager] = {}
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()
        self._started = False

    @property
    def is_ready(self) -> bool:
        return self._started and self._ready.is_set()

    def start_servers(self, configs: list[MCPServerConfig]) -> None:
        """Start all configured MCP servers in a background thread. Non-blocking."""
        if self._started:
            return
        self._started = True

        def _run() -> None:
            self._loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self._loop)
            try:
                self._loop.run_until_complete(self._start_all(configs))
            except Exception as e:
                log.warning("[WARN] MCP bridge startup error: %s", e)
            finally:
                self._ready.set()
            try:
                self._loop.run_forever()
            except Exception:
                pass

        self._thread = threading.Thread(target=_run, daemon=True, name="mcp-bridge")
        self._thread.start()

    async def _start_all(self, configs: list[MCPServerConfig]) -> None:
        for cfg in configs:
            mgr = MCPServerManager(cfg)
            ok = await mgr.start()
            if ok:
                self._servers[cfg.server_id] = mgr

    def get_tool_definitions(self) -> list[dict[str, Any]]:
        """Return all tools from all healthy servers in Claude format."""
        if not self.is_ready:
            return []
        tools: list[dict[str, Any]] = []
        for mgr in self._servers.values():
            if mgr.is_healthy:
                tools.extend(mgr.tools)
        return tools

    def has_tool(self, name: str) -> bool:
        for mgr in self._servers.values():
            if mgr.is_healthy and name in mgr.tool_names:
                return True
        return False

    def call_tool(self, name: str, arguments: dict[str, Any]) -> str | None:
        """Route a tool call to the correct server. Thread-safe.
        Returns None if no healthy server owns this tool name."""
        if not self._loop or not self.is_ready:
            return None
        for mgr in self._servers.values():
            if mgr.is_healthy and name in mgr.tool_names:
                future = asyncio.run_coroutine_threadsafe(
                    mgr.call_tool(name, arguments), self._loop,
                )
                try:
                    return future.result(timeout=120)
                except Exception as e:
                    return json.dumps({"error": str(e)})
        return None

    def stop_all(self) -> None:
        """Stop all servers and join the background thread."""
        if not self._loop:
            return
        for mgr in list(self._servers.values()):
            asyncio.run_coroutine_threadsafe(mgr.stop(), self._loop)
        self._loop.call_soon_threadsafe(self._loop.stop)
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5)
        self._servers.clear()
        self._started = False

    def server_ids(self) -> list[str]:
        """IDs of all currently healthy servers."""
        return [sid for sid, mgr in self._servers.items() if mgr.is_healthy]


# ---------------------------------------------------------------------
# Config builder
# ---------------------------------------------------------------------
def build_mcp_configs(
    ado_org: str = "",
    ado_pat: str = "",
    ado_project: str = "",
    ado_url: str = "",
    jira_url: str = "",
    jira_email: str = "",
    jira_token: str = "",
    playwright_headless: bool = True,
) -> list[MCPServerConfig]:
    """Build MCPServerConfig entries for each server that can be started.

    All four servers share the same locally-installed npm packages under
    ~/TestingToolkitWeb/mcp_servers/ (written by install.py). Each config is
    only created when the required credentials are present AND the entry-point
    JS file is found on disk (local install first, global npm fallback).

    Servers:
      ado        @azure-devops/mcp        - requires ado_org + ado_pat
      jira       @atlassian/jira-mcp      - requires jira_url + jira_email + jira_token
      playwright @playwright/mcp          - always available when node is found
    """
    configs: list[MCPServerConfig] = []
    node = _node_exe()

    if not node:
        log.warning(
            "[WARN] Node.js not found — MCP servers require Node.js 18+. "
            "Install Node.js from https://nodejs.org and re-run install.py."
        )
        return configs

    # ------------------------------------------------------------------ ADO --
    entry_ado = _ado_entry()
    if entry_ado and ado_org and ado_pat:
        base_url = ado_url or f"https://dev.azure.com/{ado_org}"
        configs.append(MCPServerConfig(
            server_id="ado",
            command=str(node),
            args=[str(entry_ado)],
            env={
                "AZURE_DEVOPS_URL": base_url,
                "AZURE_DEVOPS_PAT": ado_pat,
                "AZURE_DEVOPS_PROJECT": ado_project,
                "AZURE_DEVOPS_COLLECTION": "DefaultCollection",
            },
        ))
        log.info("[INFO] ADO MCP server configured: %s / %s", base_url, ado_project)
    elif not entry_ado:
        log.info(
            "[INFO] ADO MCP server not found — run install.py or "
            "npm install -g @azure-devops/mcp to enable it."
        )
    else:
        log.info("[INFO] ADO MCP server skipped (no credentials configured).")

    # ---------------------------------------------------------------- JIRA --
    entry_jira = _jira_entry()
    if entry_jira and jira_url and jira_email and jira_token:
        configs.append(MCPServerConfig(
            server_id="jira",
            command=str(node),
            args=[str(entry_jira)],
            env={
                "JIRA_URL": jira_url,
                "JIRA_EMAIL": jira_email,
                "JIRA_API_TOKEN": jira_token,
            },
        ))
        log.info("[INFO] JIRA MCP server configured: %s", jira_url)
    elif not entry_jira:
        log.info(
            "[INFO] JIRA MCP server not found — run install.py or "
            "npm install -g @atlassian/jira-mcp to enable it."
        )
    else:
        log.info("[INFO] JIRA MCP server skipped (no credentials configured).")

    # ----------------------------------------------------------- Playwright --
    entry_pw = _playwright_entry()
    if entry_pw:
        configs.append(MCPServerConfig(
            server_id="playwright",
            command=str(node),
            args=[
                str(entry_pw),
                "--headless" if playwright_headless else "--headed",
            ],
            env={},
        ))
        log.info(
            "[INFO] Playwright MCP server configured (headless=%s).",
            playwright_headless,
        )
    else:
        log.info(
            "[INFO] Playwright MCP server not found — run install.py or "
            "npm install -g @playwright/mcp to enable it."
        )

    return configs


# ---------------------------------------------------------------------
# Shared bridge singleton
# ---------------------------------------------------------------------
# One MCP bridge is shared process-wide so the official ADO/JIRA MCP servers
# are started once (subprocess spawn + stdio init is expensive) and reused
# across every chat request. The chat route attaches this to its ToolContext
# so tool calls route through the official servers, with the in-process
# chat_tools.py handlers as the guaranteed fallback when Node.js / the bundled
# MCP packages are unavailable.
_shared_bridge: "MCPBridge | None" = None
_shared_lock = threading.Lock()


def get_shared_bridge(
    ado_org: str = "",
    ado_pat: str = "",
    ado_project: str = "",
    ado_url: str = "",
    jira_url: str = "",
    jira_email: str = "",
    jira_token: str = "",
    wait_ready: float = 8.0,
) -> "MCPBridge | None":
    """Return the process-wide MCP bridge, starting the official servers on
    first use. Returns None when no server could be configured (e.g. Node.js or
    the bundled MCP packages are missing) so callers cleanly fall back to the
    custom in-process tools.

    ``wait_ready`` bounds how long we block for the background startup to finish
    on the FIRST call so the first chat request can already use MCP tools;
    subsequent calls return immediately.
    """
    global _shared_bridge
    with _shared_lock:
        if _shared_bridge is None:
            configs = build_mcp_configs(
                ado_org=ado_org,
                ado_pat=ado_pat,
                ado_project=ado_project,
                ado_url=ado_url,
                jira_url=jira_url,
                jira_email=jira_email,
                jira_token=jira_token,
            )
            # Only the ADO/JIRA data servers matter for chat; Playwright is a
            # browser-automation server used elsewhere. Keep only issue servers.
            configs = [c for c in configs if c.server_id in ("ado", "jira")]
            if not configs:
                return None
            bridge = MCPBridge()
            bridge.start_servers(configs)
            _shared_bridge = bridge
        b = _shared_bridge

    # Bound wait for readiness (background thread sets the ready event once all
    # servers have finished their startup attempt).
    if wait_ready > 0 and not b.is_ready:
        b._ready.wait(timeout=wait_ready)
    # If no server actually came up healthy, treat as unavailable so the caller
    # falls back to custom tools.
    if not b.server_ids():
        return None
    return b
