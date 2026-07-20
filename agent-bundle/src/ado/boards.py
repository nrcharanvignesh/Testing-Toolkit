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
import logging
import re
import ssl as _ssl
import time
from dataclasses import dataclass, field, replace as _dc_replace
from pathlib import Path
from typing import Any, Callable, Final

_log = logging.getLogger(__name__)

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

# Board list cache: boards rarely change, so cache for 5 min to avoid
# repeated N+1 API roundtrips on every project switch / UI reload.
_BOARD_CACHE_TTL: Final[float] = 300.0
_board_cache: dict[str, tuple[float, list[Any]]] = {}
_board_cache_lock: asyncio.Lock | None = None


def _get_board_cache_lock() -> asyncio.Lock:
    global _board_cache_lock
    if _board_cache_lock is None:
        _board_cache_lock = asyncio.Lock()
    return _board_cache_lock

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
    "System.AreaPath",
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
    # True when WIQL returned 0 items but the board's columns prove it is
    # configured for specific WIT types (i.e., items should exist). This
    # signals a probable ADO transient failure rather than a true empty board.
    possibly_degraded: bool = False

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


async def _retry_request(
    client: httpx.AsyncClient, method: str, url: str,
    cfg: RuntimeConfig, *, body: dict[str, Any] | None = None,
    label: str = "",
) -> httpx.Response:
    """Single retry loop for all ADO HTTP calls. Returns the successful response."""
    last_exc: Exception | None = None
    kwargs: dict[str, Any] = {}
    if body is not None:
        kwargs["json"] = body
        kwargs["headers"] = {"Content-Type": "application/json"}
    for attempt in range(cfg.retry_count):
        try:
            r = await (client.post(url, **kwargs) if method == "POST"
                       else client.get(url))
            if r.status_code in (429, 503):
                await asyncio.sleep(cfg.retry_backoff_sec * (attempt + 1))
                continue
            r.raise_for_status()
            return r
        except (httpx.HTTPError, _ssl.SSLError) as e:
            last_exc = e
            await asyncio.sleep(cfg.retry_backoff_sec * (attempt + 1))
    raise RuntimeError(
        f"{method} failed after retries: {label or url} ({last_exc!r})"
    )


async def _get_json(
    client: httpx.AsyncClient, url: str, cfg: RuntimeConfig,
) -> dict[str, Any]:
    r = await _retry_request(client, "GET", url, cfg)
    return r.json()


async def _post_json(
    client: httpx.AsyncClient, url: str, body: dict[str, Any],
    cfg: RuntimeConfig, *, label: str = "",
) -> dict[str, Any]:
    r = await _retry_request(client, "POST", url, cfg, body=body, label=label)
    return r.json()


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
    return _parse_teams(data)


async def _list_teams_with_client(
    org: str, project: str, cfg: RuntimeConfig,
    client: httpx.AsyncClient,
) -> list[Team]:
    """list_teams variant that reuses a shared client."""
    url = (
        f"https://dev.azure.com/{org}/_apis/projects/{project}/teams"
        f"?api-version={API_VER_CORE}&$top=200"
    )
    data = await _get_json(client, url, cfg)
    return _parse_teams(data)


def _parse_teams(data: dict[str, Any]) -> list[Team]:
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
    shared_client: httpx.AsyncClient | None = None,
) -> list[Board]:
    url = (
        f"https://dev.azure.com/{org}/{project}/{team.id}"
        f"/_apis/work/boards?api-version={_API_WORK}"
    )
    if shared_client is not None:
        data = await _get_json(shared_client, url, cfg)
    else:
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
    """All boards across all teams in the project. Cached for 5 min."""
    cache_key = f"{org}/{project}"
    now = time.monotonic()

    # Check cache first (hot path -- no HTTP calls at all)
    lock = _get_board_cache_lock()
    async with lock:
        entry = _board_cache.get(cache_key)
        if entry is not None:
            ts, cached_boards = entry
            if (now - ts) < _BOARD_CACHE_TTL:
                if on_log:
                    on_log(f"[INFO] {len(cached_boards)} board(s) from cache")
                return list(cached_boards)

    # Cache miss: fetch with a shared client (connection reuse across teams)
    async with _client(cfg) as shared:
        teams = await _list_teams_with_client(org, project, cfg, shared)
        if on_log:
            on_log(f"[INFO] {len(teams)} team(s) in {project}")
        sem = asyncio.Semaphore(6)

        async def _one(team: Team) -> list[Board]:
            async with sem:
                try:
                    return await list_boards_for_team(
                        org, project, team, cfg, shared_client=shared,
                    )
                except Exception as e:
                    if on_log:
                        on_log(f"[WARN] boards for team '{team.name}': {e!r}")
                    return []

        results = await asyncio.gather(*[_one(t) for t in teams])

    boards: list[Board] = []
    for r in results:
        boards.extend(r)
    boards.sort(key=lambda b: (b.team_name.lower(), b.name.lower()))

    # Store in cache
    async with lock:
        _board_cache[cache_key] = (time.monotonic(), list(boards))

    return boards


def invalidate_board_cache(org: str = "", project: str = "") -> None:
    """Clear board cache. No args = clear all; with args = clear one project."""
    if org and project:
        _board_cache.pop(f"{org}/{project}", None)
    else:
        _board_cache.clear()


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
    on_log: LogFn | None = None,
) -> list[str]:
    url = (
        f"https://dev.azure.com/{org}/{project}/{team.id}"
        f"/_apis/work/teamsettings/teamfieldvalues?api-version={_API_WORK}"
    )
    try:
        async with _client(cfg) as client:
            data = await _get_json(client, url, cfg)
    except Exception as e:
        _log.warning(
            "get_team_area_paths failed for team %r in %s/%s: %s",
            team.name, org, project, e,
        )
        if on_log:
            on_log(
                f"[WARN] Area-path fetch failed for team '{team.name}': {e!r} "
                f"-- query will run WITHOUT area-path filter (broader scope)"
            )
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


_WIQL_TOP: Final[int] = 2000


async def _run_wiql(
    client: httpx.AsyncClient, org: str, project: str,
    query: str, cfg: RuntimeConfig,
    on_log: LogFn | None = None,
) -> list[int]:
    url = (
        f"https://dev.azure.com/{org}/{project}/_apis/wit/wiql"
        f"?api-version={API_VER_WIQL}&$top={_WIQL_TOP}"
    )
    data = await _post_json(client, url, {"query": query}, cfg, label="WIQL")
    ids = [int(it["id"]) for it in data.get("workItems", []) or []]
    if not ids:
        # Diagnostic: log the raw response keys + truncated query so a future
        # run can confirm whether ADO returned a well-formed empty result vs
        # a degraded/malformed response.
        _log.warning(
            "WIQL returned 0 items for project=%r -- response keys=%r, "
            "query prefix=%.120r",
            project, list(data.keys()), query,
        )
    if len(ids) == _WIQL_TOP:
        _log.warning(
            "WIQL returned exactly %d items for project=%r -- results may be "
            "truncated (ADO $top limit hit)", _WIQL_TOP, project,
        )
        if on_log:
            on_log(
                f"[WARN] WIQL hit ${_WIQL_TOP} cap -- some work items may be "
                f"missing from this board"
            )
    return ids


def _parse_row(
    wi: dict[str, Any], counts: dict[int, int] | None = None,
    intern_fn: Callable[[str], str] | None = None,
) -> WorkItemRow:
    """Parse a single ADO work-item payload into a WorkItemRow."""
    _i = intern_fn or (lambda s: s)
    f = wi.get("fields", {}) or {}
    assigned = f.get("System.AssignedTo") or {}
    assigned_name = (
        assigned.get("displayName", "")
        if isinstance(assigned, dict) else str(assigned)
    )
    tags_raw = str(f.get("System.Tags", "") or "")
    wid = int(wi.get("id", 0) or 0)
    return WorkItemRow(
        wi_id=wid,
        test_case_count=(counts or {}).get(wid, 0),
        title=str(f.get("System.Title", "")).strip(),
        wi_type=_i(str(f.get("System.WorkItemType", "")).strip()),
        state=_i(str(f.get("System.State", "")).strip()),
        board_column=_i(str(f.get("System.BoardColumn", "")).strip()),
        board_lane=_i(str(f.get("System.BoardLane", "") or "").strip()),
        assigned_to=assigned_name,
        tags=[t.strip() for t in tags_raw.split(";") if t.strip()],
        iteration_path=_i(str(f.get("System.IterationPath", "") or "").strip()),
        area_path=_i(str(f.get("System.AreaPath", "") or "").strip()),
    )


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
        data = await _post_json(
            client, url, {"ids": batch_ids, "fields": _GRID_FIELDS},
            cfg, label="workitemsbatch",
        )
        rows.extend(_parse_row(wi) for wi in data.get("value", []) or [])
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
        area_paths = await get_team_area_paths(
            org, project, team, cfg, on_log=on_log,
        )
    query = _build_wiql(project, wit_types, area_paths)
    if on_log:
        on_log(
            f"[INFO] Board '{board.label}': {len(columns)} columns, "
            f"types={wit_types}, area_scope={'ON' if scope_to_team_area else 'OFF'}"
            f"{', paths=' + repr(area_paths) if area_paths else ''}"
        )
    # A board whose columns carry state mappings is actively configured for
    # specific work item types -- a 0-result WIQL is suspicious under load.
    board_expects_items = wit_types != list(_DEFAULT_WIT_TYPES)
    possibly_degraded = False
    async with _client(cfg) as client:
        ids = await _run_wiql(client, org, project, query, cfg, on_log=on_log)
        # Retry once after a brief pause when WIQL returns 0 items for a board
        # whose column configuration proves items should exist. ADO returns
        # HTTP 200 with empty results under sustained load (rate-limit soft
        # degradation) -- indistinguishable from a true empty board without
        # this heuristic.
        if not ids and board_expects_items:
            if on_log:
                on_log(
                    f"[WARN] 0 items from WIQL for configured board "
                    f"'{board.label}' -- retrying after 4s (suspected ADO "
                    f"transient degradation)"
                )
            await asyncio.sleep(4.0)
            ids = await _run_wiql(client, org, project, query, cfg, on_log=on_log)
            if not ids:
                possibly_degraded = True
                if on_log:
                    on_log(
                        f"[ERROR] WIQL still returned 0 items for board "
                        f"'{board.label}' after retry -- marking as possibly "
                        f"degraded (ADO may be throttling this query)"
                    )
        if on_log:
            on_log(f"[INFO] WIQL matched {len(ids)} work item(s)")
        if not ids and not possibly_degraded and on_log:
            on_log(
                f"[WARN] 0 work items for board '{board.label}' -- "
                f"wit_types={wit_types}, area_paths={area_paths or '(none)'}, "
                f"scope_to_team_area={scope_to_team_area}"
            )
        rows = await _fetch_rows(client, org, project, ids, cfg) if ids else []
        if rows:
            counts = await _fetch_test_case_counts(client, org, project, ids, cfg)
            _apply_test_case_counts(rows, counts)
    return BoardView(columns=columns, rows=rows, possibly_degraded=possibly_degraded)


# Batch callback type: receives (batch_rows, batch_index, total_batches)
BatchCallback = Callable[[list["WorkItemRow"], int, int], None]


async def _fetch_rows_streaming(
    client: httpx.AsyncClient, org: str, project: str,
    ids: list[int], cfg: RuntimeConfig,
    on_batch: BatchCallback | None = None,
    counts: dict[int, int] | None = None,
) -> list[WorkItemRow]:
    """Fetch work items in batches, emitting each batch via callback as it
    arrives. Uses sys.intern() on repeated low-cardinality strings to reduce
    memory via pointer dedup."""
    from sys import intern as _intern

    counts = counts or {}
    rows: list[WorkItemRow] = []
    url = (
        f"https://dev.azure.com/{org}/{project}/_apis/wit/workitemsbatch"
        f"?api-version={API_VER_WI}"
    )
    total_batches = (len(ids) + _BATCH_LIMIT - 1) // _BATCH_LIMIT
    for batch_idx, start in enumerate(range(0, len(ids), _BATCH_LIMIT)):
        batch_ids = ids[start:start + _BATCH_LIMIT]
        data = await _post_json(
            client, url, {"ids": batch_ids, "fields": _GRID_FIELDS},
            cfg, label="workitemsbatch",
        )
        batch_rows = [
            _parse_row(wi, counts, _intern)
            for wi in data.get("value", []) or []
        ]
        rows.extend(batch_rows)
        if on_batch and batch_rows:
            on_batch(batch_rows, batch_idx, total_batches)
    return rows


# --------------------- versatile test-case discovery ---------------------
# A relation points AT a test case when its reference name OR friendly
# attribute name matches one of these hints (alnum-stripped, lowercased).
# Covers "Tested By" (TestedBy-Forward), "Tests", shared-step "TestCase..",
# and custom link types teams rename for test coverage.
_TEST_REL_HINTS: Final[tuple[str, ...]] = ("testedby", "tests", "testcase")
# Link relations whose TARGETS we type-check as possible test cases -- many
# teams model a test as a child/related work item instead of using a
# TestedBy relation.
_LINK_REL_HINTS: Final[tuple[str, ...]] = (
    "related", "dependency",
)
# A linked work item is a test case when its normalized type name matches one
# of these tokens exactly (case-insensitive, after stripping non-alnum).
# "testcase" matches "Test Case"; "test" alone is too broad (catches "Protest",
# "Contest", "Testing Task", etc.) so we require the full compound or exact word.
_TEST_TYPE_TOKENS: Final[tuple[str, ...]] = ("testcase",)


def _norm(s: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", str(s or "").lower())


def _is_test_relation(rel: dict[str, Any]) -> bool:
    """True when a relation directly denotes test coverage (Tested By, Tests,
    or a custom/renamed test link type)."""
    ref = _norm(rel.get("rel", ""))
    name = _norm((rel.get("attributes") or {}).get("name", ""))
    return any(h in ref or h in name for h in _TEST_REL_HINTS)


def _is_link_relation(rel: dict[str, Any]) -> bool:
    """True for parent/child/related/dependency links whose target might be a
    test-type work item worth type-checking."""
    ref = _norm(rel.get("rel", ""))
    return any(h in ref for h in _LINK_REL_HINTS)


def _rel_target_id(rel: dict[str, Any]) -> int:
    m = re.search(r"/workitems/(\d+)", str(rel.get("url", "")), re.IGNORECASE)
    return int(m.group(1)) if m else 0


def _count_tested_by(wi: dict[str, Any]) -> int:
    """Count test-case relations on a single work-item payload. Matches the
    reference name (e.g. TestedBy-Forward) OR the friendly attribute name ADO
    shows ("Tested By"/"Tests"), so it is robust to reference-name variations
    and to teams that rename their test link types."""
    return sum(
        1 for rel in wi.get("relations", []) or [] if _is_test_relation(rel)
    )


async def _fetch_relations_per_item(
    client: httpx.AsyncClient, org: str, project: str,
    ids: list[int], cfg: RuntimeConfig,
) -> dict[int, list[dict[str, Any]]]:
    """Fetch relations one work item at a time via the single-item GET
    (?$expand=relations). Bounded concurrency keeps this from hammering ADO."""
    out: dict[int, list[dict[str, Any]]] = {}
    if not ids:
        return out
    sem = asyncio.Semaphore(8)

    async def _one(wid: int) -> None:
        url = (
            f"https://dev.azure.com/{org}/{project}/_apis/wit/workitems/{wid}"
            f"?$expand=relations&api-version={API_VER_WI}"
        )
        async with sem:
            try:
                data = await _get_json(client, url, cfg)
                out[wid] = data.get("relations") or []
            except RuntimeError as e:
                _log.debug("_fetch_relations_per_item WI %d failed: %s", wid, e)

    await asyncio.gather(*(_one(w) for w in ids))
    return out


async def _fetch_relations(
    client: httpx.AsyncClient, org: str, project: str,
    ids: list[int], cfg: RuntimeConfig,
) -> dict[int, list[dict[str, Any]]]:
    """Return {work_item_id: relations[]}. Primary path uses workitemsbatch
    with $expand: "Relations" (CAPITALIZED -- lowercase is ignored by ADO).
    Falls back to per-item GET when batch returns no relations. Best-effort."""
    out: dict[int, list[dict[str, Any]]] = {}
    if not ids:
        return out
    url = (
        f"https://dev.azure.com/{org}/{project}/_apis/wit/workitemsbatch"
        f"?api-version={API_VER_WI}"
    )
    for start in range(0, len(ids), _BATCH_LIMIT):
        batch_ids = ids[start:start + _BATCH_LIMIT]
        batch_returned_field = False
        try:
            data = await _post_json(
                client, url, {"ids": batch_ids, "$expand": "Relations"},
                cfg, label="relations-batch",
            )
            for wi in data.get("value", []) or []:
                wid = int(wi.get("id", 0) or 0)
                # Distinguish "field present but empty" from "field absent"
                if "relations" in wi:
                    batch_returned_field = True
                rels = wi.get("relations") or []
                if wid:
                    out[wid] = rels
        except RuntimeError as e:
            _log.debug("_fetch_relations batch %d-%d failed: %s",
                       batch_ids[0], batch_ids[-1], e)
        # Only fall back to per-item when the batch endpoint genuinely
        # omitted the relations field (not merely returned empty arrays)
        if not batch_returned_field:
            out.update(
                await _fetch_relations_per_item(
                    client, org, project, batch_ids, cfg
                )
            )
    return out


async def _fetch_work_item_types(
    client: httpx.AsyncClient, org: str, project: str,
    ids: list[int], cfg: RuntimeConfig,
) -> dict[int, str]:
    """Return {work_item_id: System.WorkItemType} for the given ids. Best-effort."""
    types: dict[int, str] = {}
    if not ids:
        return types
    url = (
        f"https://dev.azure.com/{org}/{project}/_apis/wit/workitemsbatch"
        f"?api-version={API_VER_WI}"
    )
    for start in range(0, len(ids), _BATCH_LIMIT):
        batch_ids = ids[start:start + _BATCH_LIMIT]
        try:
            data = await _post_json(
                client, url,
                {"ids": batch_ids, "fields": ["System.WorkItemType"]},
                cfg, label="wit-types",
            )
            for wi in data.get("value", []) or []:
                wid = int(wi.get("id", 0) or 0)
                wtype = str(
                    (wi.get("fields") or {}).get("System.WorkItemType", "")
                )
                if wid:
                    types[wid] = wtype
        except RuntimeError as e:
            _log.debug("_fetch_work_item_types batch %d-%d failed: %s",
                       batch_ids[0], batch_ids[-1], e)
    return types


def _classify_relations(
    relations_by_wi: dict[int, list[dict[str, Any]]],
) -> tuple[dict[int, set[int]], dict[int, int], dict[int, set[int]], set[int]]:
    """Classify relations into direct test links vs candidates needing type-check.

    Returns (direct, direct_noid, candidates, all_candidate_ids).
    """
    direct: dict[int, set[int]] = {}
    direct_noid: dict[int, int] = {}
    candidates: dict[int, set[int]] = {}
    all_candidate_ids: set[int] = set()
    for wid, rels in relations_by_wi.items():
        d: set[int] = set()
        c: set[int] = set()
        noid = 0
        for rel in rels:
            tid = _rel_target_id(rel)
            if _is_test_relation(rel):
                if tid:
                    d.add(tid)
                else:
                    noid += 1
            elif _is_link_relation(rel) and tid:
                c.add(tid)
        c -= d
        direct[wid] = d
        if noid:
            direct_noid[wid] = noid
        if c:
            candidates[wid] = c
            all_candidate_ids |= c
    return direct, direct_noid, candidates, all_candidate_ids


def _tally_test_counts(
    relations_by_wi: dict[int, list[dict[str, Any]]],
    direct: dict[int, set[int]],
    direct_noid: dict[int, int],
    candidates: dict[int, set[int]],
    test_typed: frozenset[int],
) -> dict[int, int]:
    """Tally final test-case counts per work item from classified relations.

    Only direct test relations (TestedBy/Tests) count toward the grid number.
    Related/dependency links to test-typed items are excluded from the grid
    count because they often belong to sibling/child stories and inflate the
    number. The E2E dialog uses test_steps.fetch_linked_test_cases which does
    its own deeper discovery for runnable test cases.
    """
    counts: dict[int, int] = {}
    for wid in relations_by_wi:
        d = direct.get(wid, set())
        total = len(d) + direct_noid.get(wid, 0)
        if total:
            counts[wid] = total
    return counts


async def _fetch_test_case_counts(
    client: httpx.AsyncClient, org: str, project: str,
    ids: list[int], cfg: RuntimeConfig,
) -> dict[int, int]:
    """Versatile linked-test-case discovery per work item. Counts DISTINCT test
    cases reached via test relations OR link targets that are test-type items.
    Best-effort: returns {} on error so the board still loads."""
    if not ids:
        return {}
    relations_by_wi = await _fetch_relations(client, org, project, ids, cfg)
    if not relations_by_wi:
        return {}

    direct, direct_noid, candidates, all_candidate_ids = _classify_relations(
        relations_by_wi
    )

    type_map = (
        await _fetch_work_item_types(
            client, org, project, sorted(all_candidate_ids), cfg
        )
        if all_candidate_ids else {}
    )
    test_typed = frozenset(
        tid for tid, t in type_map.items()
        if any(tok in _norm(t) for tok in _TEST_TYPE_TOKENS)
    )

    return _tally_test_counts(
        relations_by_wi, direct, direct_noid, candidates, test_typed
    )


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
        area_paths = await get_team_area_paths(
            org, project, team, cfg, on_log=on_log,
        )
    query = _build_wiql(project, wit_types, area_paths)
    if on_log:
        on_log(
            f"[INFO] Board '{board.label}': {len(columns)} columns, "
            f"types={wit_types}, area_scope={'ON' if scope_to_team_area else 'OFF'}"
            f"{', paths=' + repr(area_paths) if area_paths else ''}"
        )
    # Emit columns first so UI can render the skeleton
    if on_batch:
        on_batch([], -1, 0)  # -1 signals "columns ready, rows incoming"
    board_expects_items = wit_types != list(_DEFAULT_WIT_TYPES)
    possibly_degraded = False
    async with _client(cfg) as client:
        ids = await _run_wiql(client, org, project, query, cfg, on_log=on_log)
        if not ids and board_expects_items:
            if on_log:
                on_log(
                    f"[WARN] 0 items from WIQL for configured board "
                    f"'{board.label}' -- retrying after 4s (suspected ADO "
                    f"transient degradation)"
                )
            await asyncio.sleep(4.0)
            ids = await _run_wiql(client, org, project, query, cfg, on_log=on_log)
            if not ids:
                possibly_degraded = True
                if on_log:
                    on_log(
                        f"[ERROR] WIQL still returned 0 items for board "
                        f"'{board.label}' after retry -- marking as possibly "
                        f"degraded (ADO may be throttling this query)"
                    )
        if on_log:
            on_log(f"[INFO] WIQL matched {len(ids)} work item(s)")
        if not ids and not possibly_degraded and on_log:
            on_log(
                f"[WARN] 0 work items for board '{board.label}' -- "
                f"wit_types={wit_types}, area_paths={area_paths or '(none)'}, "
                f"scope_to_team_area={scope_to_team_area}"
            )
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
    return BoardView(columns=columns, rows=rows, possibly_degraded=possibly_degraded)


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


# ---------------------------------------------------------------------
# Work item detail
# ---------------------------------------------------------------------
def _detail_cfg(org: str, project: str, cfg: RuntimeConfig) -> RuntimeConfig:
    """Clone cfg with org/project set so the reused ado_extract fetchers
    target the right project."""
    return _dc_replace(cfg, organization=org, project=project)


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
    is simply left un-rewritten. Downloads concurrently (sem=6)."""
    urls: list[str] = []
    for html in (detail.description_html, detail.acceptance_html,
                 *[h for _, _, h in detail.comments_html]):
        urls.extend(extract_image_urls(html))
    seen: set[str] = set()
    unique = [u for u in urls if not (u in seen or seen.add(u))]
    if not unique:
        return
    media_dir.mkdir(parents=True, exist_ok=True)
    sem = asyncio.Semaphore(6)

    async def _download_one(i: int, url: str) -> None:
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
                return
            base = url.split("?")[0].split("/")[-1] or "img"
            dest = media_dir / f"inline_{detail.wi_id}_{i:03d}_{_safe_name(base)}"
            if not dest.suffix:
                dest = dest.with_suffix(".png")
            async with sem:
                await download_attachment(client, dcfg, url, dest)
            detail.inline_images[url] = str(dest)
        except Exception as e:  # noqa: BLE001
            _log.debug("_hydrate_inline_images WI %d url[%d] failed: %s",
                       detail.wi_id, i, e)

    await asyncio.gather(*(_download_one(i, url) for i, url in enumerate(unique)))


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
            except Exception as e:  # noqa: BLE001
                _log.debug("fetch_work_item_detail_async WI %d inline images failed: %s",
                           wi_id, e)
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


async def _batch_fetch_work_items(
    client: httpx.AsyncClient, org: str, project: str,
    ids: list[int], cfg: RuntimeConfig,
) -> dict[int, dict[str, Any]]:
    """Batch-fetch full work item payloads via workitemsbatch (up to 200/call).
    Returns {wi_id: payload}. Eliminates N individual GETs."""
    out: dict[int, dict[str, Any]] = {}
    if not ids:
        return out
    url = (
        f"https://dev.azure.com/{org}/{project}/_apis/wit/workitemsbatch"
        f"?api-version={API_VER_WI}"
    )
    for start in range(0, len(ids), _BATCH_LIMIT):
        batch_ids = ids[start:start + _BATCH_LIMIT]
        try:
            data = await _post_json(
                client, url,
                {"ids": batch_ids, "$expand": "All"},
                cfg, label="details-batch",
            )
            for wi in data.get("value", []) or []:
                wid = int(wi.get("id", 0) or 0)
                if wid:
                    out[wid] = wi
        except RuntimeError as e:
            _log.debug("_batch_fetch_work_items %d-%d failed: %s",
                       batch_ids[0], batch_ids[-1], e)
    return out


async def fetch_details_async(
    org: str, project: str, wi_ids: list[int], cfg: RuntimeConfig,
    on_progress: Callable[[int, int], None] | None = None,
    on_log: LogFn | None = None,
) -> list[WorkItemDetail]:
    """Fetch full detail for many work items. Batches the work-item payload
    (eliminating N individual GETs) and fetches comments per-item (no batch
    API for comments in ADO). Order of the returned list follows wi_ids."""
    dcfg = _detail_cfg(org, project, cfg)
    sem = asyncio.Semaphore(max(1, cfg.concurrency))
    done = {"n": 0}
    total = len(wi_ids)

    async with _client(cfg) as client:
        wi_map = await _batch_fetch_work_items(
            client, org, project, wi_ids, cfg
        )

        async def _fetch_comments_for(wid: int) -> WorkItemDetail | None:
            async with sem:
                wi = wi_map.get(wid)
                if not wi:
                    return None
                try:
                    comments = await fetch_comments(client, dcfg, wid)
                    return _to_detail(wi, comments)
                except Exception as e:
                    if on_log:
                        on_log(f"[ERROR] WI {wid} comments failed: {e!r}")
                    return _to_detail(wi, [])
                finally:
                    done["n"] += 1
                    if on_progress:
                        on_progress(done["n"], total)

        results = await asyncio.gather(
            *[_fetch_comments_for(w) for w in wi_ids]
        )
    by_id = {d.wi_id: d for d in results if d is not None}
    return [by_id[w] for w in wi_ids if w in by_id]


# ---------------------------------------------------------------------
# Sync wrappers (for callers not already on an event loop)
# ---------------------------------------------------------------------

def load_board_view(org: str, project: str, board: Board, cfg: RuntimeConfig,
                    on_log: LogFn | None = None) -> BoardView:
    """Sync convenience wrapper. Must not be called from within a running event
    loop (asyncio.run() starts a fresh loop; use the async variant instead)."""
    return asyncio.run(
        load_board_view_async(org, project, board, cfg, on_log=on_log)
    )


def fetch_work_item_detail(org: str, project: str, wi_id: int,
                           cfg: RuntimeConfig) -> WorkItemDetail:
    """Sync convenience wrapper. Must not be called from within a running event
    loop (asyncio.run() starts a fresh loop; use the async variant instead)."""
    return asyncio.run(fetch_work_item_detail_async(org, project, wi_id, cfg))
