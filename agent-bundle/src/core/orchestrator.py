"""
orchestrator.py
Single async entry that drives extract + package given a RuntimeConfig.

Usage from GUI:
    asyncio.run(run_pipeline(cfg, on_progress=callback))
"""

from __future__ import annotations

import asyncio
import gc
import json
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Final

import re

from ado.extract import (
    ExtractResult,
    extract_many,
    log_error,
    log_info,
    log_success,
)
from kb.bundle import build_kb_bundle, KbBundleResult
from tools.pdf_packager import package_for_wi
from core.runtime_config import RuntimeConfig

MANIFEST_NAME: Final[str] = "manifest.json"


def _title_slug(title: str, max_len: int = 40) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", title.lower()).strip("_")
    return slug[:max_len].rstrip("_") if slug else "untitled"


@dataclass(slots=True)
class PipelineResult:
    extract_results: list[ExtractResult] = field(default_factory=list)
    package_rows: list[dict] = field(default_factory=list)
    manifest_path: Path | None = None
    n_extract_ok: int = 0
    n_package_ok: int = 0
    kb_bundle: KbBundleResult | None = None


def _package_all(
    wi_ids: list[int],
    cfg: RuntimeConfig,
    on_progress: Callable[[str, int, int], None] | None,
) -> list[dict]:
    cfg.output_dir.mkdir(parents=True, exist_ok=True)
    rows: list[dict] = []
    total = len(wi_ids)
    for idx, wid in enumerate(wi_ids, start=1):
        wi_dir = cfg.work_dir / str(wid)
        if not (wi_dir / "_meta.json").exists():
            log_error(f"WI {wid} skipped (no _meta.json - extract failed)")
            rows.append({
                "wi_id": wid, "ok": False, "reason": "extract_missing",
                "packet_pdf": "", "n_pages": 0, "n_items": 0, "n_failed": 0,
            })
            if on_progress:
                on_progress("package", idx, total)
            continue
        meta = json.loads((wi_dir / "_meta.json").read_text(encoding="utf-8"))
        title = meta.get("title", "")
        slug = _title_slug(title)
        out_pdf = cfg.output_dir / f"WI{wid}_{slug}_packet.pdf"
        try:
            res = package_for_wi(wi_dir, cfg.paper_size, out_pdf, organization=cfg.organization)
            rows.append({
                "wi_id": wid, "ok": True, "reason": "",
                "packet_pdf": str(res.output_pdf),
                "n_pages": res.n_pages,
                "n_items": res.n_items,
                "n_failed": res.n_failed,
            })
            log_info(
                f"WI {wid} packaged ({res.n_pages} pages, "
                f"{res.n_failed} failed items)"
            )
        except Exception as e:
            log_error(f"WI {wid} packaging failed: {e!r}")
            rows.append({
                "wi_id": wid, "ok": False, "reason": f"packaging:{e!r}",
                "packet_pdf": "", "n_pages": 0, "n_items": 0, "n_failed": 0,
            })
        finally:
            gc.collect()
        if on_progress:
            on_progress("package", idx, total)
    return rows


def _build_kb(
    package_rows: list[dict],
    cfg: RuntimeConfig,
    on_progress: Callable[[str, int, int], None] | None,
) -> KbBundleResult | None:
    """Merge successful WI PDFs into combined PDF + KB chunk bundle."""
    successful = [r for r in package_rows if r["ok"] and r["packet_pdf"]]
    if not successful:
        return None
    wi_pdfs = [Path(r["packet_pdf"]) for r in successful]
    wi_ids = [r["wi_id"] for r in successful]

    def _log(msg: str) -> None:
        print(msg, flush=True)

    result = build_kb_bundle(
        wi_pdfs=wi_pdfs,
        wi_ids=wi_ids,
        output_dir=cfg.output_dir,
        on_progress=on_progress,
        on_log=_log,
    )
    if result.ok:
        log_success(
            f"KB bundle: {result.n_chunks} chunk(s), "
            f"combined PDF: {result.combined_pdf}"
        )
    else:
        log_error(f"KB bundle failed: {result.error}")
    return result


def _write_manifest(
    cfg: RuntimeConfig,
    extract_results: list[ExtractResult],
    package_rows: list[dict],
) -> Path:
    extract_lookup: dict[int, dict] = {}
    for r in extract_results:
        extract_lookup[int(r.wi_id)] = {
            "extract_ok": bool(r.ok),
            "title": r.title,
            "board_lane": r.board_lane,
            "n_attachments": r.n_attachments,
            "n_comments": r.n_comments,
            "extract_error": r.error,
        }

    merged: list[dict] = []
    for row in package_rows:
        wid = row["wi_id"]
        ext = extract_lookup.get(wid, {})
        merged.append({
            "wi_id": wid,
            "title": ext.get("title", ""),
            "board_lane": ext.get("board_lane", ""),
            "extract_ok": ext.get("extract_ok", False),
            "extract_error": ext.get("extract_error", ""),
            "n_attachments": ext.get("n_attachments", 0),
            "n_comments": ext.get("n_comments", 0),
            "package_ok": row["ok"],
            "package_reason": row["reason"],
            "packet_pdf": row["packet_pdf"],
            "n_pages": row["n_pages"],
            "n_items": row["n_items"],
            "n_failed": row["n_failed"],
            "wi_dir": str(cfg.work_dir / str(wid)),
        })

    out = cfg.output_dir / MANIFEST_NAME
    out.write_text(
        json.dumps(merged, indent=2, ensure_ascii=True), encoding="utf-8"
    )
    return out


async def run_pipeline(
    cfg: RuntimeConfig,
    on_progress: Callable[[str, int, int], None] | None = None,
    *,
    kb_ready: bool = False,
    combined: bool = False,
) -> PipelineResult:
    """End-to-end pipeline. on_progress(stage, current, total)."""
    errors = cfg.validate()
    if errors:
        for e in errors:
            log_error(f"config: {e}")
        raise RuntimeError(f"Invalid config: {errors}")

    cfg.work_dir.mkdir(parents=True, exist_ok=True)
    cfg.output_dir.mkdir(parents=True, exist_ok=True)

    log_info(f"Total unique WIs to process: {len(cfg.work_item_ids)}")

    # ---- Extract stage ----
    completed = {"n": 0}
    total = len(cfg.work_item_ids)

    def _extract_progress(_wi_id: int) -> None:
        completed["n"] += 1
        if on_progress:
            on_progress("extract", completed["n"], total)

    extract_results = await extract_many(cfg, progress_cb=_extract_progress)

    successful_ids = [r.wi_id for r in extract_results if r.ok]
    if not successful_ids:
        log_error("Extraction produced zero successful WIs - aborting packaging")
        return PipelineResult(extract_results=extract_results)

    # ---- Package stage (sequential, CPU-bound) ----
    loop = asyncio.get_event_loop()
    package_rows = await loop.run_in_executor(
        None, _package_all, successful_ids, cfg, on_progress,
    )

    # Append failed-extract rows for completeness
    extracted_set = frozenset(successful_ids)
    for wid in cfg.work_item_ids:
        if wid not in extracted_set:
            package_rows.append({
                "wi_id": wid, "ok": False, "reason": "extract_failed",
                "packet_pdf": "", "n_pages": 0, "n_items": 0, "n_failed": 0,
            })

    # ---- KB bundle stage (only when explicitly requested) ----
    kb_result: KbBundleResult | None = None
    if kb_ready or combined:
        kb_result = await loop.run_in_executor(
            None, _build_kb, package_rows, cfg, on_progress,
        )

    # ---- Internal manifest (not user-facing) ----
    manifest = _write_manifest(cfg, extract_results, package_rows)
    n_pkg_ok = sum(1 for r in package_rows if r["ok"])
    n_ext_ok = len(successful_ids)
    log_success(
        f"Done. Extract ok={n_ext_ok}/{total}. "
        f"Packets ok={n_pkg_ok}/{total}."
    )

    # ---- Cleanup intermediates ----
    if cfg.work_dir.exists():
        shutil.rmtree(cfg.work_dir, ignore_errors=True)
    manifest.unlink(missing_ok=True)
    kb_dir = cfg.output_dir / "Upload to KB"
    if kb_dir.exists() and not kb_ready:
        shutil.rmtree(kb_dir, ignore_errors=True)

    return PipelineResult(
        extract_results=extract_results,
        package_rows=package_rows,
        manifest_path=None,
        n_extract_ok=n_ext_ok,
        n_package_ok=n_pkg_ok,
        kb_bundle=kb_result,
    )
