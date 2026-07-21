"""
automation/parallel_runner.py
Parallel E2E execution with up to MAX_PARALLEL isolated browser contexts.

One shared Browser process, N independent BrowserContexts. Each context has:
- Own cookies/storage state (isolated login sessions)
- Own video recording directory
- Own stop signal (per-WI cancellation)
- Complete isolation: one context crash does not affect others

Architecture decision (ADR-1): N contexts from 1 browser rather than N
separate browser processes. Tradeoff: shares GPU/process memory but avoids
N cold starts (~3s each). A Chromium crash kills all contexts but suite-
level recovery already handles that case.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Coroutine

from playwright.async_api import (
    Browser,
    BrowserContext,
    Page,
    Playwright,
    async_playwright,
)

_log = logging.getLogger(__name__)

MAX_PARALLEL: int = 3

_AUTOMATION_PROFILE_BASE = Path.home() / ".testing_toolkit" / "e2e_profiles"


@dataclass
class SlotResult:
    """Result from one parallel execution slot."""

    wi_id: str
    success: bool
    result: Any = None
    error: str = ""


@dataclass
class ExecutionSlot:
    """One isolated browser context slot for a single work item."""

    wi_id: str
    context: BrowserContext | None = None
    page: Page | None = None
    stop_event: asyncio.Event = field(default_factory=asyncio.Event)
    output_dir: Path | None = None

    @property
    def stopped(self) -> bool:
        return self.stop_event.is_set()

    def request_stop(self) -> None:
        self.stop_event.set()


class ParallelRunner:
    """Manages up to MAX_PARALLEL isolated browser contexts for E2E execution.

    Usage:
        async with ParallelRunner(output_base=Path(...)) as runner:
            results = await runner.execute(
                wi_ids=["123", "456", "789"],
                run_fn=my_per_wi_function,
            )
    """

    def __init__(
        self,
        output_base: Path,
        *,
        headless: bool = False,
        maximized: bool = True,
    ) -> None:
        self._output_base = output_base
        self._headless = headless
        self._maximized = maximized
        self._pw: Playwright | None = None
        self._browser: Browser | None = None
        self._slots: dict[str, ExecutionSlot] = {}

    async def __aenter__(self) -> "ParallelRunner":
        from automation.playwright_bridge import (
            _kill_orphaned_automation_browsers,
            _require_playwright,
        )

        _require_playwright()
        _kill_orphaned_automation_browsers()
        _AUTOMATION_PROFILE_BASE.mkdir(parents=True, exist_ok=True)

        self._pw = await async_playwright().start()

        launch_args = [
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-background-networking",
            "--disable-client-side-phishing-detection",
            "--disable-sync",
            "--metrics-recording-only",
            "--no-service-autorun",
        ]
        if self._maximized and not self._headless:
            launch_args.append("--start-maximized")

        self._browser = await self._pw.chromium.launch(
            headless=self._headless,
            args=launch_args,
        )
        _log.info("[INFO] ParallelRunner: browser launched (max %d slots)", MAX_PARALLEL)
        return self

    async def __aexit__(self, *_: Any) -> None:
        for slot in self._slots.values():
            await self._close_slot(slot)
        self._slots.clear()

        if self._browser:
            try:
                await self._browser.close()
            except Exception:
                pass
            self._browser = None

        if self._pw:
            try:
                await self._pw.stop()
            except Exception:
                pass
            self._pw = None

    async def _create_context(self, wi_id: str) -> ExecutionSlot:
        """Create an isolated BrowserContext for one work item."""
        assert self._browser is not None

        slot_dir = self._output_base / wi_id
        slot_dir.mkdir(parents=True, exist_ok=True)
        video_dir = slot_dir / "video"
        video_dir.mkdir(parents=True, exist_ok=True)

        # Per-WI persistent storage for login state preservation
        storage_dir = _AUTOMATION_PROFILE_BASE / wi_id
        storage_dir.mkdir(parents=True, exist_ok=True)

        ctx_opts: dict[str, Any] = {
            "record_video_dir": str(video_dir),
            "record_video_size": {"width": 1920, "height": 1080},
        }

        if self._maximized and not self._headless:
            ctx_opts["no_viewport"] = True
        else:
            ctx_opts["viewport"] = {"width": 1920, "height": 1080}

        # Load storage state if exists (preserves login across runs)
        state_file = storage_dir / "state.json"
        if state_file.exists():
            ctx_opts["storage_state"] = str(state_file)

        context = await self._browser.new_context(**ctx_opts)
        page = await context.new_page()

        slot = ExecutionSlot(
            wi_id=wi_id,
            context=context,
            page=page,
            output_dir=slot_dir,
        )
        self._slots[wi_id] = slot
        _log.info("[INFO] ParallelRunner: context created for WI %s", wi_id)
        return slot

    async def _close_slot(self, slot: ExecutionSlot) -> None:
        """Close a slot's context and save storage state."""
        if slot.context is None:
            return

        # Save storage state for login persistence
        storage_dir = _AUTOMATION_PROFILE_BASE / slot.wi_id
        state_file = storage_dir / "state.json"
        try:
            storage_dir.mkdir(parents=True, exist_ok=True)
            await slot.context.storage_state(path=str(state_file))
        except Exception:
            pass

        try:
            await slot.context.close()
        except Exception:
            pass
        slot.context = None
        slot.page = None

    def stop_wi(self, wi_id: str) -> bool:
        """Request cancellation of a specific work item's execution."""
        slot = self._slots.get(wi_id)
        if slot:
            slot.request_stop()
            _log.info("[INFO] ParallelRunner: stop requested for WI %s", wi_id)
            return True
        return False

    def stop_all(self) -> None:
        """Request cancellation of all running slots."""
        for slot in self._slots.values():
            slot.request_stop()
        _log.info("[INFO] ParallelRunner: stop ALL requested")

    async def execute(
        self,
        wi_ids: list[str],
        run_fn: Callable[
            [ExecutionSlot],
            Coroutine[Any, Any, Any],
        ],
        *,
        on_slot_done: Callable[[SlotResult], None] | None = None,
    ) -> list[SlotResult]:
        """Execute run_fn for each wi_id with up to MAX_PARALLEL concurrency.

        Args:
            wi_ids: Work item IDs to execute (any count; batched to MAX_PARALLEL).
            run_fn: Async function receiving an ExecutionSlot, returns result data.
            on_slot_done: Optional callback fired when each slot completes.

        Returns:
            List of SlotResult in same order as wi_ids.
        """
        results: list[SlotResult] = []
        # Process in batches of MAX_PARALLEL
        for batch_start in range(0, len(wi_ids), MAX_PARALLEL):
            batch = wi_ids[batch_start:batch_start + MAX_PARALLEL]
            batch_results = await self._execute_batch(batch, run_fn, on_slot_done)
            results.extend(batch_results)
        return results

    async def _execute_batch(
        self,
        wi_ids: list[str],
        run_fn: Callable[[ExecutionSlot], Coroutine[Any, Any, Any]],
        on_slot_done: Callable[[SlotResult], None] | None,
    ) -> list[SlotResult]:
        """Execute one batch of up to MAX_PARALLEL work items concurrently."""
        # Create contexts for all items in batch
        slots: list[ExecutionSlot] = []
        for wi_id in wi_ids:
            try:
                slot = await self._create_context(wi_id)
                slots.append(slot)
            except Exception as e:
                _log.error("[ERROR] Failed to create context for WI %s: %s", wi_id, e)
                slots.append(ExecutionSlot(wi_id=wi_id))

        async def _run_slot(slot: ExecutionSlot) -> SlotResult:
            if slot.context is None:
                return SlotResult(
                    wi_id=slot.wi_id, success=False, error="Context creation failed"
                )
            try:
                result = await run_fn(slot)
                return SlotResult(wi_id=slot.wi_id, success=True, result=result)
            except Exception as e:
                _log.error("[ERROR] WI %s execution failed: %s", slot.wi_id, e)
                return SlotResult(wi_id=slot.wi_id, success=False, error=str(e))
            finally:
                await self._close_slot(slot)

        # Run all slots concurrently
        tasks = [asyncio.create_task(_run_slot(s)) for s in slots]
        batch_results: list[SlotResult] = []
        for task in asyncio.as_completed(tasks):
            result = await task
            batch_results.append(result)
            if on_slot_done:
                on_slot_done(result)

        # Reorder to match input wi_ids order
        result_map = {r.wi_id: r for r in batch_results}
        return [result_map.get(wid, SlotResult(wi_id=wid, success=False, error="lost"))
                for wid in wi_ids]
