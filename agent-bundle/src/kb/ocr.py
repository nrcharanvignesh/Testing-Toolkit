"""
kb_ocr.py
Standardize mixed-format documents to text for indexing, with OCR for
scanned / image-only PDFs via GPT-4o vision API.

Strategy (cheapest reliable path first):
  1. Extract the native text layer (kb_store.extract_text handles md/txt/
     html/docx/csv/json and digital PDFs).
  2. For PDFs, decide per the text-density heuristic whether pages are
     image-only (a scan). If so, rasterize pages locally (PyMuPDF) and
     send each page image to the GPT-4o vision API for text extraction.

All OCR processing goes through the API -- no local ONNX/RapidOCR.
If no API key is configured, born-digital documents still index normally
and scanned PDFs simply contribute whatever text layer they have.

needs_ocr() is pure logic and fully testable without any OCR engine.

ASCII-only; fully type-hinted.
"""

from __future__ import annotations

import gc
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Callable, Final

from core.hardware import optimal_cpu_workers, system_memory_mb
from kb.store import extract_text

LogFn = Callable[[str], None]

# If a PDF page yields fewer than this many extractable characters per page
# on average, treat it as image-only and route it to OCR.
_MIN_CHARS_PER_PAGE: Final[int] = 100
_PDF_SUFFIXES: Final[frozenset[str]] = frozenset({".pdf"})

# Page render resolution for OCR. 300 DPI recovers small UI labels (left-nav
# step names, field captions inside screenshot-style slide pages such as
# "Enhancement - E28.pdf") that are garbled at 150-200 DPI. Pages are
# processed one at a time so peak memory stays bounded even on 4 GB.
_OCR_DPI: Final[int] = 300


def _log(on_log: LogFn | None, msg: str) -> None:
    if on_log is not None:
        try:
            on_log(msg)
        except Exception:
            pass


def ocr_available() -> bool:
    """True when the OCR API is reachable (API key configured)."""
    try:
        from core.app_config import LLM_API_KEY
        from core.settings_store import load_api_key
        key = (load_api_key() or "").strip() or LLM_API_KEY
        return bool(key)
    except Exception:
        return False


def _pdf_text_stats(path: Path) -> tuple[int, int]:
    """Return (n_pages, total_text_chars) using pypdf. (0, 0) on failure."""
    try:
        from pypdf import PdfReader

        reader = PdfReader(str(path))
        pages = reader.pages
        total = 0
        for pg in pages:
            try:
                total += len((pg.extract_text() or ""))
            except Exception:
                continue
        return len(pages), total
    except Exception:
        return 0, 0


def needs_ocr(path: Path | str,
              min_chars_per_page: int = _MIN_CHARS_PER_PAGE) -> bool:
    """True if path is a PDF whose extractable text density is below the
    threshold (i.e. it looks scanned/image-only). Non-PDFs return False."""
    p = Path(path)
    if p.suffix.lower() not in _PDF_SUFFIXES:
        return False
    n_pages, total_chars = _pdf_text_stats(p)
    if n_pages <= 0:
        return False
    return (total_chars / n_pages) < float(min_chars_per_page)


_ocr_init_failed: bool = False


def _ocr_batch_size() -> int:
    """Memory-aware batch size for page processing."""
    mem = system_memory_mb()
    if mem <= 4096:
        return 4
    if mem <= 8192:
        return 8
    return 16


def _ocr_single_image(img_bytes: bytes, page_idx: int) -> tuple[int, str]:
    """OCR a single rasterized page image via GPT-4o vision API.
    Returns (page_index, extracted_text) for ordered reassembly."""
    import base64

    import httpx

    try:
        from core.app_config import LLM_API_KEY, LLM_BASE_URL
        from core.model_router import Task, route
        from core.settings_store import KEY_BASE_URL, get_setting, load_api_key

        api_key = (load_api_key() or "").strip() or LLM_API_KEY
        base_url = (get_setting(KEY_BASE_URL) or LLM_BASE_URL).rstrip("/")
        model = route(Task.OCR_EXTRACT)

        if not api_key:
            return page_idx, ""

        b64 = base64.b64encode(img_bytes).decode("ascii")
        body = {
            "model": model,
            "max_tokens": 4096,
            "temperature": 0.0,
            "messages": [{
                "role": "user",
                "content": [
                    {"type": "text",
                     "text": ("Extract ALL text from this document page image. "
                              "Preserve structure (headings, lists, tables). "
                              "Return ONLY the extracted text, no commentary.")},
                    {"type": "image_url",
                     "image_url": {"url": f"data:image/png;base64,{b64}"}},
                ],
            }],
        }
        headers = {
            "Authorization": f"Bearer {api_key}",
            "x-api-key": api_key,
            "content-type": "application/json",
            "accept": "application/json",
        }
        with httpx.Client(timeout=httpx.Timeout(120.0), verify=False) as client:
            resp = client.post(f"{base_url}/chat/completions",
                               json=body, headers=headers)
        if resp.status_code == 200:
            data = resp.json()
            choices = data.get("choices", [])
            if choices:
                text = choices[0].get("message", {}).get("content", "")
                return page_idx, text.strip()
    except Exception:
        pass
    return page_idx, ""


def _rasterize_page(pdf_path: str, page_idx: int, dpi: int) -> tuple[int, bytes]:
    """Rasterize a single page in its own fitz.Document instance (thread-safe).
    Each thread opens an independent file handle - no shared state.
    Returns (page_index, png_bytes) for ordered reassembly."""
    import fitz  # type: ignore  # PyMuPDF

    doc = fitz.open(pdf_path)
    try:
        page = doc[page_idx]
        pix = page.get_pixmap(dpi=dpi)
        img_bytes: bytes = pix.tobytes("png")
        del pix
        return page_idx, img_bytes
    finally:
        doc.close()


def _ocr_pdf(path: Path, on_log: LogFn | None) -> str:
    """OCR every page of a PDF via GPT-4o vision API.
    Rasterizes pages locally (PyMuPDF), sends each page image to the API.
    Returns recovered text ("" if API unavailable or fails)."""
    global _ocr_init_failed
    if _ocr_init_failed:
        return ""
    if not ocr_available():
        _ocr_init_failed = True
        _log(on_log, "[WARN] OCR API unavailable (no API key); scanned PDF "
                     "will index with its text layer only.")
        return ""

    raster_workers = optimal_cpu_workers()
    # API OCR: limit concurrency to avoid rate-limiting
    api_workers = min(4, optimal_cpu_workers())
    batch_size = _ocr_batch_size()
    parts: list[str] = []

    # --- Primary path: PyMuPDF (fitz) rasterizer ---
    # Each thread opens its own fitz.Document - no shared state, fully parallel.
    try:
        import fitz  # type: ignore  # PyMuPDF

        # Quick open to get page count then close immediately
        doc = fitz.open(str(path))
        total_pages = len(doc)
        doc.close()
        del doc

        _log(on_log, f"[INFO]   OCR (API) {total_pages} page(s), "
                     f"batch={batch_size}...")

        pdf_path_str: str = str(path)

        # Process in batches to bound peak memory
        results: list[tuple[int, str]] = []
        for batch_start in range(0, total_pages, batch_size):
            batch_end = min(batch_start + batch_size, total_pages)
            page_indices = list(range(batch_start, batch_end))

            # Parallel rasterization - each thread opens its own fitz.Document
            batch_images: list[tuple[int, bytes]] = []
            with ThreadPoolExecutor(max_workers=raster_workers) as pool:
                raster_futures = {
                    pool.submit(_rasterize_page, pdf_path_str, i, _OCR_DPI): i
                    for i in page_indices
                }
                for future in as_completed(raster_futures):
                    try:
                        batch_images.append(future.result())
                    except Exception:
                        pass

            # OCR batch via API (limited concurrency for rate-limiting)
            if batch_images:
                with ThreadPoolExecutor(max_workers=api_workers) as pool:
                    ocr_futures = {
                        pool.submit(_ocr_single_image, img, idx): idx
                        for idx, img in batch_images
                    }
                    for future in as_completed(ocr_futures):
                        try:
                            results.append(future.result())
                        except Exception:
                            pass

            _log(on_log, f"[INFO]   OCR batch {batch_start+1}-"
                         f"{batch_end}/{total_pages} done")
            del batch_images
            gc.collect()

        # Reassemble in page order
        results.sort(key=lambda x: x[0])
        parts = [text for _, text in results if text.strip()]
        del results
        gc.collect()
        return "\n\n".join(parts)
    except ImportError:
        pass
    except Exception:
        pass

    # --- Fallback: pdf2image (loads all pages; less memory-efficient) ---
    try:
        from pdf2image import convert_from_path  # type: ignore

        images = convert_from_path(str(path), dpi=_OCR_DPI)
        total_pages = len(images)
        _log(on_log, f"[INFO]   OCR (pdf2image -> API) {total_pages} page(s)...")

        import io as _io

        results_fb: list[tuple[int, str]] = []
        for batch_start in range(0, total_pages, batch_size):
            batch_end = min(batch_start + batch_size, total_pages)
            batch_imgs = [(i, images[i]) for i in range(batch_start, batch_end)]

            with ThreadPoolExecutor(max_workers=api_workers) as pool:
                futures = {}
                for idx, img in batch_imgs:
                    buf = _io.BytesIO()
                    img.save(buf, format="PNG")
                    png_bytes = buf.getvalue()
                    del buf
                    futures[pool.submit(_ocr_single_image, png_bytes, idx)] = idx
                for future in as_completed(futures):
                    try:
                        results_fb.append(future.result())
                    except Exception:
                        pass

            _log(on_log, f"[INFO]   OCR batch {batch_start+1}-"
                         f"{batch_end}/{total_pages} done")
            gc.collect()

        del images
        results_fb.sort(key=lambda x: x[0])
        parts = [text for _, text in results_fb if text.strip()]
        del results_fb
        gc.collect()
        return "\n\n".join(parts)
    except Exception:
        _log(on_log, "[WARN] No PDF rasterizer (PyMuPDF/pdf2image) for "
                     "OCR; skipping image OCR.")
        return ""


def standardize_to_text(path: Path | str, on_log: LogFn | None = None) -> str:
    """Return the best available plain text for a document, applying OCR to
    scanned PDFs when an OCR engine is present. Never raises.

    Multimedia files (image/audio/video) are handled by kb_multimedia and
    should NOT be routed here - extract_text() in kb_store already delegates
    them. This function is for documents only."""
    p = Path(path)

    # Skip multimedia files - they have their own extraction pipeline
    try:
        from kb.multimedia import is_multimedia_file
        if is_multimedia_file(p):
            from kb.multimedia import extract_multimedia_text
            return extract_multimedia_text(p, on_log=on_log)
    except ImportError:
        pass

    try:
        text = extract_text(p)
    except Exception as e:  # noqa: BLE001
        _log(on_log, f"[WARN] Text extraction failed for '{p.name}': {e!r}")
        text = ""
    if p.suffix.lower() in _PDF_SUFFIXES:
        run_ocr = needs_ocr(p)
        if not run_ocr and text.strip():
            # Low-yield check: if file is large but extracted text is tiny,
            # the text layer may be incomplete (partial scans, mixed pages).
            file_kb = p.stat().st_size / 1024
            text_kb = len(text) / 1024
            if file_kb > 200 and text_kb < file_kb * 0.02:
                run_ocr = True
                _log(on_log, f"[INFO] '{p.name}' text yield is low "
                     f"({text_kb:.0f} KB text from {file_kb:.0f} KB file); "
                     f"supplementing with OCR...")
        if run_ocr:
            if ocr_available():
                if not text.strip():
                    _log(on_log,
                         f"[INFO] '{p.name}' looks scanned; running OCR...")
                ocr_text = _ocr_pdf(p, on_log)
                if ocr_text.strip():
                    text = ((text + "\n\n" + ocr_text) if text.strip()
                            else ocr_text)
            else:
                _log(on_log,
                     f"[WARN] '{p.name}' needs OCR but no OCR engine is "
                     f"installed; indexing text layer only.")
    return text
