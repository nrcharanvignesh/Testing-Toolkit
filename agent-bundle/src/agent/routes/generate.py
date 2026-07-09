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
import json as _json
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel

from agent.jobs import JOBS, Job

router = APIRouter()


# ---------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------
def _build_cfg(project: str):
    from core.settings_store import build_runtime_config

    cfg = build_runtime_config()
    cfg.project = project
    return cfg


async def _fetch_work_item_dump(
    project: str,
    wi_ids: list[int],
    wi_keys: list[str],
    *,
    on_progress=None,
    on_log=None,
) -> tuple[str, list[str], int]:
    """Fetch work-item detail for ADO ids OR JIRA keys and build the
    generation dump. Returns (combined_dump, per_item_dumps, n_items).

    The source is resolved from the (possibly suffixed) project name so ADO
    and JIRA share one generation pipeline. Project-store keys (KB, prompt,
    outputs) keep the FULL project name, so each source has its own KB.
    """
    from core.source_resolver import resolve
    from core.source_types import SourceType

    rs = resolve(project)

    if rs.source is SourceType.JIRA:
        from core.settings_store import build_jira_runtime_config
        from jira.boards import build_jira_work_item_dump, get_issue_detail

        cfg = build_jira_runtime_config()
        total = len(wi_keys)
        details: list[dict[str, Any]] = []
        for i, key in enumerate(wi_keys):
            if on_progress:
                on_progress(i, total)
            d = await get_issue_detail(rs.url, rs.user, rs.pat, key, cfg)
            if d:
                details.append(d)
            elif on_log:
                on_log(f"[WARN] Could not fetch JIRA issue {key}")
        if on_progress:
            on_progress(total, total)
        if not details:
            return "", [], 0
        combined = build_jira_work_item_dump(details)
        per_item = [build_jira_work_item_dump([d]) for d in details]
        return combined, per_item, len(details)

    # -- ADO (default) --
    from ado.boards import fetch_details_async
    from testgen.rlm import build_work_item_dump

    cfg = _build_cfg(rs.project)
    cfg.pat = rs.pat
    cfg.organization = rs.organization
    details = await fetch_details_async(
        rs.organization, rs.project, wi_ids, cfg,
        on_progress=on_progress,
        on_log=on_log,
    )
    if not details:
        return "", [], 0
    combined = build_work_item_dump(details)
    per_item = [build_work_item_dump([d]) for d in details]
    return combined, per_item, len(details)


def _to_jira_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Transform the generated ADO-style payload into the JIRA upload schema.

    Generated stories carry parent_work_item_id (which, for a JIRA run, is the
    issue KEY the dump was built from). The JIRA creator wants parent_key, so
    map it straight across. Step/tc fields (action/expected/tags/preconditions)
    are identical between the two creators, so no per-step remapping is needed.
    """
    stories_out: list[dict[str, Any]] = []
    for s in payload.get("stories") or []:
        parent = str(
            s.get("parent_work_item_id") or s.get("work_item_id") or ""
        ).strip()
        stories_out.append({
            "parent_key": parent,
            "parent_title": s.get("title", ""),
            "test_cases": s.get("test_cases") or [],
        })
    return {"schema_version": 1, "stories": stories_out}


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
    wi_ids: list[int] = []
    wi_keys: list[str] = []
    tc_type: str = ""


@router.post("/dump")
async def build_dump(req: DumpRequest) -> dict[str, Any]:
    """Return the work-item text dump + the phase system prompt so the user
    can run an LLM session manually (no API key path). Works for both ADO
    (wi_ids) and JIRA (wi_keys) projects."""
    import core.project_store as ps

    try:
        dump, _per, n_items = await _fetch_work_item_dump(
            req.project, req.wi_ids, req.wi_keys,
        )
    except ValueError as e:  # not configured
        raise HTTPException(400, str(e))
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"Failed to fetch work item detail: {e}")
    if not dump:
        raise HTTPException(404, "No work item detail could be fetched")
    prompt = ps.read_system_prompt(req.project, req.tc_type or None)
    return {"dump": dump, "system_prompt": prompt, "n_items": n_items}


# ---------------------------------------------------------------------
# Attachment text extraction (regenerate-with-feedback on web)
# ---------------------------------------------------------------------
# Guard rails so a huge attachment can't blow up the prompt / memory.
_MAX_ATTACH_BYTES = 25 * 1024 * 1024          # 25 MB per file
_MAX_ATTACH_TEXT = 60_000                       # chars kept per file


@router.post("/extract")
async def extract_attachments(
    files: list[UploadFile] = File(...),
) -> dict[str, Any]:
    """Extract plain text from uploaded files so the web "Attach files" control
    in the Regenerate-with-feedback card can fold real document context into a
    regeneration. Reuses the SAME extractor the KB indexer uses, so PDF / DOCX /
    XLSX / PPTX / images (OCR) / legacy office formats are all supported.

    Returns one entry per file: {name, chars, text, truncated, error}. Never
    raises for a single bad file — it reports an empty extraction instead so one
    unreadable attachment doesn't fail the whole batch."""
    import tempfile

    from kb.store import extract_text

    out: list[dict[str, Any]] = []
    for f in files:
        name = f.filename or "attachment"
        suffix = Path(name).suffix.lower()
        try:
            content = await f.read()
        except Exception as e:  # noqa: BLE001
            out.append({"name": name, "chars": 0, "text": "", "error": str(e)})
            continue
        if len(content) > _MAX_ATTACH_BYTES:
            out.append(
                {
                    "name": name,
                    "chars": 0,
                    "text": "",
                    "error": f"File too large ({len(content) // (1024 * 1024)} MB; max 25 MB)",
                }
            )
            continue
        tmp_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
                tmp.write(content)
                tmp_path = Path(tmp.name)
            text = await asyncio.to_thread(extract_text, tmp_path)
        except Exception as e:  # noqa: BLE001
            out.append({"name": name, "chars": 0, "text": "", "error": str(e)})
            continue
        finally:
            if tmp_path is not None:
                try:
                    tmp_path.unlink(missing_ok=True)
                except OSError:
                    pass
        text = (text or "").strip()
        truncated = len(text) > _MAX_ATTACH_TEXT
        if truncated:
            text = text[:_MAX_ATTACH_TEXT] + " ...(truncated)"
        out.append(
            {
                "name": name,
                "chars": len(text),
                "text": text,
                "truncated": truncated,
            }
        )
    return {"files": out}


# ---------------------------------------------------------------------
# Generation
# ---------------------------------------------------------------------
class StartRequest(BaseModel):
    project: str
    wi_ids: list[int] = []
    wi_keys: list[str] = []
    tc_type: str = ""
    board: str = ""
    manual_payload: dict[str, Any] | None = None
    regen_feedback: str = ""
    base_payload: dict[str, Any] | None = None
    fast_model: bool = False
    # Post-processing (desktop parity): pattern-based test-data suggestions
    # appended to data-entry steps. Quality scoring + traceability coverage
    # are always logged and written to the JSON sidecar.
    test_data: bool = True


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
    from ado.testcase_creator import normalize_payload, validate_payload
    from core.app_logging import stream_agent_logs
    from core.settings_store import build_llm_client, model_pair
    from testgen.gen_cache import GenCache
    from testgen.quality_scorer import score_payload
    from testgen.rlm import (
        StopRequested,
        generate_test_cases_rlm_async,
    )
    from testgen.test_data import enrich_payload
    from testgen.testcase_excel import payload_to_xlsx
    from testgen.traceability import build_traceability

    # Mirror the agent's full internal DEBUG logging into the job's live log
    # for the whole run so the Activity Log is verbose (LLM calls, retrieval,
    # HTTP retries, etc.), not just the high-level [INFO] milestones. Attached
    # manually (not via `with`) to avoid re-indenting the whole run body; it is
    # detached in the finally below.
    _log_bridge = stream_agent_logs(job.log)
    _log_bridge.__enter__()
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
            from core.settings_store import build_runtime_config

            client = build_llm_client(build_runtime_config())
            if client is None:
                raise RuntimeError(
                    "No API key configured. Use Manual Mode, or add a key in "
                    "Settings."
                )

            n_expected = len(req.wi_ids) or len(req.wi_keys)
            job.log(f"[INFO] Fetching detail for {n_expected} work item(s)...")
            job.set_progress("fetch", 0, n_expected)
            dump, per_item, _n = await _fetch_work_item_dump(
                req.project, req.wi_ids, req.wi_keys,
                on_progress=lambda c, t: job.set_progress("fetch", c, t),
                on_log=job.log,
            )
            if not dump:
                raise RuntimeError("No work item detail could be fetched")

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
                project_full=req.project,
            )
            if not result.ok or result.payload is None:
                raise RuntimeError(
                    result.parse_error or "Generation produced no valid output"
                )
            payload = result.payload

        # -- Post-processing (desktop parity) --------------------------------
        # 1) Test-data suggestions: enrich_payload reads a flat payload["steps"]
        #    list, but our payload is nested stories[].test_cases[].steps, so
        #    apply it per test case.
        if req.test_data:
            n_enriched = 0
            for story in payload.get("stories") or []:
                for tc in story.get("test_cases") or []:
                    enrich_payload(tc)
                    if any("test_data" in s for s in tc.get("steps") or []):
                        n_enriched += 1
            if n_enriched:
                job.log(
                    f"[INFO] Test data suggestions added to {n_enriched} "
                    "test case(s)."
                )

        # 2) Quality score summary (flatten test cases for the scorer).
        flat_tcs = [
            tc
            for story in (payload.get("stories") or [])
            for tc in (story.get("test_cases") or [])
        ]
        quality = score_payload({"test_cases": flat_tcs})
        job.log(
            f"[INFO] Quality: avg score {quality.avg_score:.0f}/100, "
            f"{quality.below_threshold} TC(s) below threshold."
        )
        for tc, qs in zip(flat_tcs, quality.scores):
            if qs.overall < 60:
                job.log(
                    f"[WARN] Low quality: \"{tc.get('title', 'Untitled')}\" "
                    f"(score {qs.overall})."
                )

        # 3) Traceability coverage. build_traceability keys off work_item_id;
        #    our stories carry parent_work_item_id, so expose both.
        trace_payload = {
            "stories": [
                {
                    "work_item_id": s.get("work_item_id")
                    or s.get("parent_work_item_id", ""),
                    "title": s.get("title", ""),
                    "test_cases": s.get("test_cases") or [],
                }
                for s in (payload.get("stories") or [])
            ]
        }
        matrix = build_traceability(trace_payload)
        summ = matrix.summary
        job.log(
            f"[INFO] Coverage: {summ['covered']}/{summ['total_work_items']} "
            f"work item(s) covered ({summ['coverage_pct']:.0f}%)."
        )
        for item in matrix.items:
            if item.coverage_status == "uncovered":
                job.log(
                    f"[WARN] Uncovered: WI-{item.work_item_id} "
                    f"\"{item.work_item_title}\"."
                )

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

        # JSON sidecar next to the xlsx: flat test cases (for the E2E runner)
        # plus traceability + quality summaries. Best-effort; never fatal.
        try:
            flat_for_sidecar = [
                {
                    "id": str(
                        story.get("work_item_id")
                        or story.get("parent_work_item_id", "")
                    ),
                    "title": tc.get("title", "Untitled"),
                    "steps": tc.get("steps", []),
                    "category": tc.get("category", ""),
                    "priority": tc.get("priority", ""),
                }
                for story in (payload.get("stories") or [])
                for tc in (story.get("test_cases") or [])
            ]
            sidecar = {
                "test_cases": flat_for_sidecar,
                "traceability": {
                    "summary": matrix.summary,
                    "items": [
                        {
                            "work_item_id": it.work_item_id,
                            "title": it.work_item_title,
                            "test_case_ids": it.test_case_ids,
                            "status": it.coverage_status,
                        }
                        for it in matrix.items
                    ],
                },
                "quality": {
                    "avg_score": quality.avg_score,
                    "below_threshold": quality.below_threshold,
                },
            }
            await asyncio.to_thread(
                xlsx_path.with_suffix(".json").write_text,
                _json.dumps(sidecar, indent=2),
                "utf-8",
            )
        except Exception as e:  # noqa: BLE001
            job.log(f"[WARN] Could not write JSON sidecar: {e}")

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
            "quality": {
                "avg_score": quality.avg_score,
                "below_threshold": quality.below_threshold,
            },
            "coverage": matrix.summary,
        })
        job.log(f"[SUCCESS] Ready for review: {n_tcs} test case(s).")
    except StopRequested:
        job.state = "stopped"
        job.log("[WARN] Generation stopped by user.")
    except Exception as e:  # noqa: BLE001
        job.fail(f"{type(e).__name__}: {e}")
        job.log(f"[ERROR] {job.error}")
    finally:
        _log_bridge.__exit__()


@router.post("/start")
async def start_generate(req: StartRequest) -> dict[str, str]:
    if req.manual_payload is None and not req.wi_ids and not req.wi_keys:
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


async def _run_push_jira(job: Job, req: PushRequest, rs) -> None:
    """Create the reviewed test cases as issues in JIRA (Xray if present,
    else standard issues with a step table in the description)."""
    from core.settings_store import build_jira_runtime_config
    from jira.testcase_creator import create_test_cases as jira_create

    jira_payload = _to_jira_payload(req.payload)
    if not any(s.get("test_cases") for s in jira_payload["stories"]):
        raise RuntimeError("Payload contains no test cases to create")
    cfg = build_jira_runtime_config()
    batch = await jira_create(
        payload=jira_payload,
        url=rs.url,
        user=rs.user,
        pat=rs.pat,
        project_key=rs.project,
        cfg=cfg,
        on_progress=lambda s, c, t: job.set_progress(s, c, t),
        on_log=job.log,
    )
    job.finish({
        "n_ok": batch.n_ok,
        "n_failed": batch.n_failed,
        "created": [
            {
                "parent_id": r.parent_key,
                "title": r.title,
                "created_id": r.created_key,
                "created_url": r.created_url,
                "ok": r.ok,
                "error": r.error,
            }
            for r in batch.results
        ],
    })


async def _run_push(job: Job, req: PushRequest) -> None:
    from ado.testcase_creator import (
        create_test_cases_async,
        normalize_payload,
        validate_payload,
    )
    from core.app_logging import stream_agent_logs
    from core.source_resolver import resolve
    from core.source_types import SourceType

    _log_bridge = stream_agent_logs(job.log)
    _log_bridge.__enter__()
    try:
        rs = resolve(req.project)
        if rs.source is SourceType.JIRA:
            await _run_push_jira(job, req, rs)
            return

        payload = req.payload
        normalize_payload(payload)
        rep = validate_payload(payload)
        if not rep.ok:
            raise RuntimeError(
                "Payload failed validation: " + "; ".join(rep.errors[:8])
            )
        cfg = _build_cfg(rs.project)
        cfg.pat = rs.pat
        cfg.organization = rs.organization
        batch = await create_test_cases_async(
            payload=payload,
            org=rs.organization,
            project=rs.project,
            pat=rs.pat,
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
    finally:
        _log_bridge.__exit__()


@router.post("/push")
async def push_test_cases(req: PushRequest) -> dict[str, str]:
    from core.source_resolver import resolve

    try:
        rs = resolve(req.project)
    except ValueError as e:
        raise HTTPException(400, str(e))
    job = JOBS.create("push")
    dest = "JIRA" if rs.source.value == "jira" else "Azure DevOps"
    job.log(f"[INFO] Creating test cases in {dest}...")
    asyncio.create_task(_run_push(job, req))
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
    from core.source_resolver import resolve
    from testgen.testcase_excel import ExcelParseError, xlsx_to_payload

    try:
        resolve(req.project)  # validates the resolved source is configured
    except ValueError as e:
        raise HTTPException(400, str(e))
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
    asyncio.create_task(_run_push(job, push_req))
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

    # Recover parent identifiers so a regeneration can re-fetch detail.
    # ADO ids are integers; JIRA keys are strings (e.g. "PROJ-123"). Return
    # both lists so the caller re-fetches from the correct source.
    stories = payload.get("stories") or []
    wi_ids: list[int] = []
    wi_keys: list[str] = []
    seen_ids: set[int] = set()
    seen_keys: set[str] = set()
    for s in stories:
        sid = s.get("parent_work_item_id")
        if isinstance(sid, bool):
            continue
        if isinstance(sid, int):
            if sid not in seen_ids:
                seen_ids.add(sid)
                wi_ids.append(sid)
        elif isinstance(sid, str) and sid.strip():
            key = sid.strip()
            if key.isdigit():
                n = int(key)
                if n not in seen_ids:
                    seen_ids.add(n)
                    wi_ids.append(n)
            elif key not in seen_keys:
                seen_keys.add(key)
                wi_keys.append(key)
    n_tcs = sum(len(s.get("test_cases") or []) for s in stories)
    return {
        "payload": payload,
        "n_test_cases": n_tcs,
        "n_stories": len(stories),
        "xlsx_path": str(path),
        "xlsx_name": path.name,
        "wi_ids": wi_ids,
        "wi_keys": wi_keys,
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
