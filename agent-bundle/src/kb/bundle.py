"""
kb_bundle.py
After per-WI PDFs are produced, build two additional outputs:

1. A single combined PDF of all WI packets (for easy offline reading).
2. An "Upload to KB" folder containing KB-ready chunks sized to fit
   under the LLM's per-file extracted-token cap (~200k tokens -> ~700 KB
   of UTF-8 text per chunk). This folder can be drag-dropped directly
   into an AI Project Knowledge Base without hitting "Uploaded file
   is too large" errors.

Public entry point:
    build_kb_bundle(
        wi_pdfs, output_dir, paper_size, on_progress, on_log,
    ) -> KbBundleResult
"""

from __future__ import annotations

import gc
import json
import re
import uuid
from dataclasses import dataclass, field
from math import ceil
from pathlib import Path
from typing import Callable, Final

from pypdf import PdfReader, PdfWriter

# =====================================================================
# Constants (aligned with pdf_kb_splitter.py sizing)
# =====================================================================

# Per-chunk byte target. ~700 KB of UTF-8 text is roughly 175k tokens,
# well under the LLM's per-file extracted-token cap.
DEFAULT_CHUNK_BYTES: Final[int] = 700 * 1024

# The LLM's per-PDF visual-analysis cap is 100 pages. Image-lookup
# PDFs stay under this.
IMAGES_PER_LOOKUP_PDF: Final[int] = 95

# Warn if a single page exceeds this.
SINGLE_PAGE_WARN_BYTES: Final[int] = 500 * 1024

# Combined PDF naming — UUID suffix differentiates batch runs
COMBINED_PDF_NAME: Final[str] = "combined_{uuid8}.pdf"

# KB bundle folder name
KB_FOLDER_NAME: Final[str] = "Upload to KB"


# =====================================================================
# Result types
# =====================================================================

@dataclass(slots=True)
class ChunkInfo:
    chunk_num: int
    filename: str
    first_page: int
    last_page: int
    image_ids: list[str]
    byte_size: int


@dataclass(slots=True)
class KbBundleResult:
    combined_pdf: Path | None = None
    kb_dir: Path | None = None
    n_chunks: int = 0
    n_images: int = 0
    image_pdfs: list[Path] = field(default_factory=list)
    chunks: list[ChunkInfo] = field(default_factory=list)
    ok: bool = True
    error: str = ""


# ponytail: configurable per-deployment if orgs have very large bundles
MAX_COMBINED_PAGES: Final[int] = 2000

# =====================================================================
# Internal helpers
# =====================================================================

_INVALID_FN = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def _safe_stem(stem: str) -> str:
    s = _INVALID_FN.sub("_", stem).strip()
    return s or "document"


def _merge_pdfs_ordered(
    pdfs: list[Path],
    out_path: Path,
    on_log: Callable[[str], None] | None = None,
) -> int:
    """Merge PDFs in given order with bounded memory. Returns total page count."""
    writer = PdfWriter()
    total: int = 0
    capped: bool = False
    for p in pdfs:
        try:
            reader = PdfReader(str(p))
            for page in reader.pages:
                if total >= MAX_COMBINED_PAGES:
                    capped = True
                    break
                writer.add_page(page)
                total += 1
            del reader
        except Exception:
            pass
        if capped:
            break
    if capped and on_log:
        on_log(f"[WARN] Combined PDF capped at {MAX_COMBINED_PAGES} pages "
               f"to prevent OOM; remaining pages skipped.")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("wb") as f:
        writer.write(f)
    del writer
    gc.collect()
    return total


# =====================================================================
# Image extraction from PDF pages
# =====================================================================

def _extract_images_from_page(page: object) -> list[tuple[bytes, str]]:
    """Return [(bytes, extension), ...] for every image on the page."""
    out: list[tuple[bytes, str]] = []
    try:
        res = page.get("/Resources")
        if res is None:
            return out
        xo = res.get("/XObject") if hasattr(res, "get") else None
        if xo is None:
            return out
        xo_resolved = xo.get_object() if hasattr(xo, "get_object") else xo
        for k in xo_resolved:
            try:
                obj = xo_resolved[k].get_object()
            except Exception:
                continue
            if obj.get("/Subtype") != "/Image":
                continue
            try:
                data = obj.get_data()
            except Exception:
                continue
            f = obj.get("/Filter")
            f_name = ""
            if f is not None:
                try:
                    f_name = f[0] if isinstance(f, list) and f else str(f)
                except Exception:
                    f_name = ""
            f_name = str(f_name)
            if "DCTDecode" in f_name:
                ext = ".jpg"
            elif "JPXDecode" in f_name:
                ext = ".jp2"
            elif "CCITTFaxDecode" in f_name:
                ext = ".tif"
            else:
                ext = ".png"
            out.append((data, ext))
    except Exception:
        pass
    return out


# =====================================================================
# Page-walk: extract text + images from combined PDF
# =====================================================================

def _walk_pages(
    reader: PdfReader,
    collect_images: bool,
    on_progress: Callable[[str, int, int], None] | None,
    on_log: Callable[[str], None] | None,
) -> tuple[list[tuple[int, str, list[str]]], list[tuple[str, bytes, str, int]]]:
    """Walk every page. Returns (page_blocks, images).

    page_blocks: list of (page_num, text_with_inline_refs, ids_on_page)
    images:      list of (img_id, blob, ext, source_page_num)
    """
    n_pages = len(reader.pages)
    page_blocks: list[tuple[int, str, list[str]]] = []
    images: list[tuple[str, bytes, str, int]] = []
    next_img_id = 1

    for i, page in enumerate(reader.pages):
        page_num = i + 1
        if on_progress and i % 50 == 0:
            on_progress("kb_extract", i, n_pages)
        try:
            txt = page.extract_text() or ""
        except Exception:
            txt = ""

        if on_log:
            b = len(txt.encode("utf-8"))
            if b > SINGLE_PAGE_WARN_BYTES:
                on_log(
                    f"[WARN] Page {page_num} is {b / 1024:.0f} KB of "
                    f"text alone; its chunk will exceed the target."
                )

        ids_on_page: list[str] = []
        if collect_images:
            for blob, ext in _extract_images_from_page(page):
                img_id = f"img-{next_img_id:04d}"
                images.append((img_id, blob, ext, page_num))
                ids_on_page.append(img_id)
                next_img_id += 1

        ref_suffix = ""
        if ids_on_page:
            ref_suffix = "\n\n" + " ".join(
                f"[{iid}]" for iid in ids_on_page
            )
        block = f"[Page {page_num}]\n\n{txt.strip()}{ref_suffix}".rstrip()
        page_blocks.append((page_num, block, ids_on_page))

        if i % 100 == 99:
            gc.collect()

    return page_blocks, images


# =====================================================================
# Image-lookup PDF builder
# =====================================================================

def _build_image_lookup_pdfs(
    images: list[tuple[str, bytes, str, int]],
    output_dir: Path,
    stem: str,
    chunks: list[ChunkInfo],
    on_log: Callable[[str], None] | None,
) -> list[Path]:
    """Write image-lookup PDFs, one image per page with id label."""
    if not images:
        return []

    from io import BytesIO

    from PIL import Image as _PILImage
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.utils import ImageReader
    from reportlab.pdfgen import canvas

    img_to_chunk: dict[str, str] = {}
    for c in chunks:
        for iid in c.image_ids:
            img_to_chunk[iid] = c.filename

    n_total = len(images)
    batches: list[list[tuple[str, bytes, str, int]]] = []
    for i in range(0, n_total, IMAGES_PER_LOOKUP_PDF):
        batches.append(images[i:i + IMAGES_PER_LOOKUP_PDF])
    single_pdf = len(batches) == 1

    produced: list[Path] = []
    page_w, page_h = A4
    margin = 36.0
    avail_w = page_w - 2 * margin
    header_h = 56.0
    avail_h = page_h - 2 * margin - header_h

    for batch_idx, batch in enumerate(batches, start=1):
        if single_pdf:
            out_pdf = output_dir / f"{stem}_images.pdf"
        else:
            out_pdf = output_dir / f"{stem}_images_{batch_idx:03d}.pdf"

        c = canvas.Canvas(str(out_pdf), pagesize=A4)

        # Index page
        c.setFont("Helvetica-Bold", 14)
        c.drawString(
            margin, page_h - margin - 4,
            f"{stem} - Image lookup"
            + (f" (batch {batch_idx} of {len(batches)})"
               if not single_pdf else ""),
        )
        c.setFont("Helvetica", 9)
        y = page_h - margin - 24
        c.drawString(margin, y,
                     f"Total images in this PDF: {len(batch)}.")
        y -= 14
        c.drawString(margin, y,
                     "MD chunks reference these via [img-NNNN] tokens.")
        y -= 16
        c.setFont("Helvetica-Bold", 9)
        c.drawString(margin, y, "id")
        c.drawString(margin + 70, y, "from page")
        c.drawString(margin + 170, y, "referenced in chunk")
        y -= 12
        c.setFont("Helvetica", 8)
        for img_id, _blob, _ext, src_page in batch:
            if y < margin + 12:
                c.showPage()
                c.setFont("Helvetica", 8)
                y = page_h - margin
            c.drawString(margin, y, img_id)
            c.drawString(margin + 70, y, str(src_page))
            c.drawString(margin + 170, y,
                         img_to_chunk.get(img_id, "(not referenced)"))
            y -= 11
        c.showPage()

        # One page per image
        for img_id, blob, ext, src_page in batch:
            try:
                pil = _PILImage.open(BytesIO(blob))
                if pil.mode not in ("RGB", "L"):
                    pil = pil.convert("RGB")
                iw, ih = pil.size
                if iw <= 0 or ih <= 0:
                    raise ValueError("zero-sized image")
                buf = BytesIO()
                pil.save(buf, format="PNG")
                buf.seek(0)
                scale = min(avail_w / iw, avail_h / ih, 1.0)
                draw_w = iw * scale
                draw_h = ih * scale
                x = (page_w - draw_w) / 2.0
                y_img = margin + (avail_h - draw_h) / 2.0

                c.setFont("Helvetica-Bold", 12)
                c.drawString(margin, page_h - margin - 12, img_id)
                c.setFont("Helvetica", 9)
                c.drawString(
                    margin, page_h - margin - 28,
                    f"From page {src_page}  |  Chunk: "
                    f"{img_to_chunk.get(img_id, '(n/a)')}",
                )
                c.drawImage(
                    ImageReader(buf), x, y_img,
                    width=draw_w, height=draw_h,
                    preserveAspectRatio=True, mask="auto",
                )
                pil.close()
            except Exception as e:
                c.setFont("Helvetica-Bold", 12)
                c.drawString(margin, page_h - margin - 12, img_id)
                c.setFont("Helvetica", 9)
                c.drawString(
                    margin, page_h - margin - 28,
                    f"(image could not be rendered: {type(e).__name__})",
                )
            c.showPage()

        c.save()
        produced.append(out_pdf)
        if on_log:
            on_log(
                f"[INFO] Wrote {out_pdf.name} ({len(batch)} image(s), "
                f"{out_pdf.stat().st_size / 1024 / 1024:.2f} MB)"
            )

    return produced


# =====================================================================
# Chunk header / README builders
# =====================================================================

def _chunk_header(
    stem: str,
    chunk_num: int,
    total_chunks: int,
    first_page: int,
    last_page: int,
    image_ids: list[str],
    prev_chunk: str | None,
    next_chunk: str | None,
    n_image_pdfs: int,
) -> str:
    lines: list[str] = []
    lines.append(
        f"# {stem} - KB Text Chunk "
        f"{chunk_num:03d} of {total_chunks:03d}"
    )
    lines.append("")
    lines.append(f"**Source:** Combined WI packet PDF  ")
    lines.append(f"**Pages in this chunk:** {first_page} - {last_page}  ")
    if image_ids:
        sample = ", ".join(image_ids[:8])
        more = ("" if len(image_ids) <= 8
                else f", ... ({len(image_ids)} total)")
        lines.append(f"**Images referenced:** {sample}{more}  ")
        if n_image_pdfs == 1:
            lines.append(
                f"**Image lookup:** `{stem}_images.pdf`  "
            )
        elif n_image_pdfs > 1:
            lines.append(
                f"**Image lookup:** `{stem}_images_001.pdf` "
                f"through `{stem}_images_{n_image_pdfs:03d}.pdf`  "
            )
    else:
        lines.append("**Images referenced:** none  ")
    if prev_chunk:
        lines.append(f"**Previous chunk:** `{prev_chunk}`  ")
    if next_chunk:
        lines.append(f"**Next chunk:** `{next_chunk}`  ")
    lines.append("")
    lines.append(
        "Page breaks are marked with `[Page N]`. Image references "
        "use `[img-NNNN]`; to see an image, open the image-lookup "
        "PDF and find the matching id."
    )
    lines.append("")
    lines.append("---")
    lines.append("")
    return "\n".join(lines)


def _readme_text(
    stem: str,
    chunks: list[ChunkInfo],
    n_images: int,
    source_pages: int,
    image_pdfs: list[Path],
    wi_ids: list[int],
) -> str:
    lines: list[str] = []
    lines.append(f"# {stem} - Knowledge Base Bundle")
    lines.append("")
    lines.append(
        f"This bundle was produced by Testing Toolkit from "
        f"{len(wi_ids)} work-item PDF packet(s) "
        f"({source_pages} pages total). Each text chunk is sized "
        f"to fit under the LLM's per-file extracted-token cap, so "
        f"all chunks can be uploaded directly to a Project KB."
    )
    lines.append("")
    lines.append("## How to use this bundle")
    lines.append("")
    lines.append(
        "1. In your AI Project, upload **all the `.md` files** "
        "from this folder."
    )
    if image_pdfs:
        if len(image_pdfs) == 1:
            lines.append(
                f"2. Optionally upload `{image_pdfs[0].name}` so "
                f"the AI can see embedded images."
            )
        else:
            lines.append(
                f"2. Optionally upload all {len(image_pdfs)} "
                f"`{stem}_images_NNN.pdf` files for image context."
            )
    lines.append(
        "3. `index.json` lists every chunk programmatically."
    )
    lines.append("")
    lines.append("## Work items included")
    lines.append("")
    for wid in wi_ids:
        lines.append(f"- WI {wid}")
    lines.append("")
    lines.append("## Bundle contents")
    lines.append("")
    lines.append("| Chunk | File | Pages | Images | Size |")
    lines.append("|------:|------|------:|------:|-----:|")
    for c in chunks:
        size_kb = c.byte_size / 1024
        lines.append(
            f"| {c.chunk_num:03d} | `{c.filename}` | "
            f"{c.first_page}-{c.last_page} | "
            f"{len(c.image_ids)} | {size_kb:.1f} KB |"
        )
    lines.append("")
    if n_images > 0 and image_pdfs:
        pdf_names = ", ".join(f"`{p.name}`" for p in image_pdfs)
        lines.append(
            f"All {n_images} embedded image(s) bundled into: "
            f"{pdf_names}."
        )
    else:
        lines.append("No embedded images were found.")
    lines.append("")
    return "\n".join(lines)


# =====================================================================
# Public entry point
# =====================================================================

def build_kb_bundle(
    wi_pdfs: list[Path],
    wi_ids: list[int],
    output_dir: Path,
    chunk_bytes: int = DEFAULT_CHUNK_BYTES,
    extract_images: bool = True,
    on_progress: Callable[[str, int, int], None] | None = None,
    on_log: Callable[[str], None] | None = None,
) -> KbBundleResult:
    """Merge WI PDFs into one combined PDF and produce a KB-ready
    chunk bundle in an 'Upload to KB' subfolder.

    Args:
        wi_pdfs:   Ordered list of per-WI PDF paths to combine.
        wi_ids:    Corresponding work-item IDs (same order).
        output_dir: Root output folder (where individual WI_*.pdf live).
        chunk_bytes: Target max bytes per text chunk.
        extract_images: Whether to extract and bundle images.
        on_progress: Optional (stage, current, total) callback.
        on_log: Optional log-line callback.

    Returns:
        KbBundleResult with paths to combined PDF and KB folder.
    """
    output_dir = Path(output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    result = KbBundleResult()

    if not wi_pdfs:
        result.ok = False
        result.error = "No WI PDFs to bundle"
        return result

    # ------------------------------------------------------------------
    # Step 1: Merge all WI PDFs into one combined PDF
    # ------------------------------------------------------------------
    combined_path = output_dir / COMBINED_PDF_NAME.format(
        uuid8=uuid.uuid4().hex[:8],
    )
    if on_log:
        on_log(f"[INFO] Merging {len(wi_pdfs)} WI PDF(s) into combined PDF")

    existing = [p for p in wi_pdfs if p.exists()]
    if not existing:
        result.ok = False
        result.error = "None of the WI PDFs exist on disk"
        return result

    n_pages = _merge_pdfs_ordered(existing, combined_path, on_log=on_log)
    result.combined_pdf = combined_path
    if on_log:
        on_log(
            f"[SUCCESS] {combined_path.name}: {n_pages} pages, "
            f"{combined_path.stat().st_size / 1024 / 1024:.2f} MB"
        )

    # ------------------------------------------------------------------
    # Step 2: Extract text + images from combined PDF
    # ------------------------------------------------------------------
    kb_dir = output_dir / KB_FOLDER_NAME
    kb_dir.mkdir(parents=True, exist_ok=True)
    result.kb_dir = kb_dir

    stem = "WI_Bundle"

    if on_log:
        on_log(f"[INFO] Extracting text from {n_pages} page(s)")

    try:
        reader = PdfReader(str(combined_path))
    except Exception as e:
        result.ok = False
        result.error = f"Cannot read combined PDF: {type(e).__name__}: {e}"
        return result

    page_blocks, images = _walk_pages(
        reader,
        collect_images=extract_images,
        on_progress=on_progress,
        on_log=on_log,
    )
    result.n_images = len(images)
    if on_log:
        on_log(f"[INFO] Extracted {len(images)} image(s) from combined PDF")

    # ------------------------------------------------------------------
    # Step 3: Pack pages into chunks (same algorithm as pdf_kb_splitter)
    # ------------------------------------------------------------------
    if on_log:
        on_log(
            f"[INFO] Packing {n_pages} page block(s) into chunks "
            f"of <= {chunk_bytes // 1024} KB"
        )

    chunks_pages: list[list[tuple[int, str, list[str]]]] = []
    current: list[tuple[int, str, list[str]]] = []
    current_size = 0
    header_overhead = 2 * 1024

    for pnum, block, img_ids in page_blocks:
        block_bytes = len(block.encode("utf-8")) + 2
        if current and (
            current_size + block_bytes + header_overhead > chunk_bytes
        ):
            chunks_pages.append(current)
            current = []
            current_size = 0
        current.append((pnum, block, img_ids))
        current_size += block_bytes
    if current:
        chunks_pages.append(current)

    total_chunks = len(chunks_pages)
    if on_log:
        on_log(f"[INFO] Will produce {total_chunks} text chunk(s)")

    n_image_pdfs = 0
    if extract_images and images:
        n_image_pdfs = ceil(len(images) / IMAGES_PER_LOOKUP_PDF)

    # ------------------------------------------------------------------
    # Step 4: Write chunk markdown files
    # ------------------------------------------------------------------
    for idx, page_group in enumerate(chunks_pages, start=1):
        if on_progress:
            on_progress("kb_write", idx - 1, total_chunks)

        first_page = page_group[0][0]
        last_page = page_group[-1][0]
        image_ids_in_chunk: list[str] = []
        for _, _, ids in page_group:
            image_ids_in_chunk.extend(ids)

        prev_chunk = (
            f"{stem}_text_{idx - 1:03d}.md" if idx > 1 else None
        )
        next_chunk = (
            f"{stem}_text_{idx + 1:03d}.md"
            if idx < total_chunks else None
        )
        header = _chunk_header(
            stem=stem,
            chunk_num=idx,
            total_chunks=total_chunks,
            first_page=first_page,
            last_page=last_page,
            image_ids=image_ids_in_chunk,
            prev_chunk=prev_chunk,
            next_chunk=next_chunk,
            n_image_pdfs=n_image_pdfs,
        )
        body = "\n\n".join(b for _, b, _ in page_group)
        chunk_text = header + body + "\n"
        chunk_filename = f"{stem}_text_{idx:03d}.md"
        chunk_path = kb_dir / chunk_filename
        chunk_path.write_text(chunk_text, encoding="utf-8")

        chunk_info = ChunkInfo(
            chunk_num=idx,
            filename=chunk_filename,
            first_page=first_page,
            last_page=last_page,
            image_ids=image_ids_in_chunk,
            byte_size=chunk_path.stat().st_size,
        )
        result.chunks.append(chunk_info)

        if on_log:
            on_log(
                f"[SUCCESS] {chunk_filename}: pages "
                f"{first_page}-{last_page}, "
                f"{len(image_ids_in_chunk)} image ref(s), "
                f"{chunk_path.stat().st_size / 1024:.1f} KB"
            )

    if on_progress:
        on_progress("kb_write", total_chunks, total_chunks)

    result.n_chunks = total_chunks

    # ------------------------------------------------------------------
    # Step 5: Build image-lookup PDFs
    # ------------------------------------------------------------------
    if extract_images and images:
        if on_log:
            on_log(
                f"[INFO] Building image-lookup PDF(s) for "
                f"{len(images)} image(s)"
            )
        try:
            image_pdfs = _build_image_lookup_pdfs(
                images=images,
                output_dir=kb_dir,
                stem=stem,
                chunks=result.chunks,
                on_log=on_log,
            )
        except Exception as e:
            if on_log:
                on_log(
                    f"[WARN] Image lookup PDF failed: "
                    f"{type(e).__name__}: {e}"
                )
            image_pdfs = []
        result.image_pdfs = image_pdfs

    # ------------------------------------------------------------------
    # Step 6: Write index.json and README
    # ------------------------------------------------------------------
    index_data = {
        "source": "Combined WI packets",
        "wi_ids": wi_ids,
        "stem": stem,
        "n_pages": n_pages,
        "n_chunks": total_chunks,
        "n_images": len(images),
        "image_pdfs": [p.name for p in result.image_pdfs],
        "chunks": [
            {
                "chunk_num": c.chunk_num,
                "filename": c.filename,
                "first_page": c.first_page,
                "last_page": c.last_page,
                "image_ids": c.image_ids,
                "byte_size": c.byte_size,
            }
            for c in result.chunks
        ],
    }
    index_path = kb_dir / "index.json"
    index_path.write_text(
        json.dumps(index_data, indent=2), encoding="utf-8"
    )

    readme_path = kb_dir / "README.md"
    readme_path.write_text(
        _readme_text(
            stem, result.chunks, len(images), n_pages,
            result.image_pdfs, wi_ids,
        ),
        encoding="utf-8",
    )

    if on_log:
        on_log(
            f"[SUCCESS] KB bundle ready in '{KB_FOLDER_NAME}/' - "
            f"{total_chunks} chunk(s), {len(images)} image(s), "
            f"{len(result.image_pdfs)} lookup PDF(s)"
        )

    gc.collect()
    return result
