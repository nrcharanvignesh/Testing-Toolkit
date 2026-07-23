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
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Final

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from agent.jobs import JOBS, Job, spawn_job_task

# Lock protecting Job.result dict mutations from concurrent threads
_jobs_lock = threading.Lock()
from automation.credential_vault import CredentialVault
from core.app_config import EXPORTS_DIR, OUTPUTS_DIR
from core.trace import trace
from core.project_store import ensure_project

router = APIRouter()
_VAULT = CredentialVault()

# ponytail: compile retries removed in v3.50 (agentic architecture)
# MAX_PLAN_COMPILE_RETRIES, MAX_TC_RETRIES, MAX_SUITE_RECOVERY no longer used


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
    notes: str = ""
    mode: str = "ai"  # "ai" or "script"


def _persist_run(
    project: str,
    results: list[Any],
    started_at: float,
    user_messages: list[str] | None = None,
) -> str:
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
        user_messages=user_messages or [],
    )
    try:
        save_run(project, run)
    except Exception:  # noqa: BLE001
        pass
    return run.run_id



# ponytail: _compile_with_healing removed in v3.50. Agentic runner does not
# compile plans upfront; the LLM drives the browser in real-time.

# ---------------------------------------------------------------------------
# Main E2E execution — agentic LLM-in-the-loop architecture (v3.50)
# ---------------------------------------------------------------------------
async def _run_e2e(job: Job, req: E2EStartRequest) -> None:
    """Background E2E execution using the agentic architecture.

    The LLM drives the browser in real-time: observe page -> decide action ->
    execute -> repeat. No upfront plan compilation. Sonnet first, Opus fallback
    on stuck.
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

        from core.settings_store import build_llm_client
        from core.model_router import Task, route

        llm_client = build_llm_client()
        model = route(Task.E2E_AGENTIC)
        fallback_model = route(Task.E2E_AGENTIC_FALLBACK)

        # Load project context for system prompt grounding
        project_context = ""
        try:
            from core.project_store import read_context_summary
            _proj_ctx = read_context_summary(req.project)
            if _proj_ctx:
                project_context = _proj_ctx.to_prompt_section_selective(
                    " ".join(tc.get("title", "") for tc in picked)
                ) or ""
                if project_context:
                    job.log("[INFO] Project KB context loaded.")
        except Exception:  # noqa: BLE001
            pass

        # Append user-provided pre-run notes to project context
        if req.notes.strip():
            project_context += (
                f"\n\n[USER NOTES]: {req.notes.strip()}"
            )
            job.log("[INFO] User notes attached to run context.")

        # KB Briefing Engine: build per-WI test briefs
        _wi_briefs: dict[str, Any] = {}
        try:
            from automation.kb_briefing import KBBriefingEngine
            _kb_engine = KBBriefingEngine(req.project, on_log=job.log)
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

        # Assign stable IDs to test cases
        prepared: list[dict[str, Any]] = []
        for tc in picked:
            stable_id = uuid.uuid5(
                uuid.NAMESPACE_URL,
                f"{req.project}:{tc.get('id', '')}:{tc.get('title', '')}",
            ).hex[:16]
            prepared.append({**tc, "tc_id": stable_id, "wi_id": str(tc.get("id", ""))})

        job.set_progress("running", 0, len(prepared))
        job.log(
            f"[INFO] Agentic E2E: {len(prepared)} test case(s) against '{req.env}' "
            f"(model={model}, fallback={fallback_model})."
        )

        try:
            from automation.playwright_bridge import browser_session
            from automation.agentic_runner import run_agentic_suite, AgenticConfig
        except Exception as e:  # noqa: BLE001
            job.fail(
                "Playwright or agentic runner not available. "
                f"({type(e).__name__}: {e})"
            )
            return

        output_dir = EXPORTS_DIR / "e2e" / req.project
        output_dir.mkdir(parents=True, exist_ok=True)

        tc_statuses: dict[str, str] = {}

        def _on_tc_done(result: Any) -> None:
            with _jobs_lock:
                tc_statuses[result.tc_id] = result.overall_status
                job.result = {**(job.result or {}), "tc_statuses": dict(tc_statuses)}

        # --- Script-based rerun mode: run existing scripts with AI oversight ---
        if req.mode == "script":
            from automation.script_runner import ScriptRunner

            runner = ScriptRunner(
                llm_client=llm_client,
                model=model,
                project_context=project_context,
                output_dir=output_dir,
                on_log=job.log,
                stop_fn=lambda: job.stopped,
            )
            job.log("[INFO] Script-based rerun mode: executing replay scripts with AI oversight.")

            async with browser_session(output_dir=output_dir, maximized=True) as (
                context,
                page,
            ):
                await page.goto(cred.login_url, timeout=30000)
                await page.wait_for_load_state("domcontentloaded")

                results = await runner.run_suite(
                    test_cases=prepared,
                    credentials=cred,
                    page=page,
                    context=context,
                    on_progress=lambda cur, tot: job.set_progress("running", cur, tot),
                    on_tc_done=_on_tc_done,
                    user_messages=job.drain_user_messages,
                )
        else:
            # --- Full AI agentic mode ---
            async with browser_session(output_dir=output_dir, maximized=True) as (
                context,
                page,
            ):
                await page.goto(cred.login_url, timeout=30000)
                await page.wait_for_load_state("domcontentloaded")

                _kb_engine_ref = locals().get("_kb_engine")

                results = await run_agentic_suite(
                    test_cases=prepared,
                    credentials=cred,
                    briefs=_wi_briefs,
                    project_context=project_context,
                    llm_client=llm_client,
                    model=model,
                    fallback_model=fallback_model,
                    output_dir=output_dir,
                    page=page,
                    context=context,
                    config=AgenticConfig(),
                    stop_fn=lambda: job.stopped,
                    on_progress=lambda cur, tot: job.set_progress("running", cur, tot),
                    on_log=job.log,
                    on_tc_done=_on_tc_done,
                    kb_engine=_kb_engine_ref,
                    input_queue=job.drain_user_messages,
                )

        if job.stopped:
            job.state = "stopped"
            job.log("[WARN] E2E run stopped by user. Generating partial report.")
            if results:
                try:
                    from automation.report_excel import write_e2e_report
                    rp = output_dir / f"{req.project}_e2e_report_partial_{int(time.time())}.xlsx"
                    await asyncio.to_thread(write_e2e_report, results, rp)
                    job.log(f"[INFO] Partial report saved: {rp.name}")
                    run_id = _persist_run(req.project, results, job.created_at, job.user_messages)
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
            rp = output_dir / f"{req.project}_e2e_report_{int(time.time())}.xlsx"
            await asyncio.to_thread(write_e2e_report, results, rp)
            report_path = str(rp)
            job.log(f"[INFO] Report saved: {rp.name}")
        except Exception as e:  # noqa: BLE001
            job.log(f"[WARN] Report generation failed: {type(e).__name__}: {e}")

        # Report Synthesizer Sub-Agent: produce human-readable narrative
        narrative_report = None
        try:
            from automation.agentic_prompt import (
                REPORT_SYNTHESIZER_SYSTEM_PROMPT,
                build_synthesizer_user_message,
            )
            from automation.sub_agents import parse_narrative_report
            from core.model_router import Task, route

            synth_model = route(Task.E2E_REPORT_SYNTH)
            total_duration = sum(getattr(r, "duration_ms", 0) for r in results)
            synth_msg = build_synthesizer_user_message(results, total_duration)
            synth_result = await llm_client.complete_async(
                model=synth_model,
                system=REPORT_SYNTHESIZER_SYSTEM_PROMPT,
                user=synth_msg,
                max_tokens=4096,
                temperature=0.3,
            )
            narrative_report = parse_narrative_report(synth_result)
            if narrative_report:
                job.log("[INFO] Report narrative synthesized.")
        except Exception as e:  # noqa: BLE001
            job.log(f"[WARN] Report synthesis failed (non-fatal): {e}")

        # Per-WI PDF reports (with narrative sections)
        pdf_paths: list[str] = []
        try:
            from automation.report_pdf import E2EReportData, generate_e2e_pdf

            # Group results by wi_id for per-WI PDFs
            wi_results: dict[str, list[Any]] = {}
            for r in results:
                wid = getattr(r, "tc_id", "").split("_")[0] if hasattr(r, "tc_id") else "unknown"
                # Use the wi_id from prepared test case if available
                for tc in prepared:
                    if tc.get("tc_id") == getattr(r, "tc_id", ""):
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
                    narrative=narrative_report,
                )
                pdf_path = output_dir / f"e2e_report_WI_{wid}.pdf"
                await asyncio.to_thread(generate_e2e_pdf, pdf_data, pdf_path)
                pdf_paths.append(str(pdf_path))
            if pdf_paths:
                job.log(f"[INFO] {len(pdf_paths)} PDF report(s) generated.")
        except Exception as e:  # noqa: BLE001
            job.log(f"[WARN] PDF report generation failed: {type(e).__name__}: {e}")

        # ---- Post-suite video rename/remux to MKV ----
        try:
            from automation.artifact_collector import _remux_to_mkv, _safe_filename

            for webm in output_dir.rglob("*.webm"):
                tc_folder = webm.parent.parent.name
                safe_name = f"{req.project}_{tc_folder}_recording"
                mkv_dest = webm.parent / f"{_safe_filename(safe_name)}.mkv"
                if not _remux_to_mkv(webm, mkv_dest):
                    renamed = webm.parent / f"{_safe_filename(safe_name)}.webm"
                    if renamed != webm:
                        webm.rename(renamed)
            job.log("[INFO] Video files renamed/remuxed.")
        except Exception as e:  # noqa: BLE001
            job.log(f"[WARN] Video rename/remux failed (non-fatal): {e}")

        run_id = _persist_run(req.project, results, job.created_at, job.user_messages)

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


# ponytail: parallel execution removed in v3.50; agentic runner handles
# Sonnet->Opus fallback internally. Parallel slots will be re-added via
# run_agentic_slot when multi-WI parallel agentic execution is validated.


@router.post("/start")
@trace
async def start_e2e(req: E2EStartRequest) -> dict[str, str]:
    if not req.project:
        raise HTTPException(400, "No project specified")
    if not req.env:
        raise HTTPException(400, "No environment specified")
    # AUDIT-030: reject if an E2E job is already running
    existing = JOBS.find_active("e2e", req.project)
    if existing is not None:
        raise HTTPException(409, "An E2E run is already in progress for this project.")
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
    with _jobs_lock:
        wi_stops: dict[str, bool] = (job.result or {}).get("_wi_stops", {})
        wi_stops[wi_id] = True
        job.result = {**(job.result or {}), "_wi_stops": wi_stops}
    job.log(f"[INFO] Stop requested for WI {wi_id}.")
    return {"stopped": True, "scope": "wi", "wi_id": wi_id}