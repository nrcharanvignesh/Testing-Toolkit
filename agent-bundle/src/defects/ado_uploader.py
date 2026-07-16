"""
defects/ado_uploader.py
Create Bug work items in Azure DevOps from reviewed defects.
Derives Area Path, Iteration Path, and Board Column from the parent
work item automatically.
"""

from __future__ import annotations

import asyncio
import base64
from dataclasses import dataclass, field
from typing import Any, Callable

import httpx

from core.runtime_config import RuntimeConfig

LogFn = Callable[[str], None]
ProgressFn = Callable[[str, int, int], None]

_API_VERSION = "7.1"
_WI_TYPE = "$Bug"
_TARGET_COLUMNS = (
    "Ready for Triage",
    "Ready for Development",
    "New",
    "To Do",
    "Backlog",
)
_CONCURRENCY = 4


def _auth_headers(pat: str) -> dict[str, str]:
    token = base64.b64encode(f":{pat}".encode("ascii")).decode("ascii")
    return {
        "Authorization": f"Basic {token}",
        "Content-Type": "application/json-patch+json",
        "Accept": "application/json",
    }


@dataclass(slots=True)
class DefectCreateResult:
    title: str
    parent_id: int = 0
    created_id: int = 0
    created_url: str = ""
    ok: bool = True
    error: str = ""


@dataclass(slots=True)
class BulkDefectResult:
    results: list[DefectCreateResult] = field(default_factory=list)
    n_ok: int = 0
    n_failed: int = 0


async def _fetch_parent_fields(
    client: httpx.AsyncClient,
    org: str,
    project: str,
    parent_id: int,
    cache: dict[int, dict[str, Any]],
) -> dict[str, Any]:
    """Fetch parent WI fields: AreaPath, IterationPath, BoardColumn."""
    if parent_id in cache:
        return cache[parent_id]
    url = (
        f"https://dev.azure.com/{org}/{project}/_apis/wit/workitems/"
        f"{parent_id}?$expand=fields&api-version={_API_VERSION}"
    )
    try:
        resp = await client.get(url)
        if resp.status_code == 200:
            data = resp.json()
            fields = data.get("fields", {})
            info = {
                "area_path": str(fields.get("System.AreaPath", "") or ""),
                "iteration_path": str(
                    fields.get("System.IterationPath", "") or ""
                ),
                "board_column": str(
                    fields.get("System.BoardColumn", "") or ""
                ),
            }
            cache[parent_id] = info
            return info
    except Exception:
        pass
    cache[parent_id] = {}
    return {}


def _determine_board_column(parent_fields: dict[str, Any]) -> str:
    """Determine the target board column for a new bug. Tries to place it in
    a 'triage' or 'ready for dev' column."""
    parent_col = str(parent_fields.get("board_column", "") or "")
    # If parent is already in a triage/todo state, inherit.
    for target in _TARGET_COLUMNS:
        if target.lower() in parent_col.lower():
            return parent_col
    # Default to "New" - ADO will place it in the first column.
    return ""


def _build_bug_patch(
    title: str,
    description: str,
    repro_steps: str,
    severity: str,
    area_path: str,
    iteration_path: str,
    parent_id: int,
    org: str,
    expected_result: str = "",
    actual_result: str = "",
) -> list[dict[str, Any]]:
    """Build the JSON Patch document for creating a Bug work item."""
    ops: list[dict[str, Any]] = [
        {"op": "add", "path": "/fields/System.Title", "value": title},
    ]

    # Build rich repro steps HTML.
    repro_html = ""
    if repro_steps:
        steps_lines = repro_steps.strip().splitlines()
        repro_html = "<ol>" + "".join(
            f"<li>{line.lstrip('0123456789.-) ').strip()}</li>"
            for line in steps_lines if line.strip()
        ) + "</ol>"
    if description or repro_html or expected_result or actual_result:
        full_repro = ""
        if description:
            full_repro += f"<p>{description}</p>"
        if repro_html:
            full_repro += f"<p><b>Steps to Reproduce:</b></p>{repro_html}"
        if expected_result:
            full_repro += f"<p><b>Expected Result:</b> {expected_result}</p>"
        if actual_result:
            full_repro += f"<p><b>Actual Result:</b> {actual_result}</p>"
        ops.append({
            "op": "add",
            "path": "/fields/Microsoft.VSTS.TCM.ReproSteps",
            "value": full_repro,
        })

    # Severity mapping (ADO uses 1-4 numeric).
    sev_map = {"critical": "1 - Critical", "high": "2 - High",
               "medium": "3 - Medium", "low": "4 - Low"}
    sev_value = sev_map.get((severity or "medium").lower(), "3 - Medium")
    ops.append({
        "op": "add",
        "path": "/fields/Microsoft.VSTS.Common.Severity",
        "value": sev_value,
    })

    if area_path:
        ops.append({
            "op": "add", "path": "/fields/System.AreaPath",
            "value": area_path,
        })
    if iteration_path:
        ops.append({
            "op": "add", "path": "/fields/System.IterationPath",
            "value": iteration_path,
        })

    # Link to parent work item (Related).
    if parent_id > 0:
        ops.append({
            "op": "add",
            "path": "/relations/-",
            "value": {
                "rel": "System.LinkTypes.Related",
                "url": f"https://dev.azure.com/{org}/_apis/wit/workItems/"
                       f"{parent_id}",
            },
        })

    return ops


async def _create_one_bug(
    client: httpx.AsyncClient,
    org: str,
    project: str,
    defect: Any,
    parent_cache: dict[int, dict[str, Any]],
    on_log: LogFn | None,
) -> DefectCreateResult:
    """Create a single Bug work item in ADO."""
    title = defect.title
    parent_id = defect.parent_id

    # Derive paths from parent.
    area_path = ""
    iteration_path = ""
    if parent_id > 0:
        parent_fields = await _fetch_parent_fields(
            client, org, project, parent_id, parent_cache
        )
        area_path = parent_fields.get("area_path", "")
        iteration_path = parent_fields.get("iteration_path", "")

    ops = _build_bug_patch(
        title=title,
        description=defect.description,
        repro_steps=defect.repro_steps,
        severity=defect.severity,
        area_path=area_path,
        iteration_path=iteration_path,
        parent_id=parent_id,
        org=org,
        expected_result=getattr(defect, "expected_result", ""),
        actual_result=getattr(defect, "actual_result", ""),
    )

    url = (
        f"https://dev.azure.com/{org}/{project}/_apis/wit/workitems/"
        f"{_WI_TYPE}?api-version={_API_VERSION}"
    )
    try:
        resp = await client.post(url, json=ops)
        if resp.status_code in (200, 201):
            data = resp.json()
            created_id = int(data.get("id", 0))
            wi_url = str(data.get("_links", {}).get("html", {}).get(
                "href", "") or f"https://dev.azure.com/{org}/{project}/"
                f"_workitems/edit/{created_id}")
            if on_log:
                on_log(f"[SUCCESS] Created Bug #{created_id}: {title}")
            return DefectCreateResult(
                title=title, parent_id=parent_id,
                created_id=created_id, created_url=wi_url,
            )
        else:
            detail = ""
            try:
                detail = resp.json().get("message", resp.text[:200])
            except Exception:
                detail = resp.text[:200]
            if on_log:
                on_log(f"[ERROR] Failed to create Bug '{title}': "
                       f"HTTP {resp.status_code} - {detail}")
            return DefectCreateResult(
                title=title, parent_id=parent_id,
                ok=False, error=f"HTTP {resp.status_code}: {detail}",
            )
    except Exception as e:
        if on_log:
            on_log(f"[ERROR] Failed to create Bug '{title}': {e!r}")
        return DefectCreateResult(
            title=title, parent_id=parent_id,
            ok=False, error=str(e),
        )


async def upload_defects_async(
    defects: list[Any],
    org: str,
    project: str,
    pat: str,
    cfg: RuntimeConfig,
    on_log: LogFn | None = None,
    on_progress: ProgressFn | None = None,
) -> BulkDefectResult:
    """Create Bug work items in ADO from reviewed defects."""
    if not defects:
        return BulkDefectResult()

    _log = on_log or (lambda _: None)
    _log(f"[INFO] Creating {len(defects)} Bug work item(s) in "
         f"{org}/{project}...")

    headers = _auth_headers(pat)
    parent_cache: dict[int, dict[str, Any]] = {}

    # Pre-fetch parent fields to avoid duplicate API calls under concurrency.
    unique_parents = {d.parent_id for d in defects if d.parent_id > 0}
    if unique_parents:
        async with httpx.AsyncClient(
            headers=headers,
            timeout=httpx.Timeout(cfg.http_timeout_sec),
            verify=cfg.build_ssl(),
        ) as prefetch_client:
            _prefetch_sem = asyncio.Semaphore(6)

            async def _bounded_fetch(_pid: int) -> None:
                async with _prefetch_sem:
                    try:
                        await _fetch_parent_fields(
                            prefetch_client, org, project, _pid,
                            parent_cache,
                        )
                    except Exception:
                        parent_cache.setdefault(_pid, {})

            await asyncio.gather(
                *(_bounded_fetch(pid) for pid in unique_parents)
            )

    results: list[DefectCreateResult] = []
    sem = asyncio.Semaphore(_CONCURRENCY)
    completed = 0

    async def _task(defect: Any) -> DefectCreateResult:
        nonlocal completed
        async with sem:
            async with httpx.AsyncClient(
                headers=headers,
                timeout=httpx.Timeout(cfg.http_timeout_sec),
                verify=cfg.build_ssl(),
            ) as client:
                r = await _create_one_bug(
                    client, org, project, defect, parent_cache, on_log
                )
                completed += 1
                if on_progress:
                    on_progress("upload", completed, len(defects))
                return r

    results = await asyncio.gather(*[_task(d) for d in defects])
    n_ok = sum(1 for r in results if r.ok)
    n_failed = sum(1 for r in results if not r.ok)
    _log(f"[SUCCESS] Upload complete: {n_ok} created, {n_failed} failed.")
    return BulkDefectResult(results=list(results), n_ok=n_ok, n_failed=n_failed)


