"""
ado_boards.py
Azure DevOps Boards integration for the 3-pane grid viewer.

Hierarchy fetched:
    org
     +- projects                (ado_api.list_projects)
         +- teams               GET _apis/projects/{project}/teams
             +- boards          GET {project}/{team}/_apis/work/boards
                 +- columns     GET .../boards/{id}/columns  (swim lanes)
                 +- work items  WIQL filtered to the board's work item
                                types, grouped client-side by
                                System.BoardColumn

The board's columns endpoint returns each column's stateMappings
(work-item-type -> state), which gives both the ordered swim-lane list
and the set of work item types to query. Items are grouped into lanes by
their System.BoardColumn value (the same field the old extractor read),
ordered by the board's column order.

All calls reuse ado_api.build_auth_header and
RuntimeConfig.build_ssl(), so the corporate-proxy TLS handling is shared.
"""

from __future__ import annotations

import asyncio
import base64
import re
import ssl as _ssl
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Final

import httpx

from ado.api import build_auth_header
from ado.extract import (
    download_attachment,
    extract_image_urls,
    fetch_comments,
    fetch_work_item,
    html_to_text,
)
from core.runtime_config import API_VER_CORE, API_VER_WI, API_VER_WIQL, RuntimeConfig

LogFn = Callable[[str], None]

_API_WORK: Final[str] = "7.1"
_BATCH_LIMIT: Final[int] = 200

# Fallback work item types if a board reports no state mappings.
_DEFAULT_WIT_TYPES: Final[tuple[str, ...]] = (
    "Epic", "Feature", "User Story", "Product Backlog Item",
    "Requirement", "Bug", "Issue",
)

_GRID_FIELDS: Final[list[str]] = [
    "System.Id",
    "System.Title",
    "System.WorkItemType",
    "System.State",
    "System.BoardColumn",
    "System.BoardLane",
    "System.AssignedTo",
    "System.Tags",
    "System.IterationPath",
]


# ---------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------
@dataclass(slots=True)
class Team:
    id: str
    name: str


@dataclass(slots=True)
class Board:
    id: str
    name: str
    team_id: str
    team_name: str

    @property
    def label(self) -> str:
        return f"{self.team_name} / {self.name}"


@dataclass(slots=True)
class BoardColumn:
    id: str
    name: str
    column_type: str
    state_mappings: dict[str, str] = field(default_factory=dict)


@dataclass(slots=True)
class WorkItemRow:
    wi_id: int
    title: str
    wi_type: str
    state: str
    board_column: str
    board_lane: str = ""
    assigned_to: str = ""
    tags: list[str] = field(default_factory=list)
    iteration_path: str = ""
    area_path: str = ""
    # Count of linked Test Case work items (ADO "Tested By" relations). Filled
    # by a relations pass after the grid fetch; 0 when none/unavailable.
    test_case_count: int = 0

    @property
    def iteration_leaf(self) -> str:
        if not self.iteration_path:
            return ""
        return self.iteration_path.replace("/", "\\").split("\\")[-1].strip()

    @property
    def component(self) -> str:
        if not self.area_path:
            return ""
        return self.area_path.replace("/", "\\").split("\\")[-1].strip()


@dataclass(slots=True)
class Attachment:
    name: str
    url: str
    size: int = 0
    comment: str = ""
    local_path: str = ""        # filled in lazily when downloaded


@dataclass(slots=True)
class WorkItemDetail:
    wi_id: int
    title: str
    wi_type: str
    state: str
    board_column: str = ""
    area_path: str = ""
    iteration_path: str = ""
    assigned_to: str = ""
    tags: list[str] = field(default_factory=list)
    description_text: str = ""
    acceptance_text: str = ""
    description_html: str = ""
    acceptance_html: str = ""
    comments: list[tuple[str, str, str]] = field(default_factory=list)
    comments_html: list[tuple[str, str, str]] = field(default_factory=list)
    attachments: list[Attachment] = field(default_factory=list)
    hyperlinks: list[tuple[str, str]] = field(default_factory=list)
    related: list[tuple[str, int, str]] = field(default_factory=list)
    inline_images: dict[str, str] = field(default_factory=dict)
    fields: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class BoardView:
    columns: list[BoardColumn] = field(default_factory=list)
    rows: list[WorkItemRow] = field(default_factory=list)

    def grouped(self) -> list[tuple[str, list[WorkItemRow]]]:
        return group_rows_by_column(self.rows, self.columns)


# ---------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------
def _client(cfg: RuntimeConfig) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        headers=build_auth_header(cfg.pat),
        timeout=httpx.Timeout(cfg.http_timeout_sec),
        verify=cfg.build_ssl(),
        http2=False,
    )


async def _get_json(
    client: httpx.AsyncClient, url: str, cfg: RuntimeConfig,
) -> dict[str, Any]:
    last_exc: Exception | None = None
    for attempt in range(cfg.retry_count):
        try:
            r = await client.get(url)
            if r.status_code in (429, 503):
                await asyncio.sleep(cfg.retry_backoff_sec * (attempt + 1))
                continue
            r.raise_for_status()
            return r.json()
        except (httpx.HTTPError, _ssl.SSLError) as e:
            last_exc = e
            await asyncio.sleep(cfg.retry_backoff_sec * (attempt + 1))
    raise RuntimeError(f"GET failed after retries: {url} ({last_exc!r})")


def _wiql_escape(value: str) -> str:
    # WIQL string literals escape a single quote by doubling it.
    return value.replace("'", "''")


# ---------------------------------------------------------------------
# Teams / boards / columns
# ---------------------------------------------------------------------
async def list_teams(
    org: str, project: str, cfg: RuntimeConfig,
) -> list[Team]:
    url = (
        f"https://dev.azure.com/{org}/_apis/projects/{project}/teams"
        f"?api-version={API_VER_CORE}&$top=200"
    )
    async with _client(cfg) as client:
        data = await _get_json(client, url, cfg)
    out: list[Team] = []
    for t in data.get("value", []) or []:
        tid = str(t.get("id", "")).strip()
        name = str(t.get("name", "")).strip()
        if tid and name:
            out.append(Team(id=tid, name=name))
    out.sort(key=lambda t: t.name.lower())
    return out


async def list_boards_for_team(
    org: str, project: str, team: Team, cfg: RuntimeConfig,
) -> list[Board]:
    url = (
        f"https://dev.azure.com/{org}/{project}/{team.id}"
        f"/_apis/work/boards?api-version={_API_WORK}"
    )
    async with _client(cfg) as client:
        data = await _get_json(client, url, cfg)
    out: list[Board] = []
    for b in data.get("value", []) or []:
        bid = str(b.get("id", "")).strip()
        name = str(b.get("name", "")).strip()
        if bid and name:
            out.append(Board(
                id=bid, name=name, team_id=team.id, team_name=team.name,
            ))
    return out


async def list_boards_for_project_async(
    org: str, project: str, cfg: RuntimeConfig,
    on_log: LogFn | None = None,
) -> list[Board]:
    """All boards across all teams in the project."""
    teams = await list_teams(org, project, cfg)
    if on_log:
        on_log(f"[INFO] {len(teams)} team(s) in {project}")
    sem = asyncio.Semaphore(6)

    async def _one(team: Team) -> list[Board]:
        async with sem:
            try:
                return await list_boards_for_team(org, project, team, cfg)
            except Exception as e:  # one team failing must not kill all
                if on_log:
                    on_log(f"[WARN] boards for team '{team.name}': {e!r}")
                return []

    results = await asyncio.gather(*[_one(t) for t in teams])
    boards: list[Board] = []
    for r in results:
        boards.extend(r)
    boards.sort(key=lambda b: (b.team_name.lower(), b.name.lower()))
    return boards


async def get_board_columns(
    org: str, project: str, board: Board, cfg: RuntimeConfig,
) -> list[BoardColumn]:
    url = (
        f"https://dev.azure.com/{org}/{project}/{board.team_id}"
        f"/_apis/work/boards/{board.id}/columns?api-version={_API_WORK}"
    )
    async with _client(cfg) as client:
        data = await _get_json(client, url, cfg)
    out: list[BoardColumn] = []
    for c in data.get("value", []) or []:
        out.append(BoardColumn(
            id=str(c.get("id", "")),
            name=str(c.get("name", "")).strip(),
            column_type=str(c.get("columnType", "")),
            state_mappings={
                str(k): str(v)
                for k, v in (c.get("stateMappings") or {}).items()
            },
        ))
    return out


def _wit_types_from_columns(columns: list[BoardColumn]) -> list[str]:
    types: set[str] = set()
    for c in columns:
        types.update(c.state_mappings.keys())
    if not types:
        return list(_DEFAULT_WIT_TYPES)
    return sorted(types)


# ---------------------------------------------------------------------
# Team area paths (to scope WIQL to the board's team)
# ---------------------------------------------------------------------
async def get_team_area_paths(
    org: str, project: str, team: Team, cfg: RuntimeConfig,
) -> list[str]:
    url = (
        f"https://dev.azure.com/{org}/{project}/{team.id}"
        f"/_apis/work/teamsettings/teamfieldvalues?api-version={_API_WORK}"
    )
    try:
        async with _client(cfg) as client:
            data = await _get_json(client, url, cfg)
    except Exception:
        return []
    out: list[str] = []
    for v in data.get("values", []) or []:
        val = str(v.get("value", "")).strip()
        if val:
            out.append(val)
    return out


# ---------------------------------------------------------------------
# WIQL + batch fetch
# ---------------------------------------------------------------------
def _build_wiql(
    project: str, wit_types: list[str], area_paths: list[str],
) -> str:
    proj = _wiql_escape(project)
    q = (
        "SELECT [System.Id] FROM WorkItems "
        f"WHERE [System.TeamProject] = '{proj}'"
    )
    if wit_types:
        joined = ", ".join(f"'{_wiql_escape(t)}'" for t in wit_types)
        q += f" AND [System.WorkItemType] IN ({joined})"
    if area_paths:
        clauses = " OR ".join(
            f"[System.AreaPath] UNDER '{_wiql_escape(a)}'" for a in area_paths
        )
        q += f" AND ({clauses})"
    q += " ORDER BY [System.ChangedDate] DESC"
    return q


async def _run_wiql(
    client: httpx.AsyncClient, org: str, project: str,
    query: str, cfg: RuntimeConfig,
) -> list[int]:
    url = (
        f"https://dev.azure.com/{org}/{project}/_apis/wit/wiql"
        f"?api-version={API_VER_WIQL}&$top=2000"
    )
    last_exc: Exception | None = None
    for attempt in range(cfg.retry_count):
        try:
            r = await client.post(
                url, json={"query": query},
                headers={"Content-Type": "application/json"},
            )
            if r.status_code in (429, 503):
                await asyncio.sleep(cfg.retry_backoff_sec * (attempt + 1))
                continue
            r.raise_for_status()
            payload = r.json()
            return [int(it["id"]) for it in payload.get("workItems", []) or []]
        except (httpx.HTTPError, _ssl.SSLError) as e:
            last_exc = e
            await asyncio.sleep(cfg.retry_backoff_sec * (attempt + 1))
    raise RuntimeError(f"WIQL failed after retries: {last_exc!r}")


async def _fetch_rows(
    client: httpx.AsyncClient, org: str, project: str,
    ids: list[int], cfg: RuntimeConfig,
) -> list[WorkItemRow]:
    rows: list[WorkItemRow] = []
    url = (
        f"https://dev.azure.com/{org}/{project}/_apis/wit/workitemsbatch"
        f"?api-version={API_VER_WI}"
    )
    for start in range(0, len(ids), _BATCH_LIMIT):
        batch_ids = ids[start:start + _BATCH_LIMIT]
        body = {"ids": batch_ids, "fields": _GRID_FIELDS}
        last_exc: Exception | None = None
        for attempt in range(cfg.retry_count):
            try:
                r = await client.post(
                    url, json=body,
                    headers={"Content-Type": "application/json"},
                )
                if r.status_code in (429, 503):
                    await asyncio.sleep(cfg.retry_backoff_sec * (attempt + 1))
                    continue
                r.raise_for_status()
                for wi in r.json().get("value", []) or []:
                    f = wi.get("fields", {}) or {}
                    assigned = f.get("System.AssignedTo") or {}
                    assigned_name = (
                        assigned.get("displayName", "")
                        if isinstance(assigned, dict) else str(assigned)
                    )
                    tags_raw = str(f.get("System.Tags", "") or "")
                    tags = [t.strip() for t in tags_raw.split(";") if t.strip()]
                    rows.append(WorkItemRow(
                        wi_id=int(wi.get("id", 0) or 0),
                        title=str(f.get("System.Title", "")).strip(),
                        wi_type=str(f.get("System.WorkItemType", "")).strip(),
                        state=str(f.get("System.State", "")).strip(),
                        board_column=str(f.get("System.BoardColumn", "")).strip(),
                        board_lane=str(f.get("System.BoardLane", "") or "").strip(),
                        assigned_to=assigned_name,
                        tags=tags,
                        iteration_path=str(
                            f.get("System.IterationPath", "") or ""
                        ).strip(),
                        area_path=str(
                            f.get("System.AreaPath", "") or ""
                        ).strip(),
                    ))
                break
            except (httpx.HTTPError, _ssl.SSLError) as e:
                last_exc = e
                await asyncio.sleep(cfg.retry_backoff_sec * (attempt + 1))
        else:
            raise RuntimeError(f"workitemsbatch failed: {last_exc!r}")
    return rows


async def load_board_view_async(
    org: str, project: str, board: Board, cfg: RuntimeConfig,
    scope_to_team_area: bool = True,
    on_log: LogFn | None = None,
) -> BoardView:
    """Fetch the board's columns and its work items grouped by swim lane."""
    columns = await get_board_columns(org, project, board, cfg)
    wit_types = _wit_types_from_columns(columns)
    area_paths: list[str] = []
    if scope_to_team_area:
        team = Team(id=board.team_id, name=board.team_name)
        area_paths = await get_team_area_paths(org, project, team, cfg)
    query = _build_wiql(project, wit_types, area_paths)
    if on_log:
        on_log(
            f"[INFO] Board '{board.label}': {len(columns)} columns, "
            f"types={wit_types}"
        )
    async with _client(cfg) as client:
        ids = await _run_wiql(client, org, project, query, cfg)
        if on_log:
            on_log(f"[INFO] WIQL matched {len(ids)} work item(s)")
        rows = await _fetch_rows(client, org, project, ids, cfg) if ids else []
        if rows:
            counts = await _fetch_test_case_counts(client, org, project, ids, cfg)
            _apply_test_case_counts(rows, counts)
    return BoardView(columns=columns, rows=rows)


# Batch callback type: receives (batch_rows, batch_index, total_batches)
BatchCallback = Callable[[list["WorkItemRow"], int, int], None]


async def _fetch_rows_streaming(
    client: httpx.AsyncClient, org: str, project: str,
    ids: list[int], cfg: RuntimeConfig,
    on_batch: BatchCallback | None = None,
    counts: dict[int, int] | None = None,
) -> list[WorkItemRow]:
    """Fetch work items in batches, emitting each batch via callback as it
    arrives. Enables progressive UI rendering without waiting for all data.
    Uses sys.intern() on repeated low-cardinality strings (type, state,
    column) to reduce memory via pointer dedup. ``counts`` (linked test-case
    counts keyed by work-item id) is stamped onto each row before emission so
    the streamed batches carry the "Generated Tests" count immediately."""
    from sys import intern as _intern

    counts = counts or {}

    rows: list[WorkItemRow] = []
    url = (
        f"https://dev.azure.com/{org}/{project}/_apis/wit/workitemsbatch"
        f"?api-version={API_VER_WI}"
    )
    total_batches = (len(ids) + _BATCH_LIMIT - 1) // _BATCH_LIMIT
    batch_idx = 0
    for start in range(0, len(ids), _BATCH_LIMIT):
        batch_ids = ids[start:start + _BATCH_LIMIT]
        body = {"ids": batch_ids, "fields": _GRID_FIELDS}
        last_exc: Exception | None = None
        for attempt in range(cfg.retry_count):
            try:
                r = await client.post(
                    url, json=body,
                    headers={"Content-Type": "application/json"},
                )
                if r.status_code in (429, 503):
                    await asyncio.sleep(cfg.retry_backoff_sec * (attempt + 1))
                    continue
                r.raise_for_status()
                batch_rows: list[WorkItemRow] = []
                for wi in r.json().get("value", []) or []:
                    f = wi.get("fields", {}) or {}
                    assigned = f.get("System.AssignedTo") or {}
                    assigned_name = (
                        assigned.get("displayName", "")
                        if isinstance(assigned, dict) else str(assigned)
                    )
                    tags_raw = str(f.get("System.Tags", "") or "")
                    tags = [t.strip() for t in tags_raw.split(";") if t.strip()]
                    _wid = int(wi.get("id", 0) or 0)
                    # intern low-cardinality fields: saves ~40 bytes per dupe
                    batch_rows.append(WorkItemRow(
                        wi_id=_wid,
                        test_case_count=counts.get(_wid, 0),
                        title=str(f.get("System.Title", "")).strip(),
                        wi_type=_intern(
                            str(f.get("System.WorkItemType", "")).strip()),
                        state=_intern(
                            str(f.get("System.State", "")).strip()),
                        board_column=_intern(
                            str(f.get("System.BoardColumn", "")).strip()),
                        board_lane=_intern(
                            str(f.get("System.BoardLane", "") or "").strip()),
                        assigned_to=assigned_name,
                        tags=tags,
                        iteration_path=_intern(
                            str(f.get("System.IterationPath", "") or ""
                                ).strip()),
                        area_path=_intern(
                            str(f.get("System.AreaPath", "") or "").strip()),
                    ))
                rows.extend(batch_rows)
                if on_batch and batch_rows:
                    on_batch(batch_rows, batch_idx, total_batches)
                batch_idx += 1
                break
            except (httpx.HTTPError, _ssl.SSLError) as e:
                last_exc = e
                await asyncio.sleep(cfg.retry_backoff_sec * (attempt + 1))
        else:
            raise RuntimeError(f"workitemsbatch failed: {last_exc!r}")
    return rows


_TESTED_BY_REL: Final[str] = "Microsoft.VSTS.Common.TestedBy-Forward"


def _count_tested_by(wi: dict[str, Any]) -> int:
    """Count "Tested By" relations on a single work-item payload. Matches by
    the reference name (TestedBy-Forward) OR the friendly attribute name ADO
    shows ("Tested By"), so it is robust to reference-name variations."""
    n = 0
    for rel in wi.get("relations", []) or []:
        rtype = str(rel.get("rel", ""))
        rname = str((rel.get("attributes") or {}).get("name", ""))
        if rtype == _TESTED_BY_REL or rname.strip().lower() == "tested by":
            n += 1
    return n


async def _fetch_counts_per_item(
    client: httpx.AsyncClient, org: str, project: str,
    ids: list[int], cfg: RuntimeConfig,
) -> dict[int, int]:
    """Fallback: fetch relations one work item at a time via the single-item
    GET (?$expand=relations), which is honored even when the batch endpoint
    ignores $expand. Bounded concurrency keeps this from hammering ADO."""
    counts: dict[int, int] = {}
    if not ids:
        return counts
    sem = asyncio.Semaphore(8)

    async def _one(wid: int) -> None:
        url = (
            f"https://dev.azure.com/{org}/{project}/_apis/wit/workitems/{wid}"
            f"?$expand=relations&api-version={API_VER_WI}"
        )
        async with sem:
            for attempt in range(cfg.retry_count):
                try:
                    r = await client.get(url)
                    if r.status_code in (429, 503):
                        await asyncio.sleep(
                            cfg.retry_backoff_sec * (attempt + 1)
                        )
                        continue
                    r.raise_for_status()
                    n = _count_tested_by(r.json())
                    if n:
                        counts[wid] = n
                    return
                except (httpx.HTTPError, _ssl.SSLError):
                    await asyncio.sleep(cfg.retry_backoff_sec * (attempt + 1))

    await asyncio.gather(*(_one(w) for w in ids))
    return counts


async def _fetch_test_case_counts(
    client: httpx.AsyncClient, org: str, project: str,
    ids: list[int], cfg: RuntimeConfig,
) -> dict[int, int]:
    """Count linked Test Case work items ("Tested By" relations) per work item.

    Primary path is a workitemsbatch pass with ``$expand: "Relations"`` (the
    batch body must use the CAPITALIZED enum name -- lowercase "relations" is
    silently ignored by ADO, which was why the "Generated Tests" column showed
    None. The batch API also forbids combining `fields` with `$expand`, so this
    is a dedicated pass with no fields filter). If a batch response comes back
    with no relations on ANY item (an org that ignores batch $expand), we fall
    back to a per-item GET which is always honored. Best-effort: returns {} on
    error so the board still loads."""
    counts: dict[int, int] = {}
    if not ids:
        return counts
    url = (
        f"https://dev.azure.com/{org}/{project}/_apis/wit/workitemsbatch"
        f"?api-version={API_VER_WI}"
    )
    for start in range(0, len(ids), _BATCH_LIMIT):
        batch_ids = ids[start:start + _BATCH_LIMIT]
        body = {"ids": batch_ids, "$expand": "Relations"}
        saw_relations = False
        got_response = False
        for attempt in range(cfg.retry_count):
            try:
                r = await client.post(
                    url, json=body,
                    headers={"Content-Type": "application/json"},
                )
                if r.status_code in (429, 503):
                    await asyncio.sleep(cfg.retry_backoff_sec * (attempt + 1))
                    continue
                r.raise_for_status()
                got_response = True
                for wi in r.json().get("value", []) or []:
                    wid = int(wi.get("id", 0) or 0)
                    if wi.get("relations"):
                        saw_relations = True
                    n = _count_tested_by(wi)
                    if wid and n:
                        counts[wid] = n
                break
            except (httpx.HTTPError, _ssl.SSLError):
                await asyncio.sleep(cfg.retry_backoff_sec * (attempt + 1))
        # If the batch responded but carried no relations at all, the org is
        # ignoring batch $expand -> fall back to the per-item GET for this
        # batch's ids (proven to return relations).
        if got_response and not saw_relations:
            counts.update(
                await _fetch_counts_per_item(
                    client, org, project, batch_ids, cfg
                )
            )
    return counts


def _apply_test_case_counts(
    rows: list["WorkItemRow"], counts: dict[int, int],
) -> None:
    if not counts:
        return
    for row in rows:
        row.test_case_count = counts.get(row.wi_id, 0)


async def load_board_view_streaming(
    org: str, project: str, board: "Board", cfg: RuntimeConfig,
    scope_to_team_area: bool = True,
    on_log: LogFn | None = None,
    on_batch: Callable[[list["WorkItemRow"], int, int], None] | None = None,
) -> "BoardView":
    """Streaming variant: emits work-item batches to the UI as they arrive.
    Returns the complete BoardView when done (same contract as
    load_board_view_async, plus incremental batch callbacks)."""
    columns = await get_board_columns(org, project, board, cfg)
    wit_types = _wit_types_from_columns(columns)
    area_paths: list[str] = []
    if scope_to_team_area:
        team = Team(id=board.team_id, name=board.team_name)
        area_paths = await get_team_area_paths(org, project, team, cfg)
    query = _build_wiql(project, wit_types, area_paths)
    if on_log:
        on_log(
            f"[INFO] Board '{board.label}': {len(columns)} columns, "
            f"types={wit_types}"
        )
    # Emit columns first so UI can render the skeleton
    if on_batch:
        on_batch([], -1, 0)  # -1 signals "columns ready, rows incoming"
    async with _client(cfg) as client:
        ids = await _run_wiql(client, org, project, query, cfg)
        if on_log:
            on_log(f"[INFO] WIQL matched {len(ids)} work item(s)")
        # Fetch linked test-case counts BEFORE streaming the rows so every
        # emitted batch already carries the count. Previously the counts were
        # computed after all batches were streamed, but the streamed rows were
        # never re-sent, so the UI's "Generated Tests" column stayed "None".
        counts = (
            await _fetch_test_case_counts(client, org, project, ids, cfg)
            if ids else {}
        )
        if on_log and counts:
            on_log(
                f"[INFO] Linked test cases found on {len(counts)} work item(s)"
            )
        rows = (
            await _fetch_rows_streaming(
                client, org, project, ids, cfg, on_batch=on_batch, counts=counts
            )
            if ids else []
        )
    return BoardView(columns=columns, rows=rows)


# ---------------------------------------------------------------------
# Grouping
# ---------------------------------------------------------------------
_NO_COLUMN_LABEL: Final[str] = "(no board column)"


def group_rows_by_column(
    rows: list[WorkItemRow], columns: list[BoardColumn],
) -> list[tuple[str, list[WorkItemRow]]]:
    """Group rows into lanes ordered by the board's column order. Rows
    whose BoardColumn is empty or not a known column fall into a trailing
    '(no board column)' lane. Empty lanes are still shown so the board
    structure is visible."""
    order = [c.name for c in columns]
    buckets: dict[str, list[WorkItemRow]] = {name: [] for name in order}
    extra: list[WorkItemRow] = []
    for r in sorted(rows, key=lambda x: x.wi_id):
        if r.board_column and r.board_column in buckets:
            buckets[r.board_column].append(r)
        else:
            extra.append(r)
    out: list[tuple[str, list[WorkItemRow]]] = [
        (name, buckets[name]) for name in order
    ]
    if extra:
        out.append((_NO_COLUMN_LABEL, extra))
    return out


_NO_ITERATION_LABEL: Final[str] = "(no iteration)"


@dataclass(slots=True)
class KanbanModel:
    """A column-by-iteration board model. `columns` is the ordered set of
    swim-lane column names (the board's columns, plus a trailing
    '(no board column)' bucket when needed). `lanes` is the ordered list
    of (iteration_label, {column_name: rows}) horizontal bands."""
    columns: list[str] = field(default_factory=list)
    lanes: list[tuple[str, dict[str, list[WorkItemRow]]]] = field(
        default_factory=list
    )

    @property
    def total(self) -> int:
        return sum(len(rows) for _label, cells in self.lanes
                   for rows in cells.values())


def build_kanban_model(
    rows: list[WorkItemRow], columns: list[BoardColumn],
) -> KanbanModel:
    """Arrange rows into a Kanban grid: board columns across, iterations
    (sprints) as horizontal bands. Iterations are ordered by iteration
    path; items without an iteration go to a trailing band. Within a cell
    rows are ordered by work item id."""
    col_order = [c.name for c in columns]
    known = frozenset(col_order)
    need_extra = any(
        (not r.board_column) or (r.board_column not in known) for r in rows
    )
    columns_out = list(col_order)
    if need_extra:
        columns_out.append(_NO_COLUMN_LABEL)

    # Order iterations: by path, with the empty iteration last.
    iters: list[str] = []
    seen: set[str] = set()
    for r in sorted(rows, key=lambda x: (x.iteration_path == "",
                                         x.iteration_path.lower(), x.wi_id)):
        key = r.iteration_path or _NO_ITERATION_LABEL
        if key not in seen:
            seen.add(key)
            iters.append(key)

    lanes: list[tuple[str, dict[str, list[WorkItemRow]]]] = []
    for it in iters:
        cells: dict[str, list[WorkItemRow]] = {c: [] for c in columns_out}
        for r in sorted(rows, key=lambda x: x.wi_id):
            r_it = r.iteration_path or _NO_ITERATION_LABEL
            if r_it != it:
                continue
            col = (r.board_column if r.board_column in known
                   else _NO_COLUMN_LABEL)
            cells.setdefault(col, []).append(r)
        label = (
            it if it == _NO_ITERATION_LABEL
            else it.replace("/", "\\").split("\\")[-1].strip() or it
        )
        lanes.append((label, cells))
    return KanbanModel(columns=columns_out, lanes=lanes)


# ---------------------------------------------------------------------
# Work item detail
# ---------------------------------------------------------------------
def _detail_cfg(org: str, project: str, cfg: RuntimeConfig) -> RuntimeConfig:
    """Clone cfg with org/project set so the reused ado_extract fetchers
    target the right project."""
    clone = RuntimeConfig.from_env_defaults()
    clone.pat = cfg.pat
    clone.organization = org
    clone.project = project
    clone.tls_mode = cfg.tls_mode
    clone.tls_ca_bundle = cfg.tls_ca_bundle
    clone.http_timeout_sec = cfg.http_timeout_sec
    clone.retry_count = cfg.retry_count
    clone.retry_backoff_sec = cfg.retry_backoff_sec
    return clone


_INLINE_DATA_URI_RE: Final[re.Pattern[str]] = re.compile(
    r"^data:image/([a-z0-9.+-]+);base64,(.+)$", re.IGNORECASE | re.DOTALL
)


def _safe_name(name: str) -> str:
    bad = '<>:"/\\|?*'
    cleaned = "".join("_" if c in bad else c for c in (name or "")).strip(". ")
    return (cleaned or "file")[:180]


def _parse_relations(wi: dict[str, Any]) -> tuple[
    list[Attachment], list[tuple[str, str]], list[tuple[str, int, str]]
]:
    """Split a work item's relations into attachments, hyperlinks, and
    related work-item links."""
    attachments: list[Attachment] = []
    hyperlinks: list[tuple[str, str]] = []
    related: list[tuple[str, int, str]] = []
    for rel in wi.get("relations", []) or []:
        rtype = str(rel.get("rel", ""))
        url = str(rel.get("url", ""))
        attrs = rel.get("attributes") or {}
        comment = str(attrs.get("comment", "") or "")
        if rtype == "AttachedFile" and url:
            try:
                size = int(attrs.get("resourceSize", 0) or 0)
            except (TypeError, ValueError):
                size = 0
            attachments.append(Attachment(
                name=_safe_name(str(attrs.get("name", "attachment"))),
                url=url, size=size, comment=comment,
            ))
        elif rtype == "Hyperlink" and url:
            hyperlinks.append((url, comment))
        elif url and "/_apis/wit/workitems/" in url.lower():
            m = re.search(r"/workitems/(\d+)", url, re.IGNORECASE)
            wid = int(m.group(1)) if m else 0
            name = str(attrs.get("name", rtype)) or rtype
            related.append((name, wid, url))
    return attachments, hyperlinks, related


def _to_detail(wi: dict[str, Any], comments: list[dict[str, Any]]) -> WorkItemDetail:
    f = wi.get("fields", {}) or {}
    assigned = f.get("System.AssignedTo") or {}
    assigned_name = (
        assigned.get("displayName", "") if isinstance(assigned, dict)
        else str(assigned)
    )
    tags_raw = str(f.get("System.Tags", "") or "")
    tags = [t.strip() for t in tags_raw.split(";") if t.strip()]
    desc_html = str(f.get("System.Description", "") or "")
    ac_html = str(f.get("Microsoft.VSTS.Common.AcceptanceCriteria", "") or "")
    comment_tuples: list[tuple[str, str, str]] = []
    comment_html: list[tuple[str, str, str]] = []
    for c in comments:
        author = (c.get("createdBy") or {}).get("displayName", "unknown")
        when = str(c.get("createdDate", ""))
        c_html = c.get("text", "") or ""
        comment_tuples.append((when, author, html_to_text(c_html)))
        comment_html.append((when, author, c_html))
    attachments, hyperlinks, related = _parse_relations(wi)
    return WorkItemDetail(
        wi_id=int(wi.get("id", 0) or 0),
        title=str(f.get("System.Title", "")).strip(),
        wi_type=str(f.get("System.WorkItemType", "")).strip(),
        state=str(f.get("System.State", "")).strip(),
        board_column=str(f.get("System.BoardColumn", "")).strip(),
        area_path=str(f.get("System.AreaPath", "")).strip(),
        iteration_path=str(f.get("System.IterationPath", "")).strip(),
        assigned_to=assigned_name,
        tags=tags,
        description_text=html_to_text(desc_html),
        acceptance_text=html_to_text(ac_html),
        description_html=desc_html,
        acceptance_html=ac_html,
        comments=comment_tuples,
        comments_html=comment_html,
        attachments=attachments,
        hyperlinks=hyperlinks,
        related=related,
        fields=f,
    )


async def _hydrate_inline_images(
    client: httpx.AsyncClient, dcfg: RuntimeConfig,
    detail: WorkItemDetail, media_dir: Path,
) -> None:
    """Download inline <img> sources (http(s) and data URIs) into media_dir
    and record url -> local path on the detail. Best-effort; a failed image
    is simply left un-rewritten."""
    urls: list[str] = []
    for html in (detail.description_html, detail.acceptance_html,
                 *[h for _, _, h in detail.comments_html]):
        urls.extend(extract_image_urls(html))
    seen: set[str] = set()
    unique = [u for u in urls if not (u in seen or seen.add(u))]
    if not unique:
        return
    media_dir.mkdir(parents=True, exist_ok=True)
    for i, url in enumerate(unique):
        try:
            m = _INLINE_DATA_URI_RE.match(url)
            if m:
                ext = m.group(1).lower().replace("+xml", "")
                if ext not in ("png", "jpg", "jpeg", "gif", "bmp", "webp",
                               "tiff", "svg"):
                    ext = "png"
                dest = media_dir / f"inline_{detail.wi_id}_{i:03d}.{ext}"
                dest.write_bytes(base64.b64decode(m.group(2)))
                detail.inline_images[url] = str(dest)
                continue
            base = url.split("?")[0].split("/")[-1] or "img"
            dest = media_dir / f"inline_{detail.wi_id}_{i:03d}_{_safe_name(base)}"
            if not dest.suffix:
                dest = dest.with_suffix(".png")
            await download_attachment(client, dcfg, url, dest)
            detail.inline_images[url] = str(dest)
        except Exception:  # noqa: BLE001
            continue


async def fetch_work_item_detail_async(
    org: str, project: str, wi_id: int, cfg: RuntimeConfig,
    media_dir: Path | None = None,
) -> WorkItemDetail:
    dcfg = _detail_cfg(org, project, cfg)
    async with _client(cfg) as client:
        wi, comments = await asyncio.gather(
            fetch_work_item(client, dcfg, wi_id),
            fetch_comments(client, dcfg, wi_id),
        )
        detail = _to_detail(wi, comments)
        if media_dir is not None:
            try:
                await _hydrate_inline_images(client, dcfg, detail, media_dir)
            except Exception:  # noqa: BLE001
                pass
    return detail


async def download_attachment_async(
    org: str, project: str, url: str, dest: Path, cfg: RuntimeConfig,
) -> str:
    """Download an attachment (or any auth'd ADO blob) to dest. Returns the
    local path string."""
    dcfg = _detail_cfg(org, project, cfg)
    dest.parent.mkdir(parents=True, exist_ok=True)
    async with _client(cfg) as client:
        await download_attachment(client, dcfg, url, dest)
    return str(dest)


def download_attachment_sync(
    org: str, project: str, url: str, dest: Path, cfg: RuntimeConfig,
) -> str:
    return asyncio.run(download_attachment_async(org, project, url, dest, cfg))


async def tag_work_item(
    org: str,
    project: str,
    work_item_id: int,
    tag: str,
    pat: str,
    *,
    ssl_ctx: Any = None,
    on_log: Callable[[str], None] | None = None,
) -> bool:
    """Add a tag to an ADO work item. Returns True on success."""
    from core.http_retry import request_with_retry

    tag = tag.strip()
    if not tag:
        if on_log:
            on_log("[WARN] tag_work_item called with empty tag; skipping")
        return False

    headers = build_auth_header(pat)
    verify: bool | _ssl.SSLContext = ssl_ctx if ssl_ctx is not None else True

    base = f"https://dev.azure.com/{org}/{project}/_apis/wit/workitems/{work_item_id}"
    get_url = f"{base}?api-version={API_VER_WI}&fields=System.Tags"

    try:
        async with httpx.AsyncClient(
            headers=headers, timeout=httpx.Timeout(30.0),
            verify=verify, http2=False,
        ) as client:
            # 1. Read existing tags
            r = await request_with_retry(client, "GET", get_url)
            if r.status_code != 200:
                if on_log:
                    on_log(
                        f"[ERROR] GET work item {work_item_id} "
                        f"returned {r.status_code}"
                    )
                return False
            fields = r.json().get("fields", {}) or {}
            tags_raw = str(fields.get("System.Tags", "") or "")
            existing = [t.strip() for t in tags_raw.split(";") if t.strip()]

            # 2. Check if tag already present (case-insensitive)
            if any(t.lower() == tag.lower() for t in existing):
                if on_log:
                    on_log(
                        f"[INFO] Tag '{tag}' already on WI {work_item_id}; "
                        f"no update needed"
                    )
                return True

            # 3. Append and PATCH
            existing.append(tag)
            new_tags = "; ".join(existing)
            patch_url = f"{base}?api-version={API_VER_WI}"
            patch_body = [
                {
                    "op": "replace",
                    "path": "/fields/System.Tags",
                    "value": new_tags,
                }
            ]
            r = await request_with_retry(
                client, "PATCH", patch_url,
                json=patch_body,
                headers={"Content-Type": "application/json-patch+json"},
            )
            if r.status_code in (200, 204):
                if on_log:
                    on_log(
                        f"[SUCCESS] Tagged WI {work_item_id} with '{tag}'"
                    )
                return True
            if on_log:
                on_log(
                    f"[ERROR] PATCH work item {work_item_id} "
                    f"returned {r.status_code}: {r.text[:200]}"
                )
            return False
    except Exception as exc:
        if on_log:
            on_log(f"[ERROR] tag_work_item failed: {exc!r}")
        return False


async def fetch_ado_blob_async(
    org: str, project: str, url: str, cfg: RuntimeConfig,
) -> tuple[bytes, str]:
    """Fetch an authenticated ADO blob (inline image or attachment) fully into
    memory using the stored PAT. Returns (data, content_type). Used by the web
    proxy so the browser can render/download media it cannot authenticate to."""
    dcfg = _detail_cfg(org, project, cfg)
    async with _client(cfg) as client:
        r = await client.get(
            url, timeout=httpx.Timeout(dcfg.download_timeout_sec)
        )
        r.raise_for_status()
        ctype = r.headers.get("Content-Type", "application/octet-stream")
        return r.content, ctype


async def fetch_details_async(
    org: str, project: str, wi_ids: list[int], cfg: RuntimeConfig,
    on_progress: Callable[[int, int], None] | None = None,
    on_log: LogFn | None = None,
) -> list[WorkItemDetail]:
    """Fetch full detail for many work items concurrently (used to build
    the generation dump). Order of the returned list follows wi_ids."""
    dcfg = _detail_cfg(org, project, cfg)
    sem = asyncio.Semaphore(max(1, cfg.concurrency))
    done = {"n": 0}
    total = len(wi_ids)

    async def _one(client: httpx.AsyncClient, wid: int) -> WorkItemDetail | None:
        async with sem:
            try:
                wi, comments = await asyncio.gather(
                    fetch_work_item(client, dcfg, wid),
                    fetch_comments(client, dcfg, wid),
                )
                return _to_detail(wi, comments)
            except Exception as e:
                if on_log:
                    on_log(f"[ERROR] WI {wid} detail fetch failed: {e!r}")
                return None
            finally:
                done["n"] += 1
                if on_progress:
                    on_progress(done["n"], total)

    async with _client(cfg) as client:
        results = await asyncio.gather(*[_one(client, w) for w in wi_ids])
    by_id = {d.wi_id: d for d in results if d is not None}
    return [by_id[w] for w in wi_ids if w in by_id]


# ---------------------------------------------------------------------
# Sync wrappers (for callers not already on an event loop)
# ---------------------------------------------------------------------
def load_boards_for_project(org: str, project: str, cfg: RuntimeConfig,
                            on_log: LogFn | None = None) -> list[Board]:
    return asyncio.run(
        list_boards_for_project_async(org, project, cfg, on_log)
    )


def load_board_view(org: str, project: str, board: Board, cfg: RuntimeConfig,
                    on_log: LogFn | None = None) -> BoardView:
    return asyncio.run(
        load_board_view_async(org, project, board, cfg, on_log=on_log)
    )


def fetch_work_item_detail(org: str, project: str, wi_id: int,
                           cfg: RuntimeConfig) -> WorkItemDetail:
    return asyncio.run(fetch_work_item_detail_async(org, project, wi_id, cfg))
