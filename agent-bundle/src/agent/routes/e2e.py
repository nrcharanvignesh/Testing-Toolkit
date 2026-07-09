"""
agent/routes/e2e.py
E2E browser automation endpoints for the web UI.

Mirrors the desktop E2EDialog: pick an environment (from the credential
vault), pick generated test cases, run them through Playwright over a CDP
attach to the user's real browser (SSO/MFA preserved), stream live
progress/logs via the shared job registry, and produce an Excel report plus
a persisted execution run.

SECURITY: the password is resolved SERVER-SIDE from the credential vault by
environment name. It is NEVER accepted from, or returned to, the browser. The
runner only passes it to page.fill(); it is never logged or written to disk.
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from agent.jobs import JOBS, Job
from automation.credential_vault import CredentialVault
from core.app_config import OUTPUTS_DIR
from core.project_store import ensure_project

router = APIRouter()
_VAULT = CredentialVault()


# ---------------------------------------------------------------------
# Test-case discovery (from the generation JSON sidecar)
# ---------------------------------------------------------------------
def _latest_sidecar_test_cases(project: str) -> list[dict[str, Any]]:
    """Aggregate flat test cases across ALL generated JSON sidecars, keeping
    each work item's MOST RECENT generation.

    Generation writes one ``review_*.json`` per run (next to each reviewer
    .xlsx) with a flat ``test_cases`` list where each entry's ``id`` is the
    parent work item. A run may cover several work items, and different work
    items are frequently generated in separate runs. Reading only the newest
    sidecar (the old behavior) therefore dropped every work item except the
    one in the latest run -- so selecting multiple work items on the board
    showed test cases for just one of them.

    We walk sidecars newest -> oldest and, for each work item id, take its
    test cases from the FIRST (newest) sidecar that contains it. This yields
    every work item that has ever been generated, each at its latest version,
    and a later regeneration of a work item supersedes its older test cases.
    """
    paths = ensure_project(project)
    gen_dir = paths.generated_dir
    if not gen_dir.exists():
        return []
    seen_wi: set[str] = set()
    aggregated: list[dict[str, Any]] = []
    for json_path in sorted(
        gen_dir.glob("review_*.json"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    ):
        try:
            data = json.loads(json_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        tcs = data if isinstance(data, list) else data.get("test_cases", [])
        if not tcs:
            continue
        # Work items introduced by THIS (newer) sidecar win; skip any work
        # item already contributed by a more recent sidecar.
        wi_in_file = {str(tc.get("id", "")) for tc in tcs}
        new_wi = wi_in_file - seen_wi
        if not new_wi:
            continue
        for tc in tcs:
            if str(tc.get("id", "")) in new_wi:
                aggregated.append(tc)
        seen_wi |= new_wi
    return aggregated


@router.get("/test-cases/{project}")
def list_test_cases(project: str) -> dict[str, Any]:
    """Return selectable test cases for a project.

    Each entry: index (stable position in the sidecar), wi_id (parent work
    item), title, and step_count. Steps themselves are not sent — the runner
    reads them server-side by index at run time.
    """
    tcs = _latest_sidecar_test_cases(project)
    out = [
        {
            "index": i,
            "wi_id": str(tc.get("id", "")),
            "title": str(tc.get("title", "Untitled")),
            "step_count": len(tc.get("steps") or []),
            "category": str(tc.get("category", "")),
        }
        for i, tc in enumerate(tcs)
    ]
    return {"test_cases": out}


# ---------------------------------------------------------------------
# Environments (masked credentials usable as run targets)
# ---------------------------------------------------------------------
@router.get("/environments/{project}")
def list_environments(project: str) -> dict[str, Any]:
    """Return environments (credential envs) that can be run against.

    Only envs with a stored password can actually log in; the flag lets the
    UI disable un-runnable rows. Passwords are never included.
    """
    creds = _VAULT.load(project)
    return {
        "environments": [
            {
                "env": c.env,
                "login_url": c.login_url,
                "user_id": c.user_id,
                "login_method": c.login_method,
                "has_password": bool(c.password),
            }
            for c in creds
        ]
    }


# ---------------------------------------------------------------------
# Last run summary + re-run-failed support
# ---------------------------------------------------------------------
@router.get("/last-run/{project}")
def last_run(project: str) -> dict[str, Any]:
    """Return a summary of the most recent execution run, or null."""
    from automation.execution_store import load_latest_run

    run = load_latest_run(project)
    if run is None:
        return {"run": None}
    return {
        "run": {
            "run_id": run.run_id,
            "started_at": run.started_at,
            "finished_at": run.finished_at,
            "total": run.total,
            "passed": run.passed,
            "failed": run.failed,
            "skipped": run.skipped,
            "results": [
                {
                    "tc_id": r.tc_id,
                    "tc_title": r.tc_title,
                    "status": r.status,
                    "duration_ms": r.duration_ms,
                }
                for r in run.results
            ],
        }
    }


# ---------------------------------------------------------------------
# Run E2E tests (background job)
# ---------------------------------------------------------------------
class E2EStartRequest(BaseModel):
    project: str
    env: str
    # Indices into the latest sidecar's test_cases list. Empty = run all.
    indices: list[int] = []


def _persist_run(project: str, results: list[Any], started_at: float) -> str:
    """Persist a completed run via execution_store. Returns the run_id."""
    from automation.execution_store import ExecutionRun, TestResult, save_run

    now = time.time()
    test_results: list[TestResult] = []
    for r in results:
        first_shot = ""
        for s in getattr(r, "steps", []) or []:
            if getattr(s, "screenshot_path", None):
                first_shot = str(s.screenshot_path)
                break
        err = ""
        for s in getattr(r, "steps", []) or []:
            if s.status in ("fail", "error"):
                err = s.actual
                break
        test_results.append(
            TestResult(
                tc_id=getattr(r, "tc_id", ""),
                tc_title=getattr(r, "title", ""),
                status=getattr(r, "overall_status", "skip"),
                duration_ms=int(getattr(r, "duration_ms", 0)),
                error_message=err,
                screenshot_path=first_shot,
                timestamp=now,
            )
        )
    run = ExecutionRun(
        run_id=str(uuid.uuid4()),
        project_full=project,
        started_at=started_at,
        finished_at=now,
        results=test_results,
        total=len(test_results),
        passed=sum(1 for t in test_results if t.status == "pass"),
        failed=sum(1 for t in test_results if t.status in ("fail", "error")),
        skipped=sum(1 for t in test_results if t.status == "skip"),
    )
    try:
        save_run(project, run)
    except Exception:  # noqa: BLE001 - persistence is best-effort
        pass
    return run.run_id


async def _run_e2e(job: Job, req: E2EStartRequest) -> None:
    """Background E2E execution: load creds + test cases, drive Playwright,
    write the Excel report, and persist the run."""
    try:
        # 1) Resolve the credential (password stays server-side).
        cred = _VAULT.get_for_env(req.project, req.env)
        if cred is None:
            job.fail(f"No credential found for environment '{req.env}'.")
            return
        if not cred.password:
            job.fail(f"Environment '{req.env}' has no stored password.")
            return
        if not cred.login_url:
            job.fail(f"Environment '{req.env}' has no login URL.")
            return

        # 2) Select the test cases by index (default: all).
        all_tcs = _latest_sidecar_test_cases(req.project)
        if not all_tcs:
            job.fail("No generated test cases found for this project.")
            return
        if req.indices:
            picked = [all_tcs[i] for i in req.indices if 0 <= i < len(all_tcs)]
        else:
            picked = all_tcs
        if not picked:
            job.fail("No valid test cases selected.")
            return

        job.set_progress("running", 0, len(picked))
        job.log(f"[INFO] Running {len(picked)} test case(s) against '{req.env}'.")

        # 3) Lazy import so the agent starts even without Playwright installed.
        try:
            from automation.e2e_runner import run_e2e_tests
        except Exception as e:  # noqa: BLE001
            job.fail(
                "Playwright is not installed on the agent host. "
                "Run 'pip install playwright' and 'playwright install' to "
                f"enable E2E automation. ({type(e).__name__})"
            )
            return

        output_dir = OUTPUTS_DIR / "e2e" / req.project
        output_dir.mkdir(parents=True, exist_ok=True)

        # Per-test-case status accumulates into the job result so the browser
        # can colour each row as it finishes.
        tc_statuses: dict[str, str] = {}

        def _on_tc_done(tc_id: str, status: str) -> None:
            tc_statuses[tc_id] = status
            job.result = {**job.result, "tc_statuses": dict(tc_statuses)}

        def _on_screenshot(path: "Path", step_num: int, status: str) -> None:
            # Stream screenshot path to job log so the UI can surface it.
            job.log(f"[SCREENSHOT] step={step_num} status={status} path={path}")

        results = await run_e2e_tests(
            test_cases=picked,
            login_url=cred.login_url,
            username=cred.user_id,
            password=cred.password,
            output_dir=output_dir,
            ai_instructions=cred.ai_instructions,
            stop_fn=lambda: job.stopped,
            on_progress=lambda cur, tot: job.set_progress("running", cur, tot),
            on_log=lambda msg: job.log(msg),
            on_screenshot=_on_screenshot,
            on_tc_done=_on_tc_done,
        )

        if job.stopped:
            job.state = "stopped"
            job.log("[WARN] E2E run stopped by user.")
            return

        # 4) Excel report (best-effort).
        report_path = ""
        try:
            from automation.report_excel import write_e2e_report

            rp = output_dir / f"e2e_report_{int(time.time())}.xlsx"
            await asyncio.to_thread(write_e2e_report, results, rp)
            report_path = str(rp)
            job.log(f"[INFO] Report saved: {rp.name}")
        except Exception as e:  # noqa: BLE001
            job.log(f"[WARN] Report generation failed: {type(e).__name__}: {e}")

        # 5) Persist the run for history + re-run-failed.
        run_id = _persist_run(req.project, results, job.created_at)

        passed = sum(1 for r in results if r.overall_status == "pass")
        failed = sum(1 for r in results if r.overall_status in ("fail", "error"))
        job.finish(
            {
                "run_id": run_id,
                "report_path": report_path,
                "total": len(results),
                "passed": passed,
                "failed": failed,
                "tc_statuses": dict(tc_statuses),
                "results": [
                    {
                        "tc_id": r.tc_id,
                        "title": r.title,
                        "status": r.overall_status,
                        "duration_ms": r.duration_ms,
                        "script_path": str(r.script_path) if r.script_path else "",
                        "video_path": str(r.video_path) if r.video_path else "",
                        "steps": [
                            {
                                "step_num": s.step_num,
                                "action": s.action,
                                "expected": s.expected,
                                "actual": s.actual,
                                "status": s.status,
                                "screenshot_path": (
                                    str(s.screenshot_path)
                                    if s.screenshot_path
                                    else ""
                                ),
                            }
                            for s in r.steps
                        ],
                    }
                    for r in results
                ],
            }
        )
        job.log(f"[SUCCESS] E2E complete: {passed} passed, {failed} failed.")
    except Exception as e:  # noqa: BLE001
        job.fail(f"{type(e).__name__}: {e}")
        job.log(f"[ERROR] {job.error}")


@router.post("/start")
async def start_e2e(req: E2EStartRequest) -> dict[str, str]:
    if not req.project:
        raise HTTPException(400, "No project specified")
    if not req.env:
        raise HTTPException(400, "No environment specified")
    job = JOBS.create("e2e", project=req.project)
    job.log("[INFO] Starting E2E automation...")
    asyncio.create_task(_run_e2e(job, req))
    return {"job_id": job.id}


@router.post("/stop/{job_id}")
def stop_e2e(job_id: str) -> dict[str, Any]:
    """Signal a running E2E job to stop after the current test case."""
    job = JOBS.get(job_id)
    if job is None:
        raise HTTPException(404, "Job not found")
    job.stop_event.set()
    return {"stopped": True}
