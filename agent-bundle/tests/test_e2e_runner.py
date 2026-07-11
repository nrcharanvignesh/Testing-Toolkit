from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

import pytest


class _Page:
    def __init__(self) -> None:
        self.gotos: list[str] = []

    async def goto(self, url: str, **_kwargs) -> None:
        self.gotos.append(url)

    async def screenshot(self, path: str, **_kwargs) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_bytes(b"png")


@pytest.mark.asyncio
async def test_suite_reuses_one_browser_session(monkeypatch, tmp_path):
    import automation.e2e_runner as runner

    page = _Page()
    sessions = 0

    @asynccontextmanager
    async def fake_session(**_kwargs):
        nonlocal sessions
        sessions += 1
        yield object(), page

    async def fake_step(_page, step, _username, _password, _directory, step_num, **_kwargs):
        return runner.StepResult(
            step_num=step_num,
            action=str(step["action"]),
            expected="",
            actual="executed",
            status="pass",
        )

    monkeypatch.setattr(runner, "browser_session", fake_session)
    monkeypatch.setattr(runner, "_execute_step", fake_step)
    cases = [
        {"id": "one", "title": "One", "steps": [{"action": "click", "target": "Go"}]},
        {"id": "two", "title": "Two", "steps": [{"action": "click", "target": "Go"}]},
    ]
    results = await runner.run_e2e_tests(
        cases, "https://example.test", "user", "password", tmp_path,
    )
    assert sessions == 1
    assert page.gotos == ["https://example.test", "https://example.test"]
    assert [result.overall_status for result in results] == ["pass", "pass"]


@pytest.mark.asyncio
async def test_empty_compiled_plan_fails_closed(monkeypatch, tmp_path):
    import automation.e2e_runner as runner

    @asynccontextmanager
    async def fake_session(**_kwargs):
        yield object(), _Page()

    monkeypatch.setattr(runner, "browser_session", fake_session)
    results = await runner.run_e2e_tests(
        [{"id": "bad", "title": "Bad", "steps": [], "plan_error": "Compiler rejected step"}],
        "https://example.test", "user", "password", tmp_path,
    )
    assert len(results) == 1
    assert results[0].overall_status == "error"
    assert results[0].steps[0].actual == "Compiler rejected step"


@pytest.mark.asyncio
async def test_hard_step_error_stops_remaining_steps(monkeypatch, tmp_path):
    import automation.e2e_runner as runner

    @asynccontextmanager
    async def fake_session(**_kwargs):
        yield object(), _Page()

    calls: list[str] = []

    async def fake_step(_page, step, _username, _password, _directory, step_num, **_kwargs):
        calls.append(str(step["action"]))
        return runner.StepResult(
            step_num=step_num,
            action=str(step["action"]),
            expected="",
            actual="failed",
            status="error",
        )

    monkeypatch.setattr(runner, "browser_session", fake_session)
    monkeypatch.setattr(runner, "_execute_step", fake_step)
    results = await runner.run_e2e_tests(
        [{"id": "bad", "title": "Bad", "steps": [
            {"action": "click", "target": "Missing"},
            {"action": "click", "target": "Never"},
        ]}],
        "https://example.test", "user", "password", tmp_path,
    )
    assert calls == ["click"]
    assert results[0].overall_status == "error"
