"""
kb_ocr.py
Standardize mixed-format documents to text for indexing, with OCR for
scanned / image-only PDFs - all local and CPU-only.

Strategy (cheapest reliable path first):
  1. Extract the native text layer (kb_store.extract_text handles md/txt/
     html/docx/csv/json and digital PDFs).
  2. For PDFs, decide per the text-density heuristic whether pages are
     image-only (a scan). If so - and a local OCR engine is available -
     OCR those pages and append the recovered text.

OCR engine: RapidOCR (ONNX Runtime) is preferred because it needs no system
binaries (PyInstaller-friendly) and runs on CPU. It is capability-gated: if
not installed, born-digital documents still index normally and scanned PDFs
simply contribute whatever text layer they have (with a warning).

needs_ocr() is pure logic and fully testable without any OCR engine.

ASCII-only; fully type-hinted.
"""

from __future__ import annotations

import gc
from pathlib import Path
from typing import Callable, Final

from kb.store import extract_text

LogFn = Callable[[str], None]

_ocr_engine: object | None = None

# If a PDF page yields fewer than this many extractable characters per page
# on average, treat it as image-only and route it to OCR.
_MIN_CHARS_PER_PAGE: Final[int] = 100
_PDF_SUFFIXES: Final[frozenset[str]] = frozenset({".pdf"})

# Page render resolution for OCR. 300 DPI recovers small UI labels (left-nav
# step names, field captions inside screenshot-style slide pages such as
# "Enhancement - E28.pdf") that are garbled at 150-200 DPI. RapidOCR
# downscales internally and pages are processed one at a time, so peak memory
# stays bounded even on a 4 GB machine.
_OCR_DPI: Final[int] = 300


def _log(on_log: LogFn | None, msg: str) -> None:
    if on_log is not None:
        try:
            on_log(msg)
        except Exception:
            pass


def ocr_available() -> bool:
    try:
        __import__("rapidocr_onnxruntime")
        return True
    except Exception:
        try:
            __import__("rapidocr")
            return True
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


def _ocr_pdf(path: Path, on_log: LogFn | None) -> str:
    """OCR every page of a PDF by rasterizing with pypdf/Pillow is not
    reliable, so we use the OCR engine's own PDF handling when present.
    Returns recovered text ("" if OCR unavailable or fails)."""
    global _ocr_engine, _ocr_init_failed
    if _ocr_init_failed:
        return ""
    try:
        if _ocr_engine is None:
            try:
                from rapidocr_onnxruntime import RapidOCR  # type: ignore
            except Exception:
                from rapidocr import RapidOCR  # type: ignore
            _ocr_engine = RapidOCR()
        engine = _ocr_engine
    except Exception:
        _ocr_init_failed = True
        _log(on_log, "[WARN] OCR engine unavailable; scanned PDF will index "
                     "with its text layer only. Run 'python doctor.py' to "
                     "install/verify the OCR engine (rapidocr-onnxruntime).")
        return ""
    # Stream pages one at a time to avoid OOM on large scanned PDFs.
    parts: list[str] = []
    try:
        import fitz  # type: ignore  # PyMuPDF

        doc = fitz.open(str(path))
        total_pages = len(doc)
        for i, page in enumerate(doc, start=1):
            _log(on_log, f"[INFO]   OCR page {i}/{total_pages} ...")
            try:
                pix = page.get_pixmap(dpi=_OCR_DPI)
                img_bytes = pix.tobytes("png")
                del pix
                result, _elapsed = engine(img_bytes)
                del img_bytes
                if result:
                    parts.append("\n".join(line[1] for line in result if line))
            except Exception:
                continue
        doc.close()
        gc.collect()
        return "\n\n".join(p for p in parts if p.strip())
    except ImportError:
        pass
    except Exception:
        pass

    # Fallback: pdf2image (loads all pages; less memory-efficient)
    try:
        from pdf2image import convert_from_path  # type: ignore

        images = convert_from_path(str(path), dpi=_OCR_DPI)
        total_pages = len(images)
        for i, img in enumerate(images, start=1):
            _log(on_log, f"[INFO]   OCR page {i}/{total_pages} ...")
            try:
                result, _elapsed = engine(img)
                if result:
                    parts.append("\n".join(line[1] for line in result if line))
            except Exception:
                continue
        del images
        gc.collect()
        return "\n\n".join(p for p in parts if p.strip())
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
