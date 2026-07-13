"""Tests for linked tracker test-case step discovery (ADO + JIRA)."""
from __future__ import annotations

import json

import httpx
import pytest

from ado.test_steps import fetch_linked_test_cases as ado_fetch
from ado.test_steps import parse_steps_xml
from ado.testcase_creator import build_steps_xml
from core.runtime_config import RuntimeConfig
from jira.test_steps import (
    _linked_test_keys,
    fetch_linked_test_cases as jira_fetch,
    parse_xray_steps,
    parse_zephyr_scale_steps,
)


def _cfg() -> RuntimeConfig:
    cfg = RuntimeConfig.from_env_defaults()
    cfg.pat = "pat"
    cfg.organization = "org"
    cfg.retry_count = 1
    cfg.retry_backoff_sec = 0.0
    return cfg


# --------------------------- ADO XML parser ---------------------------
def test_ado_steps_xml_round_trips() -> None:
    steps = [
        {"action": "Go to login page", "expected": "Page loads"},
        {"action": "Enter user & pass", "expected": "if a > b then ok"},
        {"action": "Submit <form>", "expected": "Dashboard shown"},
    ]
    assert parse_steps_xml(build_steps_xml(steps)) == steps


def test_ado_steps_xml_empty_and_garbage() -> None:
    assert parse_steps_xml("") == []
    assert parse_steps_xml("not xml <") == []
    assert parse_steps_xml('<steps id="0" last="1"></steps>') == []


# --------------------------- ADO linked fetch ---------------------------
@pytest.mark.asyncio
async def test_ado_fetch_linked_test_cases() -> None:
    steps_xml = build_steps_xml([{"action": "click", "expected": "ok"}])

    def rel(kind: str, tid: int) -> dict:
        return {
            "rel": kind,
            "url": f"https://dev.azure.com/o/p/_apis/wit/workItems/{tid}",
            "attributes": {"name": "Tested By"} if "Tested" in kind else {},
        }

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode("utf-8"))
        if "$expand" in body:
            # WI 900 -> TestedBy 950 + a child link 951
            return httpx.Response(200, json={"value": [{
                "id": 900,
                "relations": [
                    rel("Microsoft.VSTS.Common.TestedBy-Forward", 950),
                    rel("System.LinkTypes.Hierarchy-Forward", 951),
                ],
            }]})
        # fields pass: 950 is a Test Case with steps; 951 is a Task (excluded)
        info = {
            950: {"System.Title": "Login test",
                  "System.WorkItemType": "Test Case",
                  "Microsoft.VSTS.TCM.Steps": steps_xml},
            951: {"System.Title": "Some task",
                  "System.WorkItemType": "Task",
                  "Microsoft.VSTS.TCM.Steps": ""},
        }
        return httpx.Response(200, json={"value": [
            {"id": tid, "fields": info.get(tid, {})} for tid in body["ids"]
        ]})

    transport = httpx.MockTransport(handler)
    import ado.test_steps as ts

    orig = ts._client
    ts._client = lambda cfg: httpx.AsyncClient(transport=transport)  # type: ignore
    try:
        out = await ado_fetch("org", "proj", [900], _cfg())
    finally:
        ts._client = orig

    assert len(out) == 1
    tc = out[0]
    assert tc["id"] == "900" and tc["tc_id"] == "950"
    assert tc["source"] == "ado" and tc["title"] == "Login test"
    assert tc["steps"] == [{"action": "click", "expected": "ok"}]


# --------------------------- JIRA parsers ---------------------------
def test_parse_xray_steps() -> None:
    payload = [
        {"step": {"raw": "Open app"}, "data": {"raw": "url=/x"},
         "result": {"raw": "App opens"}},
        {"step": "Login", "data": "", "result": "Home shown"},
    ]
    steps = parse_xray_steps(payload)
    assert steps == [
        {"action": "Open app (data: url=/x)", "expected": "App opens"},
        {"action": "Login", "expected": "Home shown"},
    ]


def test_parse_zephyr_scale_steps() -> None:
    payload = {"testScript": {"steps": [
        {"description": "Navigate", "testData": "", "expectedResult": "Loaded"},
        {"description": "Click save", "expectedResult": "Saved"},
    ]}}
    steps = parse_zephyr_scale_steps(payload)
    assert steps == [
        {"action": "Navigate", "expected": "Loaded"},
        {"action": "Click save", "expected": "Saved"},
    ]


def test_jira_linked_test_keys_dedup() -> None:
    fields = {
        "issuelinks": [
            {"type": {"name": "Tests"},
             "outwardIssue": {"key": "PROJ-2",
                              "fields": {"issuetype": {"name": "Test"}}}},
            {"type": {"name": "Relates"},
             "inwardIssue": {"key": "PROJ-3",
                             "fields": {"issuetype": {"name": "Story"}}}},
        ],
        "subtasks": [
            {"key": "PROJ-2", "fields": {"issuetype": {"name": "Test"}}},
            {"key": "PROJ-9", "fields": {"issuetype": {"name": "Test Sub"}}},
        ],
    }
    # PROJ-2 via link + subtask -> once; PROJ-3 (Story) excluded; PROJ-9 kept.
    assert _linked_test_keys(fields) == ["PROJ-2", "PROJ-9"]


@pytest.mark.asyncio
async def test_jira_fetch_falls_back_to_summary() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/rest/api/2/issue/PROJ-1":
            return httpx.Response(200, json={"fields": {
                "issuelinks": [{
                    "type": {"name": "Tests"},
                    "outwardIssue": {
                        "key": "PROJ-5",
                        "fields": {"issuetype": {"name": "Test"}},
                    },
                }],
                "subtasks": [],
            }})
        if path == "/rest/api/2/issue/PROJ-5":
            return httpx.Response(200, json={"fields": {
                "summary": "Verify checkout", "description": "Step body"}})
        # Xray + Zephyr endpoints 404 -> force fallback.
        return httpx.Response(404, json={})

    # Patch the AsyncClient transport by monkeypatching httpx.AsyncClient use
    # inside jira.test_steps via base_url + transport.
    import jira.test_steps as jts

    real_client = httpx.AsyncClient

    def fake_client(*args, **kwargs):  # noqa: ANN002, ANN003
        kwargs.pop("verify", None)
        kwargs["transport"] = httpx.MockTransport(handler)
        return real_client(*args, **kwargs)

    jts.httpx.AsyncClient = fake_client  # type: ignore
    try:
        out = await jira_fetch(
            "https://jira.example.com", "u", "p", "PROJ", ["PROJ-1"], _cfg()
        )
    finally:
        jts.httpx.AsyncClient = real_client  # type: ignore

    assert len(out) == 1
    assert out[0]["tc_id"] == "PROJ-5" and out[0]["source"] == "jira"
    assert out[0]["steps"] == [
        {"action": "Verify checkout. Step body", "expected": ""}
    ]
