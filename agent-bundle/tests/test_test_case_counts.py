"""Regression tests for linked "Tested By" test-case counting on the board.

These exercise the real ado.boards._fetch_test_case_counts logic with a mocked
HTTP transport (no live ADO), reproducing the shape of the actual payload for
User Story 1536967 which carries six TestedBy-Forward relations. Two paths are
covered: the workitemsbatch pass (with the capitalized "Relations" expand) and
the per-item GET fallback used when an org ignores batch $expand.
"""
from __future__ import annotations

import json

import httpx
import pytest

from ado import boards
from core.runtime_config import RuntimeConfig


def _tested_by(target_id: int) -> dict:
    return {
        "rel": "Microsoft.VSTS.Common.TestedBy-Forward",
        "url": f"https://dev.azure.com/o/p/_apis/wit/workItems/{target_id}",
        "attributes": {"isLocked": False, "name": "Tested By"},
    }


# Six relations, matching the real 1536967 payload the user provided.
_RELATIONS_1536967 = [_tested_by(t) for t in
                      (1618867, 1618871, 1618870, 1618866, 1618869, 1618868)]


def _cfg() -> RuntimeConfig:
    return RuntimeConfig(retry_count=2, retry_backoff_sec=0.0)


@pytest.mark.asyncio
async def test_batch_expand_counts_tested_by() -> None:
    """When the batch endpoint honors $expand=Relations, counts come straight
    from the batch response and no per-item GET is needed."""
    per_item_calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if "workitemsbatch" in request.url.path:
            body = json.loads(request.content.decode("utf-8"))
            # The body MUST use the capitalized enum name.
            assert body["$expand"] == "Relations"
            return httpx.Response(200, json={"value": [
                {"id": 1536967, "relations": _RELATIONS_1536967},
                {"id": 1536939},  # no relations key -> 0 test cases
            ]})
        per_item_calls.append(request.url.path)
        return httpx.Response(200, json={"id": 0})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        counts = await boards._fetch_test_case_counts(
            client, "org", "proj", [1536967, 1536939], _cfg()
        )

    assert counts == {1536967: 6}
    assert per_item_calls == []  # fallback must NOT trigger


@pytest.mark.asyncio
async def test_per_item_fallback_when_batch_ignores_expand() -> None:
    """When the batch endpoint returns items WITHOUT relations (org ignores
    batch $expand), the per-item GET fallback recovers the counts."""
    def handler(request: httpx.Request) -> httpx.Response:
        if "workitemsbatch" in request.url.path:
            # Batch ignores expand: no relations on any item.
            return httpx.Response(200, json={"value": [
                {"id": 1536967}, {"id": 1536939},
            ]})
        # Per-item GET honors expand.
        wid = int(request.url.path.rstrip("/").split("/")[-1])
        rels = _RELATIONS_1536967 if wid == 1536967 else []
        return httpx.Response(200, json={"id": wid, "relations": rels})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        counts = await boards._fetch_test_case_counts(
            client, "org", "proj", [1536967, 1536939], _cfg()
        )

    assert counts == {1536967: 6}


def test_count_tested_by_matches_reference_and_friendly_name() -> None:
    # Reference name only (no friendly attribute name).
    wi_ref = {"relations": [{"rel": "Microsoft.VSTS.Common.TestedBy-Forward"}]}
    assert boards._count_tested_by(wi_ref) == 1
    # Friendly attribute name only.
    wi_name = {"relations": [{"rel": "Other", "attributes": {"name": "Tested By"}}]}
    assert boards._count_tested_by(wi_name) == 1
    # Unrelated relation types are ignored.
    wi_other = {"relations": [
        {"rel": "System.LinkTypes.Hierarchy-Forward"},
        {"rel": "ArtifactLink", "attributes": {"name": "Build"}},
    ]}
    assert boards._count_tested_by(wi_other) == 0
    # Missing relations key -> 0.
    assert boards._count_tested_by({}) == 0
