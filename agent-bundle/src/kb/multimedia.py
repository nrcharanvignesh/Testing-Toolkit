"""
kb_multimedia.py
API-based multimedia (image, audio, video) text extraction for
knowledge-base indexing. All processing goes through the GenAI API --
no local ONNX/Whisper/Tesseract models.

Design principles (U-series laptop target):
  * One file at a time, sequential, never parallel.
  * ffmpeg is the only local tool (audio extraction from video).
  * All OCR and transcription via API (GPT-4o vision, Whisper API).
  * Graceful degradation: if no API key configured, return "" with a
    warning log. The index still builds; multimedia just contributes no
    text chunks.

Supported formats:
  IMAGE: .png .jpg .jpeg .gif .bmp .tiff .tif .webp .svg .ico .heic .heif
         -> OCR via GPT-4o vision API

  AUDIO: .mp3 .wav .ogg .flac .m4a .wma .aac .opus .aiff
         -> Speech-to-text via Whisper API endpoint

  VIDEO: .mp4 .mkv .avi .mov .webm .wmv .flv .m4v .mpg .mpeg .3gp
         -> Audio track extraction via ffmpeg (subprocess, local)
         -> Speech-to-text on extracted audio (Whisper API)
         -> Keyframe OCR via GPT-4o vision API (optional, capped at N frames)

Public API:
    extract_multimedia_text(path, on_progress, on_log) -> str
    multimedia_capabilities() -> dict[str, bool]
    is_multimedia_file(path) -> bool
    MULTIMEDIA_EXTENSIONS -> frozenset[str]
"""

from __future__ import annotations

import base64
import gc
import os
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Callable, Final

from core.hardware import optimal_cpu_workers


# Windows: suppress console window on subprocess calls
_SUBPROCESS_KWARGS: dict[str, object] = {}
if sys.platform == "win32":
    _si = subprocess.STARTUPINFO()
    _si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    _si.wShowWindow = 0  # SW_HIDE
    _SUBPROCESS_KWARGS = {
        "startupinfo": _si,
        "creationflags": subprocess.CREATE_NO_WINDOW,
    }

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
_VIDEO_AUDIO_EXTRACT_TIMEOUT: Final[int] = 300
_KEYFRAME_TIMEOUT: Final[int] = 180

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
    """Check which multimedia backends are available. Cached after first call.
    With API-based processing, OCR and transcription are always available
    when an API key is configured."""
    global _capabilities_cache
    if _capabilities_cache is not None:
        return dict(_capabilities_cache)

    caps: dict[str, bool] = {
        "image_ocr": False,
        "audio_whisper": False,
        "video_ffmpeg": False,
        "video_keyframe_ocr": False,
    }

    # API key present = OCR and transcription available (routed to API)
    try:
        from core.app_config import LLM_API_KEY
        has_key = bool(LLM_API_KEY)
    except Exception:
        has_key = False

    caps["image_ocr"] = has_key
    caps["audio_whisper"] = has_key

    # Video: ffmpeg binary still needed for audio track extraction
    ffmpeg = _resolve_ffmpeg()
    if ffmpeg:
        try:
            result = subprocess.run(
                [ffmpeg, "-version"],
                capture_output=True, timeout=10, **_SUBPROCESS_KWARGS,
            )
            caps["video_ffmpeg"] = result.returncode == 0
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
            pass

    # Video keyframe OCR: needs ffmpeg (for frames) + API (for OCR)
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
    """OCR text from an image file via GPT-4o vision API.
    Routes through the GenAI proxy - no local ONNX or Tesseract needed."""
    try:
        import httpx
        from core.app_config import LLM_API_KEY, LLM_BASE_URL
        from core.model_router import Task, route
        from core.settings_store import build_runtime_config

        key = LLM_API_KEY
        url = LLM_BASE_URL
        model = route(Task.OCR_EXTRACT)

        if not key or not url:
            _log(on_log, "[WARN] No API key/URL for vision OCR; skipping image.")
            return ""

        # Guard: skip excessively large images (>50MB raw would OOM on base64)
        _MAX_IMAGE_BYTES: int = 50 * 1024 * 1024
        if path.stat().st_size > _MAX_IMAGE_BYTES:
            _log(on_log, f"[WARN] Image '{path.name}' exceeds 50MB; skipping OCR.")
            return ""

        # Read and base64-encode the image
        img_bytes = path.read_bytes()
        b64 = base64.b64encode(img_bytes).decode("ascii")
        suffix = path.suffix.lower().lstrip(".")
        mime = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg",
                "gif": "image/gif", "bmp": "image/bmp", "webp": "image/webp",
                "tiff": "image/tiff", "tif": "image/tiff",
                "svg": "image/svg+xml", "ico": "image/x-icon",
                "heic": "image/heic", "heif": "image/heif",
                }.get(suffix, "image/png")

        cfg = build_runtime_config()
        ssl_ctx = cfg.build_ssl()
        with httpx.Client(base_url=url, verify=ssl_ctx, timeout=120.0) as client:
            resp = client.post(
                "/chat/completions",
                headers={
                    "Authorization": f"Bearer {key}",
                    "x-api-key": key,
                    "Content-Type": "application/json",
                },
                json={
                    "model": model,
                    "max_tokens": 4096,
                    "messages": [{
                        "role": "user",
                        "content": [
                            {"type": "image_url", "image_url": {
                                "url": f"data:{mime};base64,{b64}"}},
                            {"type": "text", "text":
                             "Extract ALL text visible in this image. "
                             "Return only the extracted text, no commentary. "
                             "If no text is visible, return empty string."},
                        ],
                    }],
                },
            )
            resp.raise_for_status()
            data = resp.json()
            text = data["choices"][0]["message"]["content"].strip()
            from core.network_status import report_success
            report_success()
            return text

    except Exception as e:
        _log(on_log, f"[WARN] API vision OCR failed on '{path.name}': {e!r}")
        try:
            from core.network_status import report_failure
            report_failure()
        except Exception:
            pass
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
            capture_output=True, text=True, timeout=30, **_SUBPROCESS_KWARGS,
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


_WHISPER_MAX_BYTES: Final[int] = 24 * 1024 * 1024  # 24MB safe limit (API max is 25MB)

_WHISPER_MIME: Final[dict[str, str]] = {
    ".mp3": "audio/mpeg", ".wav": "audio/wav", ".m4a": "audio/mp4",
    ".mp4": "audio/mp4", ".ogg": "audio/ogg", ".webm": "audio/webm",
    ".flac": "audio/flac", ".mpeg": "audio/mpeg", ".mpga": "audio/mpeg",
}

# Formats the Whisper API accepts directly (per GenAI docs)
_WHISPER_SUPPORTED: Final[frozenset[str]] = frozenset({
    ".mp3", ".mp4", ".mpeg", ".mpga", ".m4a", ".wav", ".webm",
})


def _convert_audio_to_mp3(src: Path, dst: Path, on_log: LogFn | None = None) -> bool:
    """Convert any audio file to mp3 via ffmpeg for API compatibility.
    Timeout scales with file size to handle multi-hour recordings."""
    ffmpeg = _resolve_ffmpeg()
    if not ffmpeg:
        _log(on_log, "[WARN] ffmpeg not available; cannot convert audio format")
        return False
    try:
        file_mb = src.stat().st_size / (1024 * 1024)
        timeout = max(_VIDEO_AUDIO_EXTRACT_TIMEOUT, int(300 + file_mb))
        cmd = [
            ffmpeg, "-y", "-i", str(src),
            "-acodec", "libmp3lame", "-ar", "16000", "-ac", "1",
            "-b:a", "64k", str(dst),
        ]
        result = subprocess.run(
            cmd, capture_output=True, timeout=timeout,
            **_SUBPROCESS_KWARGS,
        )
        return result.returncode == 0 and dst.exists() and dst.stat().st_size > 0
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as e:
        _log(on_log, f"[WARN] Audio conversion failed: {e!r}")
        return False


def _split_audio_segments(
    src: Path, output_dir: Path, segment_seconds: int = 600,
    on_log: LogFn | None = None,
) -> list[Path]:
    """Split audio into segments under the API size limit via ffmpeg.
    Timeout scales with file size to handle multi-hour recordings."""
    ffmpeg = _resolve_ffmpeg()
    if not ffmpeg:
        return [src]
    try:
        # Scale timeout: 300s base + 1s per MB of input (handles 10h+ files)
        file_mb = src.stat().st_size / (1024 * 1024)
        timeout = max(_VIDEO_AUDIO_EXTRACT_TIMEOUT, int(300 + file_mb))
        pattern = str(output_dir / "seg_%04d.mp3")
        cmd = [
            ffmpeg, "-y", "-i", str(src),
            "-f", "segment", "-segment_time", str(segment_seconds),
            "-acodec", "libmp3lame", "-ar", "16000", "-ac", "1",
            "-b:a", "64k", pattern,
        ]
        result = subprocess.run(
            cmd, capture_output=True, timeout=timeout,
            **_SUBPROCESS_KWARGS,
        )
        if result.returncode == 0:
            segs = sorted(output_dir.glob("seg_*.mp3"))
            if segs:
                _log(on_log, f"[INFO] Split audio into {len(segs)} segments")
                return segs
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as e:
        _log(on_log, f"[WARN] Audio split failed: {e!r}")
    return [src]


def _transcribe_audio_whisper(
    path: Path,
    on_log: LogFn | None = None,
    on_sub_progress: SubProgressFn | None = None,
) -> str:
    """Transcribe audio via API (Whisper endpoint on GenAI proxy).
    No local model loading -- all processing happens server-side.
    Handles format conversion and file-size segmentation automatically."""
    try:
        import httpx
        from core.app_config import LLM_API_KEY, LLM_BASE_URL
        from core.settings_store import build_runtime_config

        key = LLM_API_KEY
        url = LLM_BASE_URL

        if not key or not url:
            _log(on_log, "[WARN] No API key/URL for audio transcription; skipping.")
            return ""

        _log(on_log, f"[INFO] Transcribing '{path.name}' via API...")
        if on_sub_progress:
            on_sub_progress("Transcribe (API)", 0, 1)

        cfg = build_runtime_config()
        ssl_ctx = cfg.build_ssl()

        # Convert unsupported formats to mp3 (in tempdir, not source dir)
        actual_path = path
        tmp_converted: Path | None = None
        tmp_seg_dir: str | None = None
        try:
            if path.suffix.lower() not in _WHISPER_SUPPORTED:
                _log(on_log, f"[INFO] Converting '{path.suffix}' to mp3 for API...")
                import tempfile as _tf
                tmp_converted = Path(_tf.mkdtemp(prefix="tt_conv_")) / (path.stem + ".mp3")
                if not _convert_audio_to_mp3(path, tmp_converted, on_log):
                    _log(on_log, f"[WARN] Cannot convert '{path.name}' to supported format; skipping.")
                    return ""
                actual_path = tmp_converted

            # Segment if file exceeds API size limit
            segments: list[Path] = [actual_path]
            if actual_path.stat().st_size > _WHISPER_MAX_BYTES:
                _log(on_log, f"[INFO] Audio file {actual_path.stat().st_size // (1024*1024)}MB "
                             f"exceeds 24MB limit; splitting into segments...")
                import tempfile as _tf
                tmp_seg_dir = _tf.mkdtemp(prefix="tt_seg_")
                segments = _split_audio_segments(
                    actual_path, Path(tmp_seg_dir), on_log=on_log
                )

            # Guard: if split failed and file still > limit, skip the segment
            # to avoid sending oversized payload to API
            segments = [s for s in segments if s.stat().st_size <= _WHISPER_MAX_BYTES
                        or s == actual_path and actual_path.stat().st_size <= _WHISPER_MAX_BYTES]
            if not segments:
                _log(on_log, f"[WARN] All segments exceed 24MB limit for '{path.name}'; skipping.")
                return ""

            # Transcribe each segment
            all_text: list[str] = []
            mime = _WHISPER_MIME.get(actual_path.suffix.lower(), "audio/mpeg")

            with httpx.Client(base_url=url, verify=ssl_ctx, timeout=600.0) as client:
                for i, seg in enumerate(segments):
                    file_bytes = seg.read_bytes()
                    if len(file_bytes) == 0:
                        continue
                    # Final guard: never send > 24MB to API
                    if len(file_bytes) > _WHISPER_MAX_BYTES:
                        continue
                    seg_mime = _WHISPER_MIME.get(seg.suffix.lower(), mime)
                    resp = client.post(
                        "/audio/transcriptions",
                        headers={
                            "Authorization": f"Bearer {key}",
                            "x-api-key": key,
                        },
                        files={"file": (seg.name, file_bytes, seg_mime)},
                        data={"model": "openai.whisper-1", "response_format": "text"},
                    )
                    resp.raise_for_status()
                    chunk_text = resp.text.strip()
                    if chunk_text:
                        all_text.append(chunk_text)
                    del file_bytes
                    if on_sub_progress and len(segments) > 1:
                        on_sub_progress("Transcribe (API)", i + 1, len(segments))

            if on_sub_progress:
                on_sub_progress("Transcribe (API)", 1, 1)

            text = " ".join(all_text)
            from core.network_status import report_success
            report_success()
            gc.collect()
            return text

        finally:
            # Cleanup temp files regardless of success/failure
            if tmp_converted and tmp_converted.exists():
                try:
                    tmp_converted.unlink()
                except OSError:
                    pass
                # Also remove the temp dir created for conversion
                try:
                    tmp_converted.parent.rmdir()
                except OSError:
                    pass
            if tmp_seg_dir:
                import shutil
                shutil.rmtree(tmp_seg_dir, ignore_errors=True)

    except Exception as e:
        _log(on_log, f"[WARN] API transcription failed for '{path.name}': {e!r}")
        try:
            from core.network_status import report_failure
            report_failure()
        except Exception:
            pass
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
        _log(on_log, f"[WARN] No speech-to-text backend available for "
                     f"'{path.name}'. Configure API key for Whisper API.")
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
    output_path: Path,
    on_log: LogFn | None = None,
) -> bool:
    """Extract audio track from video using ffmpeg subprocess.
    Outputs MP3 (compressed) to stay under API 25MB limit.
    Timeout scales with file size for multi-hour videos.
    Returns True on success."""
    ffmpeg = _resolve_ffmpeg()
    if not ffmpeg:
        _log(on_log, "[WARN] ffmpeg not available for audio extraction")
        return False
    try:
        # Scale timeout: 300s base + 1s per MB (handles 10h+ videos)
        file_mb = video_path.stat().st_size / (1024 * 1024)
        timeout = max(_VIDEO_AUDIO_EXTRACT_TIMEOUT, int(300 + file_mb))
        cmd = [
            ffmpeg, "-y", "-i", str(video_path),
            "-vn",
            "-acodec", "libmp3lame",
            "-ar", "16000",
            "-ac", "1",
            "-b:a", "64k",
            str(output_path),
        ]
        result = subprocess.run(
            cmd,
            capture_output=True,
            timeout=timeout, **_SUBPROCESS_KWARGS,
        )
        if result.returncode == 0 and output_path.exists():
            if output_path.stat().st_size == 0:
                _log(on_log, "[WARN] ffmpeg produced empty audio file")
                return False
            return True
        return False
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as e:
        _log(on_log, f"[WARN] ffmpeg audio extraction failed: {e!r}")
        return False


def _extract_video_keyframes(
    video_path: Path,
    output_dir: Path,
    on_log: LogFn | None = None,
) -> list[Path]:
    """Extract keyframes from video using ffmpeg. Returns list of image paths.
    Timeout scales with file size for long videos."""
    ffmpeg = _resolve_ffmpeg()
    if not ffmpeg:
        _log(on_log, "[WARN] ffmpeg not available for keyframe extraction")
        return []
    try:
        # Scale timeout: base 180s + 0.5s per MB for long videos
        file_mb = video_path.stat().st_size / (1024 * 1024)
        timeout = max(_KEYFRAME_TIMEOUT, int(180 + file_mb * 0.5))
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
            timeout=timeout, **_SUBPROCESS_KWARGS,
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
            subprocess.run(cmd, capture_output=True, timeout=timeout,
                          **_SUBPROCESS_KWARGS)

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
            mp3_path = Path(tmpdir) / "audio.mp3"
            _log(on_log, f"[INFO] Extracting audio track from '{path.name}'...")

            if _extract_video_audio_track(path, mp3_path, on_log):
                if on_sub_progress:
                    on_sub_progress("Extract audio", 1, 2)

                _log(on_log, f"[INFO] Transcribing audio from '{path.name}'...")
                transcript = _transcribe_audio_whisper(
                    mp3_path, on_log, on_sub_progress
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

    # Phase 2: Keyframe OCR (optional, for on-screen text) - parallelized
    if caps["video_keyframe_ocr"]:
        with tempfile.TemporaryDirectory(prefix="tt_frames_") as tmpdir:
            if on_sub_progress:
                on_sub_progress("Keyframe OCR", 0, 1)

            _log(on_log, f"[INFO] Extracting keyframes from '{path.name}'...")
            frames = _extract_video_keyframes(path, Path(tmpdir), on_log)

            if frames:
                workers = optimal_cpu_workers()
                _log(on_log, f"[INFO] OCR on {len(frames)} keyframe(s) "
                             f"with {workers} workers...")
                # OCR frames in parallel - each frame is an independent file
                frame_results: list[tuple[int, str]] = []
                with ThreadPoolExecutor(max_workers=workers) as pool:
                    futures = {
                        pool.submit(_extract_image_ocr, frame, None): i
                        for i, frame in enumerate(frames)
                    }
                    done_count = 0
                    for future in as_completed(futures):
                        idx = futures[future]
                        try:
                            ocr_text = future.result()
                            if ocr_text and len(ocr_text) > 10:
                                frame_results.append((idx, ocr_text))
                        except Exception:
                            pass
                        done_count += 1
                        if on_sub_progress:
                            on_sub_progress("Keyframe OCR",
                                            done_count, len(frames))

                # Sort by original frame order for consistent dedup
                frame_results.sort(key=lambda x: x[0])
                frame_texts = [text for _, text in frame_results]
                del frame_results
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
        lines.append("Image OCR: inactive (configure API key)")

    if caps["audio_whisper"]:
        lines.append("Audio transcription: ACTIVE")
    else:
        lines.append("Audio transcription: inactive (configure API key)")

    if caps["video_ffmpeg"]:
        lines.append("Video processing: ACTIVE")
    else:
        lines.append("Video processing: inactive (install ffmpeg)")

    if caps["video_keyframe_ocr"]:
        lines.append("Video keyframe OCR: ACTIVE")
    else:
        lines.append("Video keyframe OCR: inactive")

    return "\n".join(lines)
