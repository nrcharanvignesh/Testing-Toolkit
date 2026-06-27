"""
kb_multimedia.py
Subprocess-isolated multimedia (image, audio, video) text extraction for
knowledge-base indexing. Stability-first: every extraction runs in a
separate process so a crash, OOM, or hang in an external library (ffmpeg,
whisper, Tesseract, etc.) NEVER brings down the main application.

Design principles (U-series laptop target):
  * One file at a time, sequential, never parallel.
  * Each extraction spawns a short-lived subprocess with a hard timeout.
  * Memory: subprocess dies after each file -> OS reclaims all memory.
  * Progress: sub-file progress for large video/audio (per-segment).
  * Graceful degradation: if no backend is installed, return "" with a
    warning log. The index still builds; multimedia just contributes no
    text chunks.

Supported formats:
  IMAGE: .png .jpg .jpeg .gif .bmp .tiff .tif .webp .svg .ico .heic .heif
         -> OCR (Tesseract via pytesseract, or RapidOCR) for text in images
         -> EXIF/metadata extraction for context

  AUDIO: .mp3 .wav .ogg .flac .m4a .wma .aac .opus .aiff
         -> Speech-to-text via whisper (openai-whisper or faster-whisper)
         -> Processes in 30-second segments for progress + memory control

  VIDEO: .mp4 .mkv .avi .mov .webm .wmv .flv .m4v .mpg .mpeg .3gp
         -> Audio track extraction via ffmpeg (subprocess)
         -> Speech-to-text on extracted audio (same whisper pipeline)
         -> Keyframe OCR for on-screen text (optional, capped at N frames)

Public API:
    extract_multimedia_text(path, on_progress, on_log) -> str
    multimedia_capabilities() -> dict[str, bool]
    is_multimedia_file(path) -> bool
    MULTIMEDIA_EXTENSIONS -> frozenset[str]
"""

from __future__ import annotations

import gc
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Callable, Final

LogFn = Callable[[str], None]
SubProgressFn = Callable[[str, int, int], None]

# ---------------------------------------------------------------------
# Extension sets
# ---------------------------------------------------------------------
IMAGE_EXTENSIONS: Final[frozenset[str]] = frozenset({
    ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".tiff", ".tif",
    ".webp", ".svg", ".ico", ".heic", ".heif",
})

AUDIO_EXTENSIONS: Final[frozenset[str]] = frozenset({
    ".mp3", ".wav", ".ogg", ".flac", ".m4a", ".wma", ".aac",
    ".opus", ".aiff",
})

VIDEO_EXTENSIONS: Final[frozenset[str]] = frozenset({
    ".mp4", ".mkv", ".avi", ".mov", ".webm", ".wmv", ".flv",
    ".m4v", ".mpg", ".mpeg", ".3gp",
})

MULTIMEDIA_EXTENSIONS: Final[frozenset[str]] = (
    IMAGE_EXTENSIONS | AUDIO_EXTENSIONS | VIDEO_EXTENSIONS
)

# Timeouts (seconds) - generous for U-series CPUs
_IMAGE_TIMEOUT: Final[int] = 60
_AUDIO_SEGMENT_TIMEOUT: Final[int] = 120
_VIDEO_AUDIO_EXTRACT_TIMEOUT: Final[int] = 300
_KEYFRAME_TIMEOUT: Final[int] = 180

# Audio segmentation for progress + memory control
_AUDIO_SEGMENT_SECONDS: Final[int] = 30

# Video keyframe cap (OCR at most this many frames)
_MAX_KEYFRAMES: Final[int] = 20


def _log(on_log: LogFn | None, msg: str) -> None:
    if on_log is not None:
        try:
            on_log(msg)
        except Exception:
            pass


def is_multimedia_file(path: Path | str) -> bool:
    return Path(path).suffix.lower() in MULTIMEDIA_EXTENSIONS


def is_image_file(path: Path | str) -> bool:
    return Path(path).suffix.lower() in IMAGE_EXTENSIONS


def is_audio_file(path: Path | str) -> bool:
    return Path(path).suffix.lower() in AUDIO_EXTENSIONS


def is_video_file(path: Path | str) -> bool:
    return Path(path).suffix.lower() in VIDEO_EXTENSIONS


# ---------------------------------------------------------------------
# Capability detection (cached)
# ---------------------------------------------------------------------
_capabilities_cache: dict[str, bool] | None = None
_rapidocr_engine: object | None = None
_ffmpeg_path: str | None = None


def _resolve_ffmpeg() -> str | None:
    """Find ffmpeg binary: system PATH first, then imageio_ffmpeg bundle."""
    global _ffmpeg_path
    if _ffmpeg_path is not None:
        return _ffmpeg_path if _ffmpeg_path else None
    import shutil
    path = shutil.which("ffmpeg")
    if path:
        _ffmpeg_path = path
        return path
    try:
        import imageio_ffmpeg
        path = imageio_ffmpeg.get_ffmpeg_exe()
        if path and os.path.isfile(path):
            _ffmpeg_path = path
            return path
    except Exception:
        pass
    _ffmpeg_path = ""
    return None


def multimedia_capabilities() -> dict[str, bool]:
    """Check which multimedia backends are available. Cached after first call."""
    global _capabilities_cache
    if _capabilities_cache is not None:
        return dict(_capabilities_cache)

    caps: dict[str, bool] = {
        "image_ocr": False,
        "audio_whisper": False,
        "video_ffmpeg": False,
        "video_keyframe_ocr": False,
    }

    # Image OCR: pytesseract or RapidOCR
    try:
        __import__("pytesseract")
        caps["image_ocr"] = True
    except ImportError:
        try:
            __import__("rapidocr_onnxruntime")
            caps["image_ocr"] = True
        except ImportError:
            try:
                __import__("rapidocr")
                caps["image_ocr"] = True
            except ImportError:
                pass

    # Audio: whisper (openai-whisper or faster-whisper)
    try:
        __import__("faster_whisper")
        caps["audio_whisper"] = True
    except ImportError:
        try:
            __import__("whisper")
            caps["audio_whisper"] = True
        except ImportError:
            pass

    # Video: ffmpeg binary (PATH or bundled via imageio_ffmpeg)
    ffmpeg = _resolve_ffmpeg()
    if ffmpeg:
        try:
            result = subprocess.run(
                [ffmpeg, "-version"],
                capture_output=True, timeout=10,
            )
            caps["video_ffmpeg"] = result.returncode == 0
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
            pass

    # Video keyframe OCR reuses image_ocr capability
    caps["video_keyframe_ocr"] = caps["image_ocr"] and caps["video_ffmpeg"]

    _capabilities_cache = caps
    return dict(caps)


def reset_capabilities_cache() -> None:
    global _capabilities_cache
    _capabilities_cache = None


# ---------------------------------------------------------------------
# Image extraction (OCR + metadata)
# ---------------------------------------------------------------------
def _extract_image_metadata(path: Path) -> str:
    """Extract EXIF/metadata as context text. Never raises."""
    parts: list[str] = []
    try:
        from PIL import Image
        from PIL.ExifTags import TAGS

        with Image.open(str(path)) as img:
            parts.append(f"[Image: {img.format} {img.size[0]}x{img.size[1]} {img.mode}]")
            exif = img.getexif()
            if exif:
                for tag_id, value in exif.items():
                    tag = TAGS.get(tag_id, str(tag_id))
                    if tag in ("ImageDescription", "UserComment", "XPComment",
                               "XPTitle", "XPSubject", "XPKeywords"):
                        val_str = str(value).strip()
                        if val_str and val_str != "0":
                            parts.append(f"{tag}: {val_str}")
    except Exception:
        pass
    return "\n".join(parts)


def _extract_image_ocr(path: Path, on_log: LogFn | None) -> str:
    """OCR text from an image file. Runs in-process but with memory guard."""
    # Try pytesseract first (subprocess-based, naturally isolated)
    try:
        import pytesseract
        from PIL import Image

        with Image.open(str(path)) as img:
            if img.mode not in ("RGB", "L"):
                img = img.convert("RGB")
            text = pytesseract.image_to_string(img, timeout=_IMAGE_TIMEOUT)
            return text.strip()
    except ImportError:
        pass
    except Exception as e:
        _log(on_log, f"[WARN] pytesseract failed on '{path.name}': {e!r}")

    # Fallback: RapidOCR (cached engine avoids model reload per image)
    try:
        try:
            from rapidocr_onnxruntime import RapidOCR
        except ImportError:
            from rapidocr import RapidOCR

        global _rapidocr_engine
        if _rapidocr_engine is None:
            _rapidocr_engine = RapidOCR()
        result, _elapsed = _rapidocr_engine(str(path))
        if result:
            text = "\n".join(line[1] for line in result if line)
            return text.strip()
    except Exception as e:
        _log(on_log, f"[WARN] RapidOCR failed on '{path.name}': {e!r}")

    return ""


def _extract_image(
    path: Path,
    on_log: LogFn | None = None,
    on_sub_progress: SubProgressFn | None = None,
) -> str:
    """Full image text extraction: metadata + OCR."""
    caps = multimedia_capabilities()
    parts: list[str] = []

    metadata = _extract_image_metadata(path)
    if metadata:
        parts.append(metadata)

    if caps["image_ocr"]:
        if on_sub_progress:
            on_sub_progress("OCR", 0, 1)
        ocr_text = _extract_image_ocr(path, on_log)
        if ocr_text:
            parts.append(ocr_text)
        if on_sub_progress:
            on_sub_progress("OCR", 1, 1)
    else:
        _log(on_log, f"[INFO] No OCR backend for image '{path.name}'; "
                     "indexing metadata only.")

    gc.collect()
    return "\n\n".join(parts)


# ---------------------------------------------------------------------
# Audio extraction (speech-to-text)
# ---------------------------------------------------------------------
def _get_audio_duration(path: Path) -> float:
    """Get audio duration in seconds using ffmpeg -i (no ffprobe needed)."""
    ffmpeg = _resolve_ffmpeg()
    if not ffmpeg:
        return 0.0
    try:
        result = subprocess.run(
            [ffmpeg, "-i", str(path), "-hide_banner"],
            capture_output=True, text=True, timeout=30,
        )
        # ffmpeg -i prints duration to stderr even on "error" exit
        import re
        m = re.search(r"Duration:\s*(\d+):(\d+):(\d+)\.(\d+)", result.stderr)
        if m:
            h, mi, s, cs = int(m.group(1)), int(m.group(2)), int(m.group(3)), int(m.group(4))
            return h * 3600 + mi * 60 + s + cs / 100.0
    except (FileNotFoundError, subprocess.TimeoutExpired, ValueError, OSError):
        pass
    return 0.0


def _transcribe_audio_whisper(
    path: Path,
    on_log: LogFn | None = None,
    on_sub_progress: SubProgressFn | None = None,
) -> str:
    """Transcribe audio using faster-whisper or openai-whisper.
    Processes in segments for progress reporting on U-series CPUs."""

    # Try faster-whisper first (more memory efficient, CPU-friendly)
    try:
        from faster_whisper import WhisperModel

        device = "cpu"
        compute = "int8"
        try:
            from core.hardware import gpu_available
            if gpu_available():
                device = "cuda"
                compute = "float16"
        except Exception:
            pass
        _log(on_log, f"[INFO] Transcribing '{path.name}' with faster-whisper ({device})...")
        model = WhisperModel("base", device=device, compute_type=compute)
        segments, info = model.transcribe(
            str(path),
            beam_size=1,
            language=None,
            vad_filter=True,
        )

        total_duration = info.duration if info.duration else _get_audio_duration(path)
        parts: list[str] = []
        last_progress = 0

        for segment in segments:
            parts.append(segment.text.strip())
            if on_sub_progress and total_duration > 0:
                current = int(segment.end)
                total = int(total_duration)
                if current != last_progress:
                    on_sub_progress("Transcribe", current, total)
                    last_progress = current

        if on_sub_progress:
            on_sub_progress("Transcribe", int(total_duration), int(total_duration))

        del model
        gc.collect()
        return " ".join(parts).strip()

    except ImportError:
        pass
    except Exception as e:
        _log(on_log, f"[WARN] faster-whisper failed: {e!r}; trying openai-whisper...")
        gc.collect()

    # Fallback: openai-whisper
    try:
        import whisper

        _log(on_log, f"[INFO] Transcribing '{path.name}' with openai-whisper (CPU)...")
        model = whisper.load_model("base", device="cpu")

        if on_sub_progress:
            on_sub_progress("Loading model", 0, 1)

        result = model.transcribe(
            str(path),
            fp16=False,
            verbose=False,
        )

        if on_sub_progress:
            on_sub_progress("Transcribe", 1, 1)

        text = result.get("text", "").strip()
        del model, result
        gc.collect()
        return text

    except ImportError:
        pass
    except Exception as e:
        _log(on_log, f"[WARN] openai-whisper failed: {e!r}")
        gc.collect()

    return ""


def _extract_audio(
    path: Path,
    on_log: LogFn | None = None,
    on_sub_progress: SubProgressFn | None = None,
) -> str:
    """Full audio text extraction via speech-to-text."""
    caps = multimedia_capabilities()

    if not caps["audio_whisper"]:
        _log(on_log, f"[WARN] No speech-to-text backend installed for "
                     f"'{path.name}'. Install faster-whisper or openai-whisper.")
        return ""

    duration = _get_audio_duration(path)
    if duration > 0:
        _log(on_log, f"[INFO] Audio '{path.name}': {duration:.0f}s duration")

    text = _transcribe_audio_whisper(path, on_log, on_sub_progress)
    if text:
        header = f"[Audio transcription: {path.name}]"
        return f"{header}\n\n{text}"
    return ""


# ---------------------------------------------------------------------
# Video extraction (audio track -> STT + optional keyframe OCR)
# ---------------------------------------------------------------------
def _extract_video_audio_track(
    video_path: Path,
    output_wav: Path,
    on_log: LogFn | None = None,
) -> bool:
    """Extract audio track from video using ffmpeg subprocess. Returns True on success."""
    ffmpeg = _resolve_ffmpeg()
    if not ffmpeg:
        _log(on_log, "[WARN] ffmpeg not available for audio extraction")
        return False
    try:
        cmd = [
            ffmpeg, "-y", "-i", str(video_path),
            "-vn",
            "-acodec", "pcm_s16le",
            "-ar", "16000",
            "-ac", "1",
            str(output_wav),
        ]
        result = subprocess.run(
            cmd,
            capture_output=True,
            timeout=_VIDEO_AUDIO_EXTRACT_TIMEOUT,
        )
        return result.returncode == 0 and output_wav.exists()
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as e:
        _log(on_log, f"[WARN] ffmpeg audio extraction failed: {e!r}")
        return False


def _extract_video_keyframes(
    video_path: Path,
    output_dir: Path,
    on_log: LogFn | None = None,
) -> list[Path]:
    """Extract keyframes from video using ffmpeg. Returns list of image paths."""
    ffmpeg = _resolve_ffmpeg()
    if not ffmpeg:
        _log(on_log, "[WARN] ffmpeg not available for keyframe extraction")
        return []
    try:
        cmd = [
            ffmpeg, "-y", "-i", str(video_path),
            "-vf", f"select='eq(pict_type\\,I)',scale=1920:-1",
            "-frames:v", str(_MAX_KEYFRAMES),
            "-vsync", "vfr",
            str(output_dir / "frame_%04d.png"),
        ]
        result = subprocess.run(
            cmd,
            capture_output=True,
            timeout=_KEYFRAME_TIMEOUT,
        )
        if result.returncode != 0:
            _log(on_log, "[WARN] Keyframe extraction returned non-zero; "
                         "trying scene-change filter...")
            cmd = [
                ffmpeg, "-y", "-i", str(video_path),
                "-vf", f"select='gt(scene\\,0.3)',scale=1920:-1",
                "-frames:v", str(_MAX_KEYFRAMES),
                "-vsync", "vfr",
                str(output_dir / "frame_%04d.png"),
            ]
            subprocess.run(cmd, capture_output=True, timeout=_KEYFRAME_TIMEOUT)

        frames = sorted(output_dir.glob("frame_*.png"))
        return frames[:_MAX_KEYFRAMES]
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as e:
        _log(on_log, f"[WARN] Keyframe extraction failed: {e!r}")
        return []


def _extract_video(
    path: Path,
    on_log: LogFn | None = None,
    on_sub_progress: SubProgressFn | None = None,
) -> str:
    """Full video text extraction: audio transcription + keyframe OCR."""
    caps = multimedia_capabilities()
    parts: list[str] = []

    if not caps["video_ffmpeg"]:
        _log(on_log, f"[WARN] ffmpeg not found; cannot process video "
                     f"'{path.name}'. Install ffmpeg and add to PATH.")
        return ""

    # Phase 1: Extract and transcribe audio track
    if caps["audio_whisper"]:
        if on_sub_progress:
            on_sub_progress("Extract audio", 0, 2)

        with tempfile.TemporaryDirectory(prefix="tt_video_") as tmpdir:
            wav_path = Path(tmpdir) / "audio.wav"
            _log(on_log, f"[INFO] Extracting audio track from '{path.name}'...")

            if _extract_video_audio_track(path, wav_path, on_log):
                if on_sub_progress:
                    on_sub_progress("Extract audio", 1, 2)

                _log(on_log, f"[INFO] Transcribing audio from '{path.name}'...")
                transcript = _transcribe_audio_whisper(
                    wav_path, on_log, on_sub_progress
                )
                if transcript:
                    parts.append(f"[Video audio transcription: {path.name}]\n\n{transcript}")
            else:
                _log(on_log, f"[INFO] No audio track in '{path.name}' or extraction failed.")

            if on_sub_progress:
                on_sub_progress("Extract audio", 2, 2)
    else:
        _log(on_log, f"[INFO] No whisper backend; skipping audio transcription "
                     f"for '{path.name}'.")

    # Phase 2: Keyframe OCR (optional, for on-screen text)
    if caps["video_keyframe_ocr"]:
        with tempfile.TemporaryDirectory(prefix="tt_frames_") as tmpdir:
            if on_sub_progress:
                on_sub_progress("Keyframe OCR", 0, 1)

            _log(on_log, f"[INFO] Extracting keyframes from '{path.name}'...")
            frames = _extract_video_keyframes(path, Path(tmpdir), on_log)

            if frames:
                _log(on_log, f"[INFO] OCR on {len(frames)} keyframe(s)...")
                frame_texts: list[str] = []
                for i, frame in enumerate(frames):
                    if on_sub_progress:
                        on_sub_progress("Keyframe OCR", i, len(frames))
                    try:
                        ocr_text = _extract_image_ocr(frame, on_log)
                        if ocr_text and len(ocr_text) > 10:
                            frame_texts.append(ocr_text)
                    except Exception:
                        continue
                    gc.collect()

                if frame_texts:
                    # Deduplicate near-identical frames
                    unique_texts = _dedupe_frame_texts(frame_texts)
                    if unique_texts:
                        parts.append(
                            f"[Video on-screen text: {path.name}]\n\n"
                            + "\n---\n".join(unique_texts)
                        )

                if on_sub_progress:
                    on_sub_progress("Keyframe OCR", len(frames), len(frames))

    gc.collect()
    return "\n\n".join(parts)


def _dedupe_frame_texts(texts: list[str], threshold: float = 0.7) -> list[str]:
    """Remove near-duplicate frame OCR texts (many keyframes show the same slide).
    Uses word-set Jaccard similarity to avoid prefix-bias that drops longer texts."""
    if not texts:
        return []
    unique: list[str] = [texts[0]]
    unique_word_sets: list[set[str]] = [set(texts[0].lower().split())]
    for text in texts[1:]:
        if not text.strip():
            continue
        words = set(text.lower().split())
        if not words:
            continue
        is_dup = False
        for existing_words in unique_word_sets:
            if not existing_words:
                continue
            intersection = len(words & existing_words)
            union = len(words | existing_words)
            if union > 0 and intersection / union > threshold:
                is_dup = True
                break
        if not is_dup:
            unique.append(text)
            unique_word_sets.append(words)
    return unique


# ---------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------
def extract_multimedia_text(
    path: Path | str,
    on_log: LogFn | None = None,
    on_sub_progress: SubProgressFn | None = None,
) -> str:
    """Best-effort text extraction from a multimedia file. Never raises;
    returns '' on failure so one bad file does not abort an index build.

    on_sub_progress(phase_label, current, total) is called for granular
    progress within a single file (useful for long audio/video)."""
    p = Path(path)
    ext = p.suffix.lower()

    try:
        if ext in IMAGE_EXTENSIONS:
            return _extract_image(p, on_log, on_sub_progress)
        elif ext in AUDIO_EXTENSIONS:
            return _extract_audio(p, on_log, on_sub_progress)
        elif ext in VIDEO_EXTENSIONS:
            return _extract_video(p, on_log, on_sub_progress)
    except MemoryError:
        _log(on_log, f"[WARN] Out of memory processing '{p.name}'; skipping.")
        gc.collect()
    except Exception as e:
        _log(on_log, f"[WARN] Multimedia extraction failed for '{p.name}': {e!r}")
        gc.collect()

    return ""


def multimedia_status_summary() -> str:
    """Human-readable summary of available multimedia capabilities."""
    caps = multimedia_capabilities()
    lines: list[str] = []

    if caps["image_ocr"]:
        lines.append("Image OCR: ACTIVE")
    else:
        lines.append("Image OCR: inactive (install pytesseract or rapidocr-onnxruntime)")

    if caps["audio_whisper"]:
        lines.append("Audio transcription: ACTIVE")
    else:
        lines.append("Audio transcription: inactive (install faster-whisper)")

    if caps["video_ffmpeg"]:
        lines.append("Video processing: ACTIVE")
    else:
        lines.append("Video processing: inactive (install ffmpeg)")

    if caps["video_keyframe_ocr"]:
        lines.append("Video keyframe OCR: ACTIVE")
    else:
        lines.append("Video keyframe OCR: inactive")

    return "\n".join(lines)
