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
from typing import Any, Final

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from agent.jobs import JOBS, Job, spawn_job_task
from automation.credential_vault import CredentialVault
from core.app_config import EXPORTS_DIR, OUTPUTS_DIR
from core.trace import trace
from core.project_store import ensure_project

router = APIRouter()
_VAULT = CredentialVault()

MAX_PLAN_COMPILE_RETRIES: Final[int] = 3
MAX_TC_RETRIES: Final[int] = 2
MAX_SUITE_RECOVERY: Final[int] = 1


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


def _parse_wi_ids(wi_ids: str) -> list[str]:
    """Parse the comma-separated wi_ids query param into a clean list."""
    return [p.strip() for p in (wi_ids or "").split(",") if p.strip()]


async def _fetch_linked_test_cases(
    project: str, wi_ids: list[str],
) -> list[dict[str, Any]]:
    """Discover the REAL, runnable test cases linked to the given work items in
    the tracker (ADO Test Case work items or JIRA test issues), matching the
    board's "Generated Tests" count. Returns [] when nothing is scoped or the
    source is not configured. Best-effort: never raises."""
    if not wi_ids:
        return []
    try:
        from core.source_resolver import SourceType, resolve
        from core.settings_store import build_runtime_config

        res = resolve(project)
        cfg = build_runtime_config()
        if res.source is SourceType.JIRA:
            from jira.test_steps import fetch_linked_test_cases as jira_fetch

            return await jira_fetch(
                res.url, res.user, res.pat, res.project, wi_ids, cfg
            )
        # ADO: work item ids are numeric.
        from ado.test_steps import fetch_linked_test_cases as ado_fetch

        numeric = [int(w) for w in wi_ids if w.isdigit()]
        cfg.pat = res.pat
        cfg.organization = res.organization
        return await ado_fetch(res.organization, res.project, numeric, cfg)
    except Exception:  # noqa: BLE001 - discovery is best-effort
        return []


async def _all_test_cases(
    project: str, wi_ids: list[str],
) -> list[dict[str, Any]]:
    """The full runnable test-case list for a project: app-generated sidecar
    test cases FIRST (their indices are preserved), then linked tracker test
    cases. This single builder is used by BOTH the list and run endpoints so
    the index space is IDENTICAL -- a selected index always maps to the same
    test case at run time. Linked test cases are de-duplicated by (source,
    tc_id)."""
    generated = _latest_sidecar_test_cases(project)
    for tc in generated:
        tc.setdefault("source", "generated")
    linked = await _fetch_linked_test_cases(project, wi_ids)
    seen: set[tuple[str, str]] = set()
    deduped: list[dict[str, Any]] = []
    for tc in linked:
        key = (str(tc.get("source", "")), str(tc.get("tc_id", "")))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(tc)
    return generated + deduped


@router.get("/test-cases/{project}")
@trace
async def list_test_cases(project: str, wi_ids: str = "") -> dict[str, Any]:
    """Return selectable test cases for a project.

    Each entry: index (stable position in the merged list), wi_id (parent work
    item), title, step_count, category, and source ("generated" for app-created
    test cases, "ado"/"jira" for test cases linked in the tracker). Steps
    themselves are not sent — the runner reads them server-side by index.

    ``wi_ids`` (comma-separated) scopes the tracker-linked discovery to the work
    items currently shown/selected on the board; without it only app-generated
    test cases are returned.
    """
    tcs = await _all_test_cases(project, _parse_wi_ids(wi_ids))
    out = [
        {
            "index": i,
            "wi_id": str(tc.get("id", "")),
            "title": str(tc.get("title", "Untitled")),
            "step_count": len(tc.get("steps") or []),
            "category": str(tc.get("category", "")),
            "source": str(tc.get("source", "generated")),
            "tc_id": str(tc.get("tc_id", "")),
        }
        for i, tc in enumerate(tcs)
        if str(tc.get("id", "")).strip() and len(tc.get("steps") or []) > 0
    ]
    return {"test_cases": out}


# ---------------------------------------------------------------------
# Environments (masked credentials usable as run targets)
# ---------------------------------------------------------------------
@router.get("/environments/{project}")
@trace
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
@trace
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
    indices: list[int] = []
    wi_ids: list[str] = []


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
    except Exception:  # noqa: BLE001
        pass
    return run.run_id


# ---------------------------------------------------------------------------
# Plan compilation with self-healing retry
# ---------------------------------------------------------------------------
async def _compile_with_healing(
    tc: dict[str, Any],
    candidate: dict[str, Any],
    *,
    cred: Any,
    proj_ctx: Any,
    plan_cache: Path,
    compiler_client: Any,
    compiler_model: str,
    job: Job,
    kb_brief: Any = None,
) -> dict[str, Any]:
    """Compile a single test case plan with retry on failure.

    On first failure: invalidates cache, broadens context, retries.
    Returns the compiled plan dict (with steps) or a stub with plan_error.
    """
    from automation.e2e_plan import PlanValidationError, compile_test_case

    # Per-TC selective project context, enriched with KB brief
    tc_project_ctx = ""
    if kb_brief:
        try:
            tc_project_ctx = kb_brief.to_prompt_section()
        except Exception:  # noqa: BLE001
            pass
    if proj_ctx and not tc_project_ctx:
        try:
            tc_text = f"{tc.get('title', '')} {' '.join(str(s) for s in tc.get('steps', []))}"
            tc_project_ctx = proj_ctx.to_prompt_section_selective(tc_text) or ""
        except Exception:  # noqa: BLE001
            pass

    last_error = ""
    for attempt in range(1, MAX_PLAN_COMPILE_RETRIES + 1):
        try:
            plan = await compile_test_case(
                candidate,
                login_url=cred.login_url,
                username=cred.user_id,
                ai_instructions=cred.ai_instructions,
                cache_dir=plan_cache,
                client=compiler_client,
                model=compiler_model,
                on_log=job.log,
                project_context=tc_project_ctx,
            )
            if attempt > 1:
                job.log(
                    f"[HEAL] Plan compiled on retry {attempt} for "
                    f"'{tc.get('title', 'Untitled')}'."
                )
            return plan.test_case
        except PlanValidationError as exc:
            last_error = str(exc)[:500]
            if attempt < MAX_PLAN_COMPILE_RETRIES:
                job.log(
                    f"[HEAL] Plan compilation failed (attempt {attempt}/"
                    f"{MAX_PLAN_COMPILE_RETRIES}): {last_error[:120]}. "
                    "Retrying with broadened context..."
                )
                # Invalidate the cache so retry gets a fresh LLM call
                import hashlib
                cache_key = hashlib.sha256(
                    json.dumps(
                        {"schema": 3, "test_case": candidate,
                         "login_url": cred.login_url,
                         "ai_instructions": cred.ai_instructions},
                        sort_keys=True, ensure_ascii=True,
                    ).encode()
                ).hexdigest()
                stale = plan_cache / f"{cache_key}.json"
                stale.unlink(missing_ok=True)
                # Broaden: use full (non-selective) KB context on retry
                if proj_ctx:
                    try:
                        broader = proj_ctx.to_prompt_section()
                        if broader and len(broader) > len(tc_project_ctx):
                            tc_project_ctx = broader
                    except Exception:  # noqa: BLE001
                        pass
                await asyncio.sleep(1.0)
            else:
                job.log(
                    f"[ERROR] Plan rejected for '{tc.get('title', 'Untitled')}'"
                    f" after {MAX_PLAN_COMPILE_RETRIES} attempts: {last_error}"
                )
    return {**candidate, "steps": [], "plan_error": last_error}

# ---------------------------------------------------------------------------
# Main E2E execution with self-healing recovery
# ---------------------------------------------------------------------------
async def _run_e2e(job: Job, req: E2EStartRequest) -> None:
    """Background E2E execution with self-healing layers:
    1. Plan compilation: retries with broadened KB context on failure
    2. Per-TC retry: browser-failure TCs are re-run up to MAX_TC_RETRIES
    3. Suite recovery: catastrophic failures trigger diagnose->repair->retry
    """
    try:
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

        all_tcs = await _all_test_cases(req.project, req.wi_ids)
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

        job.set_progress("compiling", 0, len(picked))
        job.log(f"[INFO] Compiling {len(picked)} test case(s) for '{req.env}'.")

        from automation.e2e_plan import PlanValidationError, compile_test_case
        from core.settings_store import build_llm_client
        from core.model_router import Task, route

        compiler_client = build_llm_client()
        compiler_model = route(Task.MAP_EXTRACT)
        plan_cache = ensure_project(req.project).cache_dir / "e2e_plans"

        # Load project context object for per-TC selective injection.
        _proj_ctx = None
        try:
            from core.project_store import read_context_summary
            _proj_ctx = read_context_summary(req.project)
            if _proj_ctx:
                job.log("[INFO] Project KB context loaded for plan compilation.")
        except Exception:  # noqa: BLE001
            pass

        # KB Briefing Engine: build per-WI test briefs for autonomous navigation
        _kb_engine = None
        _wi_briefs: dict[str, Any] = {}
        try:
            from automation.kb_briefing import KBBriefingEngine
            _kb_engine = KBBriefingEngine(req.project, on_log=job.log)
            # Build briefs for all distinct WI ids
            wi_ids_set = {str(tc.get("id", "")) for tc in picked if tc.get("id")}
            for wi_id in wi_ids_set:
                wi_tcs = [tc for tc in picked if str(tc.get("id", "")) == wi_id]
                if wi_tcs:
                    first = wi_tcs[0]
                    brief = _kb_engine.build_brief(
                        wi_id=wi_id,
                        title=str(first.get("title", "")),
                        description=str(first.get("description", "")),
                        acceptance_criteria=str(first.get("acceptance_criteria", "")),
                    )
                    _wi_briefs[wi_id] = brief
            if _wi_briefs:
                job.log(f"[INFO] KB briefs built for {len(_wi_briefs)} work item(s).")
        except Exception:  # noqa: BLE001
            pass

        compiled: list[dict[str, Any]] = []
        for position, tc in enumerate(picked, 1):
            if job.stopped:
                job.state = "stopped"
                job.log("[WARN] E2E run stopped during plan compilation.")
                return
            stable_id = uuid.uuid5(
                uuid.NAMESPACE_URL,
                f"{req.project}:{tc.get('id', '')}:{tc.get('title', '')}",
            ).hex[:16]
            candidate = {**tc, "id": stable_id, "wi_id": str(tc.get("id", ""))}
            plan_result = await _compile_with_healing(
                tc, candidate,
                cred=cred,
                proj_ctx=_proj_ctx,
                plan_cache=plan_cache,
                compiler_client=compiler_client,
                compiler_model=compiler_model,
                job=job,
                kb_brief=_wi_briefs.get(str(tc.get("id", ""))),
            )
            compiled.append(plan_result)
            if job.stopped:
                job.state = "stopped"
                job.log("[WARN] E2E run stopped during plan compilation.")
                return
            job.set_progress("compiling", position, len(picked))

        job.set_progress("running", 0, len(compiled))
        job.log(f"[INFO] Running {len(compiled)} validated result(s) against '{req.env}'.")

        try:
            from automation.e2e_runner import run_e2e_tests, run_e2e_slot
        except Exception as e:  # noqa: BLE001
            job.fail(
                "Playwright is not installed on the agent host. "
                "Run 'pip install playwright' and 'playwright install' to "
                f"enable E2E automation. ({type(e).__name__})"
            )
            return

        output_dir = EXPORTS_DIR / "e2e" / req.project
        output_dir.mkdir(parents=True, exist_ok=True)

        tc_statuses: dict[str, str] = {}

        def _on_tc_done(tc_id: str, status: str) -> None:
            tc_statuses[tc_id] = status
            job.result = {**job.result, "tc_statuses": dict(tc_statuses)}

        def _on_screenshot(path: "Path", step_num: int, status: str) -> None:
            job.log(f"[SCREENSHOT] step={step_num} status={status} path={path}")

        # --- Branch: parallel vs sequential execution ---
        wi_groups = _group_by_wi(compiled)
        use_parallel = len(wi_groups) > 1

        if use_parallel:
            results = await _run_parallel(
                wi_groups=wi_groups,
                cred=cred,
                output_dir=output_dir,
                compiler_client=compiler_client,
                compiler_model=compiler_model,
                job=job,
                tc_statuses=tc_statuses,
                on_tc_done=_on_tc_done,
                on_screenshot=_on_screenshot,
            )
        else:
            results = await _execute_with_recovery(
                compiled=compiled,
                cred=cred,
                output_dir=output_dir,
                compiler_client=compiler_client,
                compiler_model=compiler_model,
                job=job,
                tc_statuses=tc_statuses,
                on_tc_done=_on_tc_done,
                on_screenshot=_on_screenshot,
            )

        if job.stopped:
            job.state = "stopped"
            job.log("[WARN] E2E run stopped by user. Generating partial report.")
            if results:
                try:
                    from automation.report_excel import write_e2e_report
                    rp = output_dir / f"e2e_report_partial_{int(time.time())}.xlsx"
                    await asyncio.to_thread(write_e2e_report, results, rp)
                    job.log(f"[INFO] Partial report saved: {rp.name}")
                    run_id = _persist_run(req.project, results, job.created_at)
                    passed = sum(1 for r in results if r.overall_status == "pass")
                    failed = sum(1 for r in results if r.overall_status in ("fail", "error"))
                    job.finish({
                        "run_id": run_id,
                        "report_path": str(rp),
                        "total": len(results),
                        "passed": passed,
                        "failed": failed,
                        "partial": True,
                    })
                except Exception as e:  # noqa: BLE001
                    job.log(f"[WARN] Partial report generation failed: {e}")
            return

        report_path = ""
        try:
            from automation.report_excel import write_e2e_report
            rp = output_dir / f"e2e_report_{int(time.time())}.xlsx"
            await asyncio.to_thread(write_e2e_report, results, rp)
            report_path = str(rp)
            job.log(f"[INFO] Report saved: {rp.name}")
        except Exception as e:  # noqa: BLE001
            job.log(f"[WARN] Report generation failed: {type(e).__name__}: {e}")

        # Per-WI PDF reports (steps + AI observations)
        pdf_paths: list[str] = []
        try:
            from automation.report_pdf import E2EReportData, generate_e2e_pdf

            # Group results by wi_id for per-WI PDFs
            wi_results: dict[str, list[Any]] = {}
            for r in results:
                wid = getattr(r, "tc_id", "").split("_")[0] if hasattr(r, "tc_id") else "unknown"
                # Use the wi_id from compiled test case if available
                for tc in compiled:
                    if tc.get("id") == getattr(r, "tc_id", ""):
                        wid = tc.get("wi_id", wid)
                        break
                wi_results.setdefault(wid, []).append(r)

            for wid, wi_res in wi_results.items():
                pdf_data = E2EReportData(
                    wi_id=wid,
                    wi_title=getattr(wi_res[0], "title", ""),
                    environment=req.env,
                    started_at=job.created_at,
                    finished_at=time.time(),
                    results=wi_res,
                )
                pdf_path = output_dir / f"e2e_report_WI_{wid}.pdf"
                await asyncio.to_thread(generate_e2e_pdf, pdf_data, pdf_path)
                pdf_paths.append(str(pdf_path))
            if pdf_paths:
                job.log(f"[INFO] {len(pdf_paths)} PDF report(s) generated.")
        except Exception as e:  # noqa: BLE001
            job.log(f"[WARN] PDF report generation failed: {type(e).__name__}: {e}")

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

# ---------------------------------------------------------------------------
# Parallel execution helpers (Phase 1c)
# ---------------------------------------------------------------------------

def _group_by_wi(compiled: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """Group compiled test cases by their wi_id (work item).

    TCs within the same WI run sequentially (share login context);
    different WIs can run in parallel slots.
    """
    groups: dict[str, list[dict[str, Any]]] = {}
    for tc in compiled:
        wi_id = str(tc.get("wi_id", "unknown"))
        groups.setdefault(wi_id, []).append(tc)
    return groups


async def _run_parallel(
    *,
    wi_groups: dict[str, list[dict[str, Any]]],
    cred: Any,
    output_dir: Path,
    compiler_client: Any,
    compiler_model: str,
    job: Job,
    tc_statuses: dict[str, str],
    on_tc_done: Any,
    on_screenshot: Any,
) -> list[Any]:
    """Execute work items in parallel using ParallelRunner.

    Each WI gets its own isolated BrowserContext with independent login.
    TCs within a WI run sequentially on that context's page.
    """
    from automation.e2e_runner import run_e2e_slot
    from automation.parallel_runner import ExecutionSlot, ParallelRunner

    wi_ids = list(wi_groups.keys())
    job.log(f"[INFO] Parallel mode: {len(wi_ids)} work items across isolated contexts.")

    all_results: list[Any] = []

    async def _slot_fn(slot: ExecutionSlot) -> list[Any]:
        """Run one WI's test cases in its assigned slot."""
        tcs = wi_groups[slot.wi_id]
        slot_output = output_dir / slot.wi_id
        slot_output.mkdir(parents=True, exist_ok=True)

        def _slot_log(msg: str) -> None:
            job.log(f"[WI:{slot.wi_id}] {msg}")

        def _slot_stop() -> bool:
            if job.stopped or slot.stopped:
                return True
            wi_stops = (job.result or {}).get("_wi_stops", {})
            return bool(wi_stops.get(slot.wi_id))

        return await run_e2e_slot(
            page=slot.page,
            test_cases=tcs,
            login_url=cred.login_url,
            username=cred.user_id,
            password=cred.password,
            output_dir=slot_output,
            ai_instructions=cred.ai_instructions,
            stop_fn=_slot_stop,
            on_progress=lambda cur, tot: job.set_progress(
                "running", cur, tot
            ),
            on_log=_slot_log,
            on_screenshot=on_screenshot,
            on_tc_done=on_tc_done,
            client=compiler_client,
            model=compiler_model,
        )

    try:
        async with ParallelRunner(output_base=output_dir) as runner:
            # Store runner ref on job for per-WI cancellation
            job.result = {**(job.result or {}), "_runner_ref": id(runner)}

            slot_results = await runner.execute(
                wi_ids=wi_ids,
                run_fn=_slot_fn,
                on_slot_done=lambda sr: job.log(
                    f"[INFO] WI {sr.wi_id}: {'DONE' if sr.success else 'FAILED'}"
                    f"{'' if sr.success else ' - ' + sr.error}"
                ),
            )

        for sr in slot_results:
            if sr.success and sr.result:
                all_results.extend(sr.result)
            elif not sr.success:
                from automation.e2e_runner import StepResult, TestCaseResult
                all_results.append(TestCaseResult(
                    tc_id=f"wi_{sr.wi_id}",
                    title=f"Work Item {sr.wi_id} (slot error)",
                    steps=[StepResult(
                        step_num=0, action="parallel_setup",
                        expected="Slot execution", actual=sr.error,
                        status="error",
                    )],
                    overall_status="error",
                ))
    except Exception as exc:
        job.log(f"[ERROR] Parallel execution failed: {type(exc).__name__}: {exc}")
        from automation.e2e_runner import StepResult, TestCaseResult
        all_results.append(TestCaseResult(
            tc_id="parallel_error",
            title="Parallel runner setup",
            steps=[StepResult(
                step_num=0, action="parallel_setup",
                expected="ParallelRunner started", actual=str(exc)[:300],
                status="error",
            )],
            overall_status="error",
        ))

    return all_results


# ---------------------------------------------------------------------------
# Suite-level recovery helpers
# ---------------------------------------------------------------------------

_BROWSER_ERROR_MARKERS: frozenset[str] = frozenset({
    "timeout", "browser has been closed", "target closed",
    "navigation failed", "net::err_", "session closed",
    "browser disconnected", "crashed",
})


def _is_browser_failure(result: Any) -> bool:
    """True if a TestCaseResult failed due to a transient browser issue."""
    if getattr(result, "overall_status", "") != "error":
        return False
    for step in getattr(result, "steps", []):
        actual = getattr(step, "actual", "").lower()
        if any(marker in actual for marker in _BROWSER_ERROR_MARKERS):
            return True
    return False


def _is_suite_level_failure(results: list[Any], total: int) -> bool:
    """True if the entire suite crashed (>80% errors or zero executed)."""
    if not results:
        return True
    error_count = sum(1 for r in results if getattr(r, "overall_status", "") == "error")
    return error_count > total * 0.8


def _diagnose_suite_failure(results: list[Any]) -> str:
    """Return a short diagnosis from the first error result's step messages."""
    for r in results:
        if getattr(r, "overall_status", "") == "error":
            for step in getattr(r, "steps", []):
                actual = getattr(step, "actual", "")
                if actual:
                    return actual[:200]
    return "Unknown suite-level failure"


# ---------------------------------------------------------------------------
# Suite-level recovery and per-TC retry
# ---------------------------------------------------------------------------
async def _execute_with_recovery(
    *,
    compiled: list[dict[str, Any]],
    cred: Any,
    output_dir: Path,
    compiler_client: Any,
    compiler_model: str,
    job: Job,
    tc_statuses: dict[str, str],
    on_tc_done: Any,
    on_screenshot: Any,
) -> list[Any]:
    """Execute E2E tests with per-TC retry and suite-level recovery.

    Layer 1: Run the full suite.
    Layer 2: If suite catastrophically fails, diagnose and retry once.
    Layer 3: For individual TCs that fail with browser issues, retry up to
             MAX_TC_RETRIES times.
    """
    from automation.e2e_runner import run_e2e_tests

    for suite_attempt in range(1, MAX_SUITE_RECOVERY + 2):
        results = await run_e2e_tests(
            test_cases=compiled,
            login_url=cred.login_url,
            username=cred.user_id,
            password=cred.password,
            output_dir=output_dir,
            ai_instructions=cred.ai_instructions,
            stop_fn=lambda: job.stopped,
            on_progress=lambda cur, tot: job.set_progress("running", cur, tot),
            on_log=lambda msg: job.log(msg),
            on_screenshot=on_screenshot,
            on_tc_done=on_tc_done,
            client=compiler_client,
            model=compiler_model,
        )

        if job.stopped:
            return results

        # --- Suite-level recovery ---
        if _is_suite_level_failure(results, len(compiled)):
            if suite_attempt <= MAX_SUITE_RECOVERY:
                diagnosis = _diagnose_suite_failure(results)
                job.log(
                    f"[HEAL] Suite-level failure detected (attempt {suite_attempt}/"
                    f"{MAX_SUITE_RECOVERY + 1}). Diagnosis: {diagnosis}"
                )
                job.log("[HEAL] Waiting 3s before recovery retry...")
                await asyncio.sleep(3.0)
                job.log("[HEAL] Retrying full suite after recovery pause.")
                continue
            else:
                job.log(
                    "[HEAL] Suite recovery exhausted. Proceeding with partial results."
                )
                break
        else:
            # Suite ran; now retry individual browser-failure TCs
            results = await _retry_browser_failures(
                results=results,
                compiled=compiled,
                cred=cred,
                output_dir=output_dir,
                compiler_client=compiler_client,
                compiler_model=compiler_model,
                job=job,
                tc_statuses=tc_statuses,
                on_tc_done=on_tc_done,
                on_screenshot=on_screenshot,
            )
            break

    return results


async def _retry_browser_failures(
    *,
    results: list[Any],
    compiled: list[dict[str, Any]],
    cred: Any,
    output_dir: Path,
    compiler_client: Any,
    compiler_model: str,
    job: Job,
    tc_statuses: dict[str, str],
    on_tc_done: Any,
    on_screenshot: Any,
) -> list[Any]:
    """Identify TCs that failed due to browser issues and retry them individually."""
    from automation.e2e_runner import run_e2e_tests

    retry_indices: list[int] = []
    for idx, r in enumerate(results):
        if _is_browser_failure(r):
            retry_indices.append(idx)

    if not retry_indices:
        return results

    job.log(
        f"[HEAL] {len(retry_indices)} test case(s) failed with browser issues. "
        f"Retrying (up to {MAX_TC_RETRIES} attempts each)..."
    )

    for retry_round in range(1, MAX_TC_RETRIES + 1):
        if not retry_indices or job.stopped:
            break

        still_failing: list[int] = []
        for idx in retry_indices:
            if job.stopped:
                break
            tc_id = getattr(results[idx], "tc_id", f"TC_{idx}")
            title = getattr(results[idx], "title", "Untitled")
            job.log(
                f"[HEAL] Retry {retry_round}/{MAX_TC_RETRIES} for: {tc_id} - {title}"
            )

            retry_results = await run_e2e_tests(
                test_cases=[compiled[idx]],
                login_url=cred.login_url,
                username=cred.user_id,
                password=cred.password,
                output_dir=output_dir,
                ai_instructions=cred.ai_instructions,
                stop_fn=lambda: job.stopped,
                on_progress=lambda _c, _t: None,
                on_log=lambda msg: job.log(msg),
                on_screenshot=on_screenshot,
                on_tc_done=on_tc_done,
                client=compiler_client,
                model=compiler_model,
            )

            if retry_results and retry_results[0].overall_status == "pass":
                job.log(f"[HEAL] {tc_id} PASSED on retry {retry_round}.")
                results[idx] = retry_results[0]
                tc_statuses[tc_id] = "pass"
            elif retry_results and _is_browser_failure(retry_results[0]):
                still_failing.append(idx)
                job.log(f"[HEAL] {tc_id} still failing (browser issue) on retry {retry_round}.")
            else:
                if retry_results:
                    results[idx] = retry_results[0]
                job.log(f"[HEAL] {tc_id} failed on retry (non-transient). Accepting result.")

        retry_indices = still_failing

    if retry_indices:
        job.log(
            f"[HEAL] {len(retry_indices)} TC(s) still failing after "
            f"{MAX_TC_RETRIES} retries. Accepting failures."
        )

    return results


@router.post("/start")
@trace
async def start_e2e(req: E2EStartRequest) -> dict[str, str]:
    if not req.project:
        raise HTTPException(400, "No project specified")
    if not req.env:
        raise HTTPException(400, "No environment specified")
    job = JOBS.create("e2e", project=req.project)
    job.log("[INFO] Starting E2E automation...")
    spawn_job_task(_run_e2e(job, req), job)
    return {"job_id": job.id}


@router.post("/stop/{job_id}")
@trace
def stop_e2e(job_id: str, wi_id: str = "") -> dict[str, Any]:
    """Signal a running E2E job to stop.

    Without wi_id: stops ALL work items (entire run).
    With wi_id: stops only that work item's slot (others continue).
    """
    job = JOBS.get(job_id)
    if job is None:
        raise HTTPException(404, "Job not found")
    if not wi_id:
        job.stop_event.set()
        return {"stopped": True, "scope": "all"}
    # Per-WI cancellation: signal the specific slot's stop_event
    # The parallel runner's slots check their own stop_event.
    # We store per-WI stop signals in the job's result dict.
    wi_stops: dict[str, bool] = (job.result or {}).get("_wi_stops", {})
    wi_stops[wi_id] = True
    job.result = {**(job.result or {}), "_wi_stops": wi_stops}
    job.log(f"[INFO] Stop requested for WI {wi_id}.")
    return {"stopped": True, "scope": "wi", "wi_id": wi_id}