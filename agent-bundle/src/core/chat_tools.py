"""
chat_tools.py
Tool registry for the Custom Generate agentic chat. Exposes ADO module
functions as Claude tool_use schemas and dispatches tool calls to the
appropriate handler.

Architecture:
  - TOOL_DEFINITIONS: list of dicts ready for the API `tools` param.
  - execute_tool(name, input, ctx): dispatches to the real handler,
    returns a string result (JSON-serialised where appropriate).
  - ToolContext: holds runtime credentials so handlers can call APIs.
"""
from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Any, Final

from core.runtime_config import RuntimeConfig


# ---------------------------------------------------------------------
# Context passed to every tool handler
# ---------------------------------------------------------------------
@dataclass(slots=True)
class ToolContext:
    """Credentials and config needed by tool handlers."""
    # ADO
    ado_org: str = ""
    ado_project: str = ""
    ado_cfg: RuntimeConfig | None = None
    # MCP bridge (optional, set when MCP servers are running)
    mcp_bridge: Any = None

    @property
    def has_ado(self) -> bool:
        return bool(self.ado_org and self.ado_project and self.ado_cfg)


# ---------------------------------------------------------------------
# Tool definitions (Claude API format)
# ---------------------------------------------------------------------
_ADO_TOOLS: Final[list[dict[str, Any]]] = [
    {
        "name": "ado_run_wiql",
        "description": (
            "Run a WIQL query against Azure DevOps and return matching "
            "work item IDs. Use this to search for work items by state, "
            "type, assignment, iteration, or any field condition."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": (
                        "A valid WIQL query string. Example: "
                        "SELECT [System.Id] FROM WorkItems "
                        "WHERE [System.WorkItemType] = 'User Story' "
                        "AND [System.State] = 'Active'"
                    ),
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "ado_get_work_item",
        "description": (
            "Fetch full details of an Azure DevOps work item by ID. "
            "Returns title, description, acceptance criteria, state, "
            "type, assigned to, comments, and attachments metadata."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "work_item_id": {
                    "type": "integer",
                    "description": "The numeric work item ID.",
                },
            },
            "required": ["work_item_id"],
        },
    },
    {
        "name": "ado_get_comments",
        "description": (
            "Fetch all comments on an Azure DevOps work item. "
            "Returns author, date, and text for each comment."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "work_item_id": {
                    "type": "integer",
                    "description": "The numeric work item ID.",
                },
            },
            "required": ["work_item_id"],
        },
    },
    {
        "name": "ado_update_work_item",
        "description": (
            "Update fields on an Azure DevOps work item. Supports "
            "changing state, title, assigned to, tags, or any other "
            "patchable field."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "work_item_id": {
                    "type": "integer",
                    "description": "The numeric work item ID to update.",
                },
                "fields": {
                    "type": "object",
                    "description": (
                        "Key-value pairs of field reference names and "
                        "new values. Example: "
                        '{"System.State": "Active", '
                        '"System.AssignedTo": "user@example.com"}'
                    ),
                    "additionalProperties": {"type": "string"},
                },
            },
            "required": ["work_item_id", "fields"],
        },
    },
    {
        "name": "ado_add_comment",
        "description": (
            "Add a comment to an Azure DevOps work item."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "work_item_id": {
                    "type": "integer",
                    "description": "The work item ID to comment on.",
                },
                "text": {
                    "type": "string",
                    "description": "The comment text (plain text or HTML).",
                },
            },
            "required": ["work_item_id", "text"],
        },
    },
    {
        "name": "ado_create_work_item",
        "description": (
            "Create a new work item in Azure DevOps. Specify at minimum "
            "the work item type and title."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "work_item_type": {
                    "type": "string",
                    "description": (
                        "Type of work item: 'Bug', 'Task', "
                        "'User Story', 'Test Case', etc."
                    ),
                },
                "title": {
                    "type": "string",
                    "description": "Title of the new work item.",
                },
                "fields": {
                    "type": "object",
                    "description": (
                        "Optional additional field values. Example: "
                        '{"System.Description": "...", '
                        '"System.AreaPath": "Project\\\\Team"}'
                    ),
                    "additionalProperties": {"type": "string"},
                },
            },
            "required": ["work_item_type", "title"],
        },
    },
]



def get_tool_definitions(ctx: ToolContext) -> list[dict[str, Any]]:
    """Return tool definitions for available integrations.

    Official MCP servers are the PREFERRED source: when the MCP bridge is ready
    and exposes tools, those are used (the assistant drives ADO/JIRA through the
    official @azure-devops/mcp and mcp-atlassian servers). The in-process
    custom tools are only used as a fallback when no MCP server is available
    (Node.js or the bundled MCP packages missing), guaranteeing chat still works.
    """
    # Official MCP tools first (dynamic, may be empty if servers not ready).
    if ctx.mcp_bridge is not None:
        try:
            from core.mcp_bridge import MCPBridge

            bridge: MCPBridge = ctx.mcp_bridge
            if bridge.is_ready:
                mcp_tools = bridge.get_tool_definitions()
                if mcp_tools:
                    # Prefer official MCP tools; add custom tools only for
                    # capabilities MCP does not expose (dedup by name).
                    names = {t["name"] for t in mcp_tools}
                    merged = list(mcp_tools)
                    if ctx.has_ado:
                        for t in _ADO_TOOLS:
                            if t["name"] not in names:
                                merged.append(t)
                    return merged
        except Exception:
            pass  # fall through to custom tools

    # Fallback: in-process custom tools.
    tools: list[dict[str, Any]] = []
    if ctx.has_ado:
        tools.extend(_ADO_TOOLS)
    return tools


# ---------------------------------------------------------------------
# Tool execution handlers
# ---------------------------------------------------------------------
def _json_result(data: Any) -> str:
    """Serialize result for tool_result content."""
    if isinstance(data, str):
        return data
    return json.dumps(data, indent=2, ensure_ascii=True, default=str)


def _run_async(coro: Any) -> Any:
    """Run an async coroutine from sync context."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    if loop and loop.is_running():
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor(1) as pool:
            return pool.submit(asyncio.run, coro).result(timeout=120)
    return asyncio.run(coro)


# --- ADO handlers ---

def _ado_run_wiql(params: dict[str, Any], ctx: ToolContext) -> str:
    import httpx
    from ado.extract import run_wiql, build_auth_header

    cfg = ctx.ado_cfg
    assert cfg is not None

    async def _do() -> list[int]:
        async with httpx.AsyncClient(
            headers=build_auth_header(cfg.pat),
            timeout=httpx.Timeout(cfg.http_timeout_sec),
            verify=cfg.build_ssl(),
        ) as client:
            return await run_wiql(client, cfg, params["query"])

    ids = _run_async(_do())
    return _json_result({"work_item_ids": ids, "count": len(ids)})


def _ado_get_work_item(params: dict[str, Any], ctx: ToolContext) -> str:
    from ado.boards import fetch_work_item_detail

    cfg = ctx.ado_cfg
    assert cfg is not None
    wi_id = int(params["work_item_id"])
    detail = fetch_work_item_detail(ctx.ado_org, ctx.ado_project, wi_id, cfg)
    return _json_result({
        "id": detail.wi_id,
        "title": detail.title,
        "type": detail.wi_type,
        "state": detail.state,
        "assigned_to": detail.assigned_to,
        "area_path": detail.area_path,
        "iteration_path": detail.iteration_path,
        "tags": detail.tags,
        "description": detail.description_text[:3000],
        "acceptance_criteria": detail.acceptance_text[:3000],
        "comments_count": len(detail.comments),
        "attachments_count": len(detail.attachments),
    })


def _ado_get_comments(params: dict[str, Any], ctx: ToolContext) -> str:
    import httpx
    from ado.extract import fetch_comments, build_auth_header, html_to_text

    cfg = ctx.ado_cfg
    assert cfg is not None
    wi_id = int(params["work_item_id"])

    async def _do() -> list[dict[str, Any]]:
        async with httpx.AsyncClient(
            headers=build_auth_header(cfg.pat),
            timeout=httpx.Timeout(cfg.http_timeout_sec),
            verify=cfg.build_ssl(),
        ) as client:
            raw = await fetch_comments(client, cfg, wi_id)
            return [
                {
                    "author": (c.get("createdBy") or {}).get("displayName", ""),
                    "date": c.get("createdDate", ""),
                    "text": html_to_text(c.get("text", "")),
                }
                for c in raw
            ]

    comments = _run_async(_do())
    return _json_result({"work_item_id": wi_id, "comments": comments})


def _ado_update_work_item(params: dict[str, Any], ctx: ToolContext) -> str:
    import httpx
    from ado.extract import build_auth_header
    from core.runtime_config import API_VER_WI

    cfg = ctx.ado_cfg
    assert cfg is not None
    wi_id = int(params["work_item_id"])
    fields = params.get("fields", {})

    patch_ops = [
        {"op": "replace", "path": f"/fields/{k}", "value": v}
        for k, v in fields.items()
    ]

    async def _do() -> dict[str, Any]:
        url = (
            f"https://dev.azure.com/{ctx.ado_org}/{ctx.ado_project}"
            f"/_apis/wit/workitems/{wi_id}?api-version={API_VER_WI}"
        )
        headers = build_auth_header(cfg.pat)
        headers["Content-Type"] = "application/json-patch+json"
        async with httpx.AsyncClient(
            headers=headers,
            timeout=httpx.Timeout(cfg.http_timeout_sec),
            verify=cfg.build_ssl(),
        ) as client:
            r = await client.patch(url, json=patch_ops)
            r.raise_for_status()
            return r.json()

    result = _run_async(_do())
    return _json_result({
        "success": True,
        "id": result.get("id"),
        "title": (result.get("fields") or {}).get("System.Title", ""),
        "state": (result.get("fields") or {}).get("System.State", ""),
    })


def _ado_add_comment(params: dict[str, Any], ctx: ToolContext) -> str:
    import httpx
    from ado.extract import build_auth_header
    from core.runtime_config import API_VER_COMMENTS

    cfg = ctx.ado_cfg
    assert cfg is not None
    wi_id = int(params["work_item_id"])
    text = str(params["text"])

    async def _do() -> dict[str, Any]:
        url = (
            f"https://dev.azure.com/{ctx.ado_org}/{ctx.ado_project}"
            f"/_apis/wit/workItems/{wi_id}/comments"
            f"?api-version={API_VER_COMMENTS}"
        )
        async with httpx.AsyncClient(
            headers=build_auth_header(cfg.pat),
            timeout=httpx.Timeout(cfg.http_timeout_sec),
            verify=cfg.build_ssl(),
        ) as client:
            r = await client.post(url, json={"text": text})
            r.raise_for_status()
            return r.json()

    result = _run_async(_do())
    return _json_result({
        "success": True,
        "comment_id": result.get("id"),
        "work_item_id": wi_id,
    })


def _ado_create_work_item(params: dict[str, Any], ctx: ToolContext) -> str:
    import httpx
    from ado.extract import build_auth_header
    from core.runtime_config import API_VER_WI

    cfg = ctx.ado_cfg
    assert cfg is not None
    wi_type = str(params["work_item_type"])
    title = str(params["title"])
    extra_fields = params.get("fields", {}) or {}

    patch_ops = [
        {"op": "add", "path": "/fields/System.Title", "value": title},
    ]
    for k, v in extra_fields.items():
        patch_ops.append({"op": "add", "path": f"/fields/{k}", "value": v})

    async def _do() -> dict[str, Any]:
        url = (
            f"https://dev.azure.com/{ctx.ado_org}/{ctx.ado_project}"
            f"/_apis/wit/workitems/${wi_type}?api-version={API_VER_WI}"
        )
        headers = build_auth_header(cfg.pat)
        headers["Content-Type"] = "application/json-patch+json"
        async with httpx.AsyncClient(
            headers=headers,
            timeout=httpx.Timeout(cfg.http_timeout_sec),
            verify=cfg.build_ssl(),
        ) as client:
            r = await client.post(url, json=patch_ops)
            r.raise_for_status()
            return r.json()

    result = _run_async(_do())
    return _json_result({
        "success": True,
        "id": result.get("id"),
        "type": wi_type,
        "title": title,
        "url": result.get("_links", {}).get("html", {}).get("href", ""),
    })




# ---------------------------------------------------------------------
# Dispatch table
# ---------------------------------------------------------------------
_HANDLERS: Final[dict[str, Any]] = {
    "ado_run_wiql": _ado_run_wiql,
    "ado_get_work_item": _ado_get_work_item,
    "ado_get_comments": _ado_get_comments,
    "ado_update_work_item": _ado_update_work_item,
    "ado_add_comment": _ado_add_comment,
    "ado_create_work_item": _ado_create_work_item,
}


def execute_tool(
    name: str, tool_input: dict[str, Any], ctx: ToolContext,
) -> str:
    """Execute a tool call and return the string result.

    Official MCP servers are tried FIRST (the assistant's tools come from them
    when available); the in-process custom handlers are the fallback. On error,
    returns a JSON error object (never raises)."""
    # Official MCP bridge (dynamic tools from running servers) — preferred.
    if ctx.mcp_bridge is not None:
        try:
            from core.mcp_bridge import MCPBridge

            bridge: MCPBridge = ctx.mcp_bridge
            if bridge.has_tool(name):
                result = bridge.call_tool(name, tool_input)
                if result is not None:
                    return result
        except Exception as e:
            return _json_result({"error": str(e)})

    # Fallback: in-process custom handler.
    handler = _HANDLERS.get(name)
    if handler is not None:
        try:
            return handler(tool_input, ctx)
        except Exception as e:
            return _json_result({"error": str(e)})

    return _json_result({"error": f"Unknown tool: {name}"})
