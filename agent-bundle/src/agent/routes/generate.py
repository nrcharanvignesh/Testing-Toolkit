"""Test-case generation endpoints.

Drives the same pipeline the desktop "Generate test cases" dialog uses, but
as background jobs the browser polls:

    POST /generate/dump     build the work-item dump + system prompt (manual)
    POST /generate/start    start an RLM generation run            -> {job_id}
    POST /generate/push     create the reviewed test cases in ADO  -> {job_id}
    GET  /generate/excel/{job_id}   download the reviewer .xlsx
    GET  /generate/job/{job_id}     poll a job (shared with /jobs)

Long runs report progress + live logs via the shared JobManager.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from agent.jobs import JOBS, Job

router = APIRouter()


# ---------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------
def _require_pat_org() -> tuple[str, str]:
    from core.settings_store import get_setting, load_pat_value, KEY_ORG

    pat = load_pat_value()
    org = get_setting(KEY_ORG)
    if not pat or not org:
        raise HTTPException(400, "PAT or organization not configured")
    return pat, org


def _build_cfg(project: str):
    from core.settings_store import build_runtime_config

    cfg = build_runtime_config()
    cfg.project = project
    return cfg


def _regen_system_prompt(base_prompt: str, feedback: str, payload: dict) -> str:
    """Augment the system prompt so a regeneration applies reviewer feedback
    to an existing payload (mirrors the desktop regen card)."""
    import json as _json

    existing = _json.dumps(payload, indent=None)
    if len(existing) > 40000:
        existing = existing[:40000] + " ...(truncated)"
    return (
        base_prompt
        + "\n\n=== REGENERATION REQUEST ===\n"
        "The reviewer has requested changes to a previously generated set of "
        "test cases. You MUST:\n"
        "1. Apply ALL the reviewer's change instructions below.\n"
        "2. Keep the parts that were already correct.\n"
        "3. Return the COMPLETE corrected payload in the same strict JSON "
        "schema.\n\n"
        "REVIEWER CHANGE INSTRUCTIONS:\n" + feedback.strip() + "\n\n"
        "PREVIOUS PAYLOAD (refine this):\n```json\n" + existing + "\n```"
    )


# ---------------------------------------------------------------------
# Manual-mode dump + prompt
# ---------------------------------------------------------------------
class DumpRequest(BaseModel):
    project: str
    wi_ids: list[int]
    tc_type: str = ""


@router.post("/dump")
async def build_dump(req: DumpRequest) -> dict[str, Any]:
    """Return the work-item text dump + the phase system prompt so the user
    can run an LLM session manually (no API key path)."""
    import core.project_store as ps
    from ado.boards import fetch_details_async
    from testgen.rlm import build_work_item_dump

    pat, org = _require_pat_org()
    cfg = _build_cfg(req.project)
    cfg.pat = pat
    cfg.organization = org
    try:
        details = await fetch_details_async(org, req.project, req.wi_ids, cfg)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"Failed to fetch work item detail: {e}")
    if not details:
        raise HTTPException(404, "No work item detail could be fetched")
    dump = build_work_item_dump(details)
    prompt = ps.read_system_prompt(req.project, req.tc_type or None)
    return {"dump": dump, "system_prompt": prompt, "n_items": len(details)}


# ---------------------------------------------------------------------
# Generation
# ---------------------------------------------------------------------
class StartRequest(BaseModel):
    project: str
    wi_ids: list[int] = []
    tc_type: str = ""
    board: str = ""
    manual_payload: dict[str, Any] | None = None
    regen_feedback: str = ""
    base_payload: dict[str, Any] | None = None
    fast_model: bool = False


def _board_token(board: str) -> str:
    """Filesystem-safe, underscore-free board token for artifact names.

    Underscores are the field delimiters in the generated artifact name
    (review_<phase>_<board>_<stamp>.xlsx), so collapse every run of
    non-alphanumeric characters to a single hyphen. The web Outputs parser
    reverses hyphens back to spaces when it displays "Board Name - timestamp".
    """
    import re

    token = re.sub(r"[^A-Za-z0-9]+", "-", (board or "").strip()).strip("-")
    return token[:60]


async def _run_generate(job: Job, req: StartRequest) -> None:
    import core.project_store as ps
    from ado.boards import fetch_details_async
    from ado.testcase_creator import normalize_payload, validate_payload
    from core.settings_store import build_llm_client, model_pair
    from testgen.gen_cache import GenCache
    from testgen.rlm import (
        StopRequested,
        build_work_item_dump,
        generate_test_cases_rlm_async,
    )
    from testgen.testcase_excel import payload_to_xlsx

    try:
        paths = ps.ProjectPaths.for_name(req.project)
        ps.ensure_project(req.project)

        if req.manual_payload is not None:
            job.log("[INFO] Using manually supplied JSON payload.")
            payload = req.manual_payload
            normalize_payload(payload)
            rep = validate_payload(payload)
            if not rep.ok:
                raise RuntimeError(
                    "Pasted JSON failed validation: "
                    + "; ".join(rep.errors[:8])
                )
        else:
            pat, org = _require_pat_org()
            cfg = _build_cfg(req.project)
            cfg.pat = pat
            cfg.organization = org

            client = build_llm_client(cfg)
            if client is None:
                raise RuntimeError(
                    "No API key configured. Use Manual Mode, or add a key in "
                    "Settings."
                )

            job.log(f"[INFO] Fetching detail for {len(req.wi_ids)} work item(s)...")
            job.set_progress("fetch", 0, len(req.wi_ids))
            details = await fetch_details_async(
                org, req.project, req.wi_ids, cfg,
                on_progress=lambda c, t: job.set_progress("fetch", c, t),
                on_log=job.log,
            )
            if not details:
                raise RuntimeError("No work item detail could be fetched")

            dump = build_work_item_dump(details)
            per_item = [build_work_item_dump([d]) for d in details]

            job.log("[INFO] Loading project knowledge base index...")
            index = await asyncio.to_thread(ps.get_index, req.project)
            retriever = await asyncio.to_thread(
                ps.open_project_retriever, req.project
            )

            primary, fast = model_pair()
            # Fast mode (desktop parity): use the fast model as primary and
            # skip the decompose + verify passes for a much quicker run.
            enable_decompose = not req.fast_model
            enable_verify = not req.fast_model
            if req.fast_model:
                primary = fast
                job.log(f"[INFO] Fast mode: {primary} (no decompose/verify)")
            else:
                job.log(f"[INFO] Model: {primary} (retrieval: {fast})")
            system_prompt = ps.read_system_prompt(
                req.project, req.tc_type or None
            )
            if req.regen_feedback and req.base_payload:
                job.log("[INFO] Applying reviewer feedback (regeneration).")
                system_prompt = _regen_system_prompt(
                    system_prompt, req.regen_feedback, req.base_payload
                )

            cache = GenCache(paths.cache_dir)
            result = await generate_test_cases_rlm_async(
                client=client,
                primary_model=primary,
                fast_model=fast,
                system_prompt=system_prompt,
                kb_index=index,
                work_item_dump=dump,
                per_item_dumps=per_item,
                on_log=job.log,
                on_progress=lambda c, t: job.set_progress("generate", c, t),
                stop_event=job.stop_event,
                cache=cache,
                retriever=retriever,
                enable_decompose=enable_decompose,
                enable_verify=enable_verify,
            )
            if not result.ok or result.payload is None:
                raise RuntimeError(
                    result.parse_error or "Generation produced no valid output"
                )
            payload = result.payload

        # Write the reviewer Excel into the project's generated/ folder.
        # Name: review_<phase>_<board>_<YYYYMMDD_HHMMSS>.xlsx  (board optional)
        # so the web Outputs tab can show "Board Name - timestamp" and offer
        # the regenerate/upload actions (it keys off the review_ prefix).
        paths.generated_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        phase = req.tc_type or "general"
        board_tok = _board_token(req.board)
        fname = (
            f"review_{phase}_{board_tok}_{stamp}.xlsx"
            if board_tok
            else f"review_{phase}_{stamp}.xlsx"
        )
        xlsx_path = paths.generated_dir / fname
        await asyncio.to_thread(payload_to_xlsx, payload, xlsx_path)

        n_tcs = sum(
            len(s.get("test_cases") or [])
            for s in (payload.get("stories") or [])
        )
        job.finish({
            "payload": payload,
            "n_test_cases": n_tcs,
            "n_stories": len(payload.get("stories") or []),
            "xlsx_path": str(xlsx_path),
            "xlsx_name": xlsx_path.name,
        })
        job.log(f"[SUCCESS] Ready for review: {n_tcs} test case(s).")
    except StopRequested:
        job.state = "stopped"
        job.log("[WARN] Generation stopped by user.")
    except Exception as e:  # noqa: BLE001
        job.fail(f"{type(e).__name__}: {e}")
        job.log(f"[ERROR] {job.error}")


@router.post("/start")
async def start_generate(req: StartRequest) -> dict[str, str]:
    if req.manual_payload is None and not req.wi_ids:
        raise HTTPException(400, "No work items selected")
    job = JOBS.create("generate")
    job.log("[INFO] Starting test case generation...")
    asyncio.create_task(_run_generate(job, req))
    return {"job_id": job.id}


# ---------------------------------------------------------------------
# Push reviewed payload to ADO
# ---------------------------------------------------------------------
class PushRequest(BaseModel):
    project: str
    payload: dict[str, Any]
    area_override: str = ""
    iteration_override: str = ""
    inherit_paths: bool = True
    test_category_field: str = "Custom.TestCategory"


async def _run_push(job: Job, req: PushRequest, pat: str, org: str) -> None:
    from ado.testcase_creator import (
        create_test_cases_async,
        normalize_payload,
        validate_payload,
    )

    try:
        payload = req.payload
        normalize_payload(payload)
        rep = validate_payload(payload)
        if not rep.ok:
            raise RuntimeError(
                "Payload failed validation: " + "; ".join(rep.errors[:8])
            )
        cfg = _build_cfg(req.project)
        cfg.pat = pat
        cfg.organization = org
        batch = await create_test_cases_async(
            payload=payload,
            org=org,
            project=req.project,
            pat=pat,
            area_override=req.area_override,
            iteration_override=req.iteration_override,
            inherit_paths=req.inherit_paths,
            test_category_field=req.test_category_field,
            cfg=cfg,
            on_progress=lambda s, c, t: job.set_progress(s, c, t),
            on_log=job.log,
        )
        job.finish({
            "n_ok": batch.n_ok,
            "n_failed": batch.n_failed,
            "created": [
                {
                    "parent_id": r.parent_id,
                    "title": r.title,
                    "created_id": r.created_id,
                    "created_url": r.created_url,
                    "ok": r.ok,
                    "error": r.error,
                }
                for r in batch.files
            ],
        })
    except Exception as e:  # noqa: BLE001
        job.fail(f"{type(e).__name__}: {e}")
        job.log(f"[ERROR] {job.error}")


@router.post("/push")
async def push_test_cases(req: PushRequest) -> dict[str, str]:
    pat, org = _require_pat_org()
    job = JOBS.create("push")
    job.log("[INFO] Creating test cases in Azure DevOps...")
    asyncio.create_task(_run_push(job, req, pat, org))
    return {"job_id": job.id}


# ---------------------------------------------------------------------
# Push from a reviewed Excel on disk (honors reviewer edits)
# ---------------------------------------------------------------------
class PushXlsxRequest(BaseModel):
    project: str
    xlsx_path: str
    area_override: str = ""
    iteration_override: str = ""
    inherit_paths: bool = True
    test_category_field: str = "Custom.TestCategory"


@router.post("/push-xlsx")
async def push_test_cases_from_xlsx(req: PushXlsxRequest) -> dict[str, str]:
    """Re-read a reviewer-edited .xlsx back into a payload and push it to ADO.

    This mirrors the desktop "Push to ADO" button, which re-reads the edited
    review spreadsheet (so Skip=Yes rows and manual title/step edits are
    honored) instead of pushing the originally generated payload.
    """
    from testgen.testcase_excel import ExcelParseError, xlsx_to_payload

    pat, org = _require_pat_org()
    path = Path(req.xlsx_path)
    if not path.exists():
        raise HTTPException(404, f"Reviewed Excel not found: {req.xlsx_path}")
    try:
        payload, _warnings = xlsx_to_payload(path)
    except ExcelParseError as e:
        raise HTTPException(400, f"Could not read reviewed Excel: {e}")
    except Exception as e:  # noqa: BLE001
        raise HTTPException(400, f"Could not read reviewed Excel: {e}")

    push_req = PushRequest(
        project=req.project,
        payload=payload,
        area_override=req.area_override,
        iteration_override=req.iteration_override,
        inherit_paths=req.inherit_paths,
        test_category_field=req.test_category_field,
    )
    job = JOBS.create("push")
    job.log(f"[INFO] Creating test cases from {path.name}...")
    asyncio.create_task(_run_push(job, push_req, pat, org))
    return {"job_id": job.id}


# ---------------------------------------------------------------------
# Load an existing reviewer Excel back into a payload (for regeneration)
# ---------------------------------------------------------------------
class LoadXlsxRequest(BaseModel):
    xlsx_path: str


@router.post("/load-xlsx")
async def load_test_cases_from_xlsx(req: LoadXlsxRequest) -> dict[str, Any]:
    """Read an existing reviewer .xlsx artifact back into a payload.

    Backs the web "Load and Regenerate with feedback" right-click action
    (desktop parity): the UI loads a previously generated artifact and then
    regenerates it with reviewer feedback. The work item ids are recovered
    from each story's parent_work_item_id so the regeneration can re-fetch
    work item detail. Constrained to the workspace so a crafted path cannot
    read arbitrary files off the user's machine.
    """
    from core.app_config import WORKSPACE
    from testgen.testcase_excel import ExcelParseError, xlsx_to_payload

    path = Path(req.xlsx_path).resolve()
    root = Path(WORKSPACE).resolve()
    if root not in path.parents and path != root:
        raise HTTPException(403, "Path outside workspace")
    if not path.exists() or not path.is_file():
        raise HTTPException(404, f"Artifact not found: {req.xlsx_path}")
    try:
        payload, _warnings = xlsx_to_payload(path)
    except ExcelParseError as e:
        raise HTTPException(400, f"Could not read artifact: {e}")
    except Exception as e:  # noqa: BLE001
        raise HTTPException(400, f"Could not read artifact: {e}")

    stories = payload.get("stories") or []
    wi_ids: list[int] = []
    seen: set[int] = set()
    for s in stories:
        sid = s.get("parent_work_item_id")
        if isinstance(sid, int) and sid not in seen:
            seen.add(sid)
            wi_ids.append(sid)
    n_tcs = sum(len(s.get("test_cases") or []) for s in stories)
    return {
        "payload": payload,
        "n_test_cases": n_tcs,
        "n_stories": len(stories),
        "xlsx_path": str(path),
        "xlsx_name": path.name,
        "wi_ids": wi_ids,
    }


# ---------------------------------------------------------------------
# Reviewer Excel download
# ---------------------------------------------------------------------
@router.get("/excel/{job_id}")
async def download_excel(job_id: str) -> FileResponse:
    job = JOBS.get(job_id)
    if job is None:
        raise HTTPException(404, "Job not found")
    path = (job.result or {}).get("xlsx_path")
    if not path or not Path(path).exists():
        raise HTTPException(404, "No reviewer Excel available for this job")
    name = (job.result or {}).get("xlsx_name") or Path(path).name
    return FileResponse(
        path,
        media_type=(
            "application/vnd.openxmlformats-officedocument."
            "spreadsheetml.sheet"
        ),
        filename=name,
    )
