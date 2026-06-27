"""
combine_pdf.py
Combine multiple files (Office, images, PDFs) into one or more PDFs.

Reuses office_convert (xlsx/docx/csv/rtf/txt -> PDF) and image-to-PDF
conversion. Supports three modes:

    none   -> single combined output
    size   -> split when cumulative size exceeds N MB
    count  -> split into chunks of N items
"""

from __future__ import annotations

import gc
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Final, Literal

from pypdf import PdfReader, PdfWriter

from tools.office_convert import convert_to_pdf, is_office_extension

PDF_EXTS: Final[frozenset[str]] = frozenset({".pdf"})
IMAGE_EXTS: Final[frozenset[str]] = frozenset({
    ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".tiff", ".tif", ".webp",
})

BatchMode = Literal["none", "size", "count"]


@dataclass(slots=True)
class CombineResult:
    output_files: list[Path] = field(default_factory=list)
    n_inputs: int = 0
    n_pages_total: int = 0
    n_failed: int = 0
    failures: list[tuple[str, str]] = field(default_factory=list)


def _safe_name(s: str) -> str:
    bad = '<>:"/\\|?*'
    return "".join("_" if c in bad else c for c in s).strip(". ") or "out"


def _convert_one_to_pdf(
    src: Path, tmp_dir: Path, paper_size: str,
) -> tuple[Path | None, str]:
    """Convert a single input into a PDF located in tmp_dir.

    Returns (pdf_path, error_message). pdf_path is None on failure.
    """
    ext = src.suffix.lower()
    out_pdf = tmp_dir / f"{_safe_name(src.stem)}_{abs(hash(str(src)))}.pdf"
    try:
        if ext in PDF_EXTS:
            return src, ""  # passthrough
        if ext in IMAGE_EXTS:
            from PIL import Image
            with Image.open(src) as im:
                if im.mode in ("RGBA", "LA", "P"):
                    im = im.convert("RGB")
                im.save(out_pdf, "PDF", resolution=150.0)
            return out_pdf, ""
        if is_office_extension(ext):
            status, msg = convert_to_pdf(src, out_pdf, paper_size)
            if status == "FAILED":
                return None, msg
            return out_pdf, msg
        return None, f"Unsupported extension: {ext}"
    except Exception as e:
        return None, f"{type(e).__name__}: {e!r}"


def _pdf_size_bytes(p: Path) -> int:
    try:
        return p.stat().st_size
    except Exception:
        return 0


def _pdf_page_count(p: Path) -> int:
    try:
        return len(PdfReader(str(p)).pages)
    except Exception:
        return 0


def _merge_pdfs(pdfs: list[Path], out_path: Path) -> int:
    writer = PdfWriter()
    total_pages = 0
    for p in pdfs:
        try:
            r = PdfReader(str(p))
            for page in r.pages:
                writer.add_page(page)
                total_pages += 1
        except Exception:
            pass
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("wb") as f:
        writer.write(f)
    return total_pages


def combine_files_to_pdf(
    inputs: list[Path],
    output_dir: Path,
    output_basename: str,
    batch_mode: BatchMode = "none",
    batch_size_mb: float = 50.0,
    batch_count: int = 50,
    paper_size: str = "A4",
    on_progress: Callable[[str, int, int], None] | None = None,
    on_log: Callable[[str], None] | None = None,
) -> CombineResult:
    """Convert and merge inputs into 1 or more PDF outputs."""
    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    result = CombineResult(n_inputs=len(inputs))

    if not inputs:
        return result

    # ---- Stage 1: convert all inputs to PDF (sequential, deterministic) ----
    tmp_root = Path(tempfile.mkdtemp(prefix="combine_pdf_"))
    converted: list[Path] = []
    total = len(inputs)
    for idx, src in enumerate(inputs, start=1):
        if on_progress:
            on_progress("convert", idx - 1, total)
        pdf, err = _convert_one_to_pdf(src, tmp_root, paper_size)
        if pdf is None:
            result.n_failed += 1
            result.failures.append((src.name, err))
            if on_log:
                on_log(f"[ERROR] {src.name}: {err}")
            continue
        converted.append(pdf)
        if on_log:
            on_log(f"[INFO] Converted: {src.name}")
        if on_progress:
            on_progress("convert", idx, total)

    if not converted:
        if on_log:
            on_log("[ERROR] No inputs successfully converted")
        return result

    # ---- Stage 2: batch + merge ----
    base = _safe_name(output_basename) or "combined"

    if batch_mode == "none":
        out = output_dir / f"{base}.pdf"
        if on_progress:
            on_progress("merge", 0, 1)
        n_pages = _merge_pdfs(converted, out)
        if on_progress:
            on_progress("merge", 1, 1)
        result.output_files.append(out)
        result.n_pages_total = n_pages
        if on_log:
            on_log(f"[SUCCESS] Wrote {out.name} ({n_pages} pages)")

    elif batch_mode == "count":
        n = max(1, int(batch_count))
        batches = [converted[i:i + n] for i in range(0, len(converted), n)]
        total_b = len(batches)
        for i, group in enumerate(batches, start=1):
            if on_progress:
                on_progress("merge", i - 1, total_b)
            out = output_dir / f"{base}_part{i:03d}.pdf"
            n_pages = _merge_pdfs(group, out)
            result.output_files.append(out)
            result.n_pages_total += n_pages
            if on_log:
                on_log(
                    f"[SUCCESS] Wrote {out.name} "
                    f"({len(group)} files, {n_pages} pages)"
                )
            if on_progress:
                on_progress("merge", i, total_b)

    elif batch_mode == "size":
        threshold_bytes = max(1, int(batch_size_mb * 1024 * 1024))
        groups: list[list[Path]] = []
        cur: list[Path] = []
        cur_size = 0
        for p in converted:
            sz = _pdf_size_bytes(p)
            if cur and (cur_size + sz) > threshold_bytes:
                groups.append(cur)
                cur = []
                cur_size = 0
            cur.append(p)
            cur_size += sz
        if cur:
            groups.append(cur)
        total_b = len(groups)
        for i, group in enumerate(groups, start=1):
            if on_progress:
                on_progress("merge", i - 1, total_b)
            out = output_dir / f"{base}_part{i:03d}.pdf"
            n_pages = _merge_pdfs(group, out)
            result.output_files.append(out)
            result.n_pages_total += n_pages
            if on_log:
                on_log(
                    f"[SUCCESS] Wrote {out.name} "
                    f"({len(group)} files, {n_pages} pages)"
                )
            if on_progress:
                on_progress("merge", i, total_b)

    # ---- Cleanup intermediate PDFs ----
    for p in converted:
        if p.parent == tmp_root:
            try:
                p.unlink()
            except Exception:
                pass
    try:
        tmp_root.rmdir()
    except Exception:
        pass

    gc.collect()
    return result
