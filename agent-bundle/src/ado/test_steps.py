"""
ado/test_steps.py
Fetch and parse the REAL steps of linked ADO Test Case work items so they can
be listed and RUN in the E2E dialog (not just counted on the board).

The board's "Generated Tests" column counts test cases linked to each work item
(see ``ado.boards`` discovery). This module resolves those linked Test Case work
items to their actual step content -- the ``Microsoft.VSTS.TCM.Steps`` field,
which ADO stores as XML with HTML-encoded step text -- and returns them in the
same flat shape the E2E runner consumes:

    {"id": <parent work item id>, "tc_id": <test case id>, "title": ...,
     "steps": [{"action": ..., "expected": ...}], "category": "linked",
     "source": "ado"}

Reuses the versatile discovery helpers from ``ado.boards`` so the set of linked
test cases matches the board count exactly (Tested By / Tests relations plus
test-type child/related work items).
"""
from __future__ import annotations

import re
from html import unescape
from typing import Any
from xml.etree import ElementTree as ET

import httpx

from ado.boards import (
    _BATCH_LIMIT,
    _client,
    _fetch_relations,
    _is_link_relation,
    _is_test_relation,
    _norm,
    _rel_target_id,
    _TEST_TYPE_TOKEN,
    _ssl,
)
from core.runtime_config import API_VER_WI, RuntimeConfig

_STEPS_FIELD = "Microsoft.VSTS.TCM.Steps"
_TITLE_FIELD = "System.Title"
_TYPE_FIELD = "System.WorkItemType"

_TAG_RE = re.compile(r"<[^>]+>")


def _html_to_text(html: str) -> str:
    """Strip HTML tags and decode entities from a step's parameterizedString
    body, yielding plain text suitable for the runner. Block/break tags become
    spaces so words don't run together."""
    if not html:
        return ""
    s = re.sub(r"(?i)<br\s*/?>", " ", html)
    s = re.sub(r"(?i)</(p|div|li)>", " ", s)
    s = _TAG_RE.sub("", s)
    s = unescape(s)
    return re.sub(r"\s+", " ", s).strip()


def parse_steps_xml(xml: str) -> list[dict[str, str]]:
    """Parse a ``Microsoft.VSTS.TCM.Steps`` XML value into ``[{action,
    expected}]``. Reverse of ``ado.testcase_creator.build_steps_xml``.

    Each ``<step>`` holds two ``<parameterizedString>`` children: the action and
    the expected result. ElementTree decodes the outer XML once, leaving the
    inner HTML string as the element text, which we then tag-strip and unescape.
    Best-effort: returns [] on any parse error or empty field."""
    if not xml or not xml.strip():
        return []
    try:
        root = ET.fromstring(xml)
    except ET.ParseError:
        return []
    steps: list[dict[str, str]] = []
    for step in root.iter("step"):
        params = step.findall("parameterizedString")
        action = _html_to_text(params[0].text or "") if len(params) >= 1 else ""
        expected = _html_to_text(params[1].text or "") if len(params) >= 2 else ""
        if action or expected:
            steps.append({"action": action, "expected": expected})
    return steps


async def _fetch_test_case_fields(
    client: httpx.AsyncClient, org: str, project: str,
    ids: list[int], cfg: RuntimeConfig,
) -> dict[int, dict[str, Any]]:
    """Batch-fetch Title, WorkItemType, and the Steps XML for candidate test
    case work items. Returns {id: {title, type, steps_xml}}."""
    out: dict[int, dict[str, Any]] = {}
    if not ids:
        return out
    url = (
        f"https://dev.azure.com/{org}/{project}/_apis/wit/workitemsbatch"
        f"?api-version={API_VER_WI}"
    )
    fields = [_TITLE_FIELD, _TYPE_FIELD, _STEPS_FIELD]
    for start in range(0, len(ids), _BATCH_LIMIT):
        batch_ids = ids[start:start + _BATCH_LIMIT]
        for attempt in range(cfg.retry_count):
            try:
                r = await client.post(
                    url, json={"ids": batch_ids, "fields": fields},
                    headers={"Content-Type": "application/json"},
                )
                if r.status_code in (429, 503):
                    import asyncio
                    await asyncio.sleep(cfg.retry_backoff_sec * (attempt + 1))
                    continue
                r.raise_for_status()
                for wi in r.json().get("value", []) or []:
                    wid = int(wi.get("id", 0) or 0)
                    f = wi.get("fields") or {}
                    if wid:
                        out[wid] = {
                            "title": str(f.get(_TITLE_FIELD, "")),
                            "type": str(f.get(_TYPE_FIELD, "")),
                            "steps_xml": str(f.get(_STEPS_FIELD, "") or ""),
                        }
                break
            except (httpx.HTTPError, _ssl.SSLError):
                import asyncio
                await asyncio.sleep(cfg.retry_backoff_sec * (attempt + 1))
    return out


def _linked_test_case_ids(
    relations_by_wi: dict[int, list[dict[str, Any]]],
) -> tuple[dict[int, list[int]], set[int], set[int]]:
    """From relations, return (parent -> candidate target ids), the set of ids
    reached via a TEST relation (definitely test cases), and the set reached
    only via a link (must be type-checked)."""
    per_parent: dict[int, list[int]] = {}
    definite: set[int] = set()
    link_only: set[int] = set()
    for wid, rels in relations_by_wi.items():
        d: list[int] = []
        for rel in rels:
            tid = _rel_target_id(rel)
            if not tid:
                continue
            if _is_test_relation(rel):
                definite.add(tid)
                d.append(tid)
            elif _is_link_relation(rel):
                link_only.add(tid)
                d.append(tid)
        if d:
            per_parent[wid] = d
    link_only -= definite
    return per_parent, definite, link_only


async def fetch_linked_test_cases(
    org: str, project: str, wi_ids: list[int], cfg: RuntimeConfig,
) -> list[dict[str, Any]]:
    """Return runnable test cases linked to the given work items.

    Discovery matches the board count (Tested By / Tests relations + test-type
    child/related items). Each returned dict carries the PARENT work item id in
    ``id`` (so the E2E dialog groups it under the right work item) plus the real
    parsed steps. Best-effort: returns [] on error."""
    if not wi_ids:
        return []
    async with _client(cfg) as client:
        relations = await _fetch_relations(client, org, project, wi_ids, cfg)
        if not relations:
            return []
        per_parent, definite, link_only = _linked_test_case_ids(relations)
        all_ids = sorted(definite | link_only)
        fields = await _fetch_test_case_fields(
            client, org, project, all_ids, cfg
        )

    # A link-only target is a test case only when its type says so.
    valid: set[int] = set(definite)
    for tid in link_only:
        info = fields.get(tid)
        if info and _TEST_TYPE_TOKEN in _norm(info.get("type", "")):
            valid.add(tid)

    out: list[dict[str, Any]] = []
    for parent, targets in per_parent.items():
        seen: set[int] = set()
        for tid in targets:
            if tid not in valid or tid in seen:
                continue
            seen.add(tid)
            info = fields.get(tid)
            if not info:
                continue
            steps = parse_steps_xml(info.get("steps_xml", ""))
            if not steps:
                # No runnable steps -> not usable for E2E; skip.
                continue
            out.append({
                "id": str(parent),
                "tc_id": str(tid),
                "title": info.get("title", "") or f"Test Case {tid}",
                "steps": steps,
                "category": "linked",
                "source": "ado",
            })
    return out
