"""
kb_indexer.py
Automatic, resumable, crash-safe knowledge-base indexing.

The Recursive Language Model reads a per-project chunk index (see
kb_store). Building that index for large document sets is the slow part of
the first generation. This module turns indexing into a background step
that:

  * runs automatically (call build_index_resumable from a worker thread),
  * reports progress (files done / total + elapsed + estimated remaining),
  * reports sub-file progress for heavy multimedia operations (audio/video
    transcription) via on_sub_progress callback,
  * checkpoints after every file to a partial file next to the final index,
    so an app crash or close mid-build RESUMES from where it stopped instead
    of restarting, and
  * is idempotent: re-running with an unchanged KB is a no-op.

Stability-first for multimedia on U-series CPUs:
  * Memory: gc.collect() after every file, especially after heavy extractions.
  * Isolation: multimedia extraction runs in subprocess where possible.
  * Timeouts: hard limits on each extraction so hangs never stall the app.
  * Crash recovery: if extraction OOMs or segfaults, the checkpoint is intact
    and the next run skips the problematic file cleanly.

The output is the same kb_index.json the RLM already loads (so nothing
downstream changes); the partial checkpoint is a separate file that is
promoted atomically on completion and deleted afterwards.

Determinism: documents are processed in the same sorted order kb_store
uses, and each document's chunk ids are derived from its fixed position in
that sorted list, so ids are stable across resumes.

ASCII-only; fully type-hinted; no third-party imports.
"""

from __future__ import annotations

import gc
import json
import os
import threading
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Final

from kb.store import (
    KbChunk,
    KbIndex,
    KbSource,
    _index_is_current,
    _load_index,
    _raw_scan,
    _scan_sources,
    chunk_document,
    dedup_twins,
    extract_text,
)
from kb.ocr import standardize_to_text

# (done_files, total_files, elapsed_seconds) -> None
ProgressFn = Callable[[int, int, float], None]
# (phase_label, current_step, total_steps) -> None for within-file progress
SubProgressFn = Callable[[str, int, int], None]
# Cooperative cancel: return True to pause (checkpoint is kept for resume).
StopFn = Callable[[], bool]
LogFn = Callable[[str], None]

_PARTIAL_SUFFIX: Final[str] = ".partial.json"
_SCHEMA: Final[int] = 1


def _sig(name: str, mtime: float, size: int) -> str:
    return f"{name}|{round(float(mtime), 3)}|{int(size)}"


def _file_sig(p: Path) -> tuple[str, float, int]:
    try:
        st = p.stat()
        return p.name, st.st_mtime, st.st_size
    except OSError:
        return p.name, 0.0, 0


def partial_path_for(index_path: Path | str) -> Path:
    index_path = Path(index_path)
    return index_path.with_name(index_path.name + _PARTIAL_SUFFIX)


@dataclass(slots=True)
class IndexStatus:
    is_current: bool
    n_files: int
    has_partial: bool


def index_status(kb_dir: Path | str, index_path: Path | str) -> IndexStatus:
    """Cheap check the UI can use to decide whether to kick off indexing."""
    kb_dir = Path(kb_dir)
    index_path = Path(index_path)
    files = _scan_sources(kb_dir)
    return IndexStatus(
        is_current=_index_is_current(index_path, files),
        n_files=len(files),
        has_partial=partial_path_for(index_path).exists(),
    )


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=True), encoding="utf-8")
    os.replace(str(tmp), str(path))


def _load_partial(partial: Path) -> dict[str, Any] | None:
    try:
        if not partial.exists():
            return None
        data = json.loads(partial.read_text(encoding="utf-8"))
        if int(data.get("schema", 0)) != _SCHEMA:
            return None
        return data
    except (OSError, json.JSONDecodeError, ValueError):
        return None


def _format_eta(seconds: float) -> str:
    """Human-readable ETA string."""
    if seconds < 60:
        return f"{int(seconds)}s"
    if seconds < 3600:
        m = int(seconds // 60)
        s = int(seconds % 60)
        return f"{m}m {s}s"
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    return f"{h}h {m}m"


def _chunks_to_dicts(chunks: list[KbChunk]) -> list[dict[str, Any]]:
    return [
        {"chunk_id": c.chunk_id, "doc": c.doc, "title": c.title,
         "text": c.text, "n_chars": c.n_chars, "context": c.context}
        for c in chunks
    ]


def _dicts_to_chunks(items: list[dict[str, Any]]) -> list[KbChunk]:
    return [
        KbChunk(
            chunk_id=str(d.get("chunk_id", "")),
            doc=str(d.get("doc", "")),
            title=str(d.get("title", "")),
            text=str(d.get("text", "")),
            n_chars=int(d.get("n_chars", 0) or 0),
            context=str(d.get("context", "") or ""),
        )
        for d in items
    ]


def _process_one_file(
    p: Path,
    doc_index: int,
    on_log: LogFn | None,
    on_sub_progress: SubProgressFn | None,
    llm_client: "Any | None",
    llm_model: str,
) -> list[KbChunk]:
    """Extract + chunk (+ contextualize) a single document.

    Pure with respect to the index: it only reads ``p`` and returns the new
    chunks, so it is safe to run for many files concurrently. ``doc_index`` is
    the file's fixed position in the sorted scan, which keeps chunk ids stable
    and deterministic regardless of completion order. Never raises.
    """
    def _log(msg: str) -> None:
        if on_log is not None:
            try:
                on_log(msg)
            except Exception:
                pass

    is_multimedia = False
    try:
        from kb.multimedia import is_multimedia_file
        is_multimedia = is_multimedia_file(p)
    except Exception:
        pass

    try:
        if is_multimedia:
            from kb.multimedia import extract_multimedia_text
            _log(f"[INFO] Processing multimedia: '{p.name}'...")
            text = extract_multimedia_text(
                p, on_log=on_log, on_sub_progress=on_sub_progress
            )
        else:
            text = standardize_to_text(p, on_log=on_log)

        if not text.strip():
            try:
                file_size_kb = p.stat().st_size / 1024
            except OSError:
                file_size_kb = 0.0
            _log(f"[WARN] '{p.name}' ({file_size_kb:.0f} KB) yielded no "
                 f"text - may need OCR or is binary/encrypted.")
            return []

        text_kb = len(text) / 1024
        new_chunks = chunk_document(p.name, doc_index, text)
        if llm_client is not None and llm_model and new_chunks:
            try:
                from kb.contextual import contextualize_document
                chunk_dicts = [
                    {"text": c.text, "chunk_id": c.chunk_id}
                    for c in new_chunks
                ]
                n_ctx = contextualize_document(
                    llm_client, llm_model, text, chunk_dicts, on_log=on_log,
                )
                if n_ctx > 0:
                    ctx_map = {
                        d["chunk_id"]: d.get("context", "")
                        for d in chunk_dicts if d.get("context")
                    }
                    for c in new_chunks:
                        if c.chunk_id in ctx_map:
                            c.context = ctx_map[c.chunk_id]
            except Exception as e:  # noqa: BLE001
                _log(f"[WARN] Contextual retrieval failed for '{p.name}': "
                     f"{e!r}; using plain chunks.")
        _log(f"[INFO] '{p.name}': {text_kb:.0f} KB text -> "
             f"{len(new_chunks)} chunk(s)")
        return new_chunks
    except MemoryError:
        _log(f"[WARN] Out of memory indexing '{p.name}'; skipping. "
             "Consider closing other applications.")
        gc.collect()
        return []
    except Exception as e:  # noqa: BLE001 - one bad file must not abort
        _log(f"[WARN] Could not index '{p.name}': {e!r}; skipping.")
        return []


def build_index_resumable(
    kb_dir: Path | str,
    index_path: Path | str,
    on_progress: ProgressFn | None = None,
    on_log: LogFn | None = None,
    should_stop: StopFn | None = None,
    on_sub_progress: SubProgressFn | None = None,
    llm_client: "Any | None" = None,
    llm_model: str = "",
    force: bool = False,
) -> KbIndex:
    """Build (or refresh) the project KB index incrementally and resumably.

    Returns the finished KbIndex. If should_stop() becomes true, the build
    pauses after the current file with the checkpoint intact and returns a
    partial KbIndex (callers should treat a paused result as incomplete).
    Never raises on I/O problems with the checkpoint; a corrupt checkpoint
    is discarded and the build starts fresh.

    on_sub_progress(phase, current, total) is forwarded to multimedia
    extractors for within-file progress on heavy operations (transcription,
    keyframe OCR). This allows the UI to show e.g. "Transcribe: 45s/120s"
    for a long audio file."""
    kb_dir = Path(kb_dir)
    index_path = Path(index_path)
    partial = partial_path_for(index_path)

    def _log(msg: str) -> None:
        if on_log is not None:
            try:
                on_log(msg)
            except Exception:
                pass

    def _prog(done: int, total: int, elapsed: float, name: str = "") -> None:
        """Call on_progress, passing the current filename when the callback
        accepts a 4th argument; falls back to the 3-arg form otherwise."""
        if on_progress is None:
            return
        try:
            on_progress(done, total, elapsed, name)  # type: ignore[misc]
        except TypeError:
            try:
                on_progress(done, total, elapsed)
            except Exception:
                pass
        except Exception:
            pass

    files, dropped_twins = dedup_twins(_raw_scan(kb_dir))
    for pdf, sheet in dropped_twins:
        _log(f"[INFO] Skipping redundant PDF '{pdf.name}'; using spreadsheet "
             f"'{sheet.name}' (cleaner extraction).")
    total = len(files)

    # Forced full rebuild: drop the stored index + any resume checkpoint so the
    # whole KB is re-extracted and re-chunked from scratch (used by the explicit
    # "Rebuild KB index" action and the post-reinstall reindex-all flow).
    if force:
        _log("[INFO] Forced rebuild: discarding cached index and checkpoint.")
        for stale in (index_path, partial):
            try:
                stale.unlink(missing_ok=True)
            except OSError:
                pass

    # Already up to date: nothing to do (skipped entirely on a forced rebuild).
    if not force and _index_is_current(index_path, files):
        try:
            partial.unlink(missing_ok=True)
        except OSError:
            pass
        _prog(total, total, 0.0)
        _log(f"[INFO] KB index is current ({total} file(s)); no rebuild "
             f"needed.")
        try:
            return _load_index(index_path)
        except (OSError, json.JSONDecodeError, ValueError):
            pass  # fall through and rebuild if the file is unreadable

    # Guard: never overwrite a contextualized index with a plain one.
    # If the existing index has LLM-generated context prefixes but we have
    # no LLM client for this run, keep the existing higher-quality index.
    if not llm_client and index_path.exists():
        try:
            existing = _load_index(index_path)
            if any(c.context for c in existing.chunks):
                _prog(total, total, 0.0)
                _log("[INFO] Existing index has contextual retrieval data; "
                     "skipping rebuild (no LLM client available).")
                return existing
        except (OSError, json.JSONDecodeError, ValueError):
            pass

    if total == 0:
        # No documents: write an empty, current index so the check passes.
        empty = KbIndex(chunks=[], sources=[], built_at=time.time())
        try:
            _atomic_write_json(index_path, empty.to_dict())
            partial.unlink(missing_ok=True)
        except OSError:
            pass
        _prog(0, 0, 0.0)
        _log("[INFO] No KB documents to index.")
        return empty

    # Target signatures (name -> sig) for the current file set, in order.
    target_sigs: list[str] = []
    for p in files:
        n, m, s = _file_sig(p)
        target_sigs.append(_sig(n, m, s))
    target_set = set(target_sigs)

    # Resume from a valid checkpoint: keep only chunks whose source file is
    # still present and unchanged.
    done_sigs: set[str] = set()
    chunks: list[KbChunk] = []
    data = _load_partial(partial)
    if data is not None:
        saved_done = {str(x) for x in (data.get("done_sigs") or [])}
        if saved_done and saved_done.issubset(target_set):
            done_sigs = saved_done
            chunks = _dicts_to_chunks(data.get("chunks") or [])
            _log(f"[INFO] Resuming KB index from checkpoint: "
                 f"{len(done_sigs)}/{total} file(s) already done.")
        else:
            _log("[INFO] Checkpoint no longer matches the KB; rebuilding "
                 "from scratch.")

    start = time.monotonic()
    done = len(done_sigs)
    _prog(done, total, 0.0)

    # Track per-file timing for ETA calculation
    file_times: list[float] = []

    # Files still to do, paired with their fixed sorted position so chunk ids
    # stay deterministic no matter what order they finish in.
    pending: list[tuple[int, Path, str]] = []
    for doc_index, p in enumerate(files):
        n, m, s = _file_sig(p)
        sig = _sig(n, m, s)
        if sig in done_sigs:
            continue
        pending.append((doc_index, p, sig))

    from core.app_config import resolve_index_workers
    workers = resolve_index_workers(len(pending))

    # Serialize checkpoint writes / shared-state mutation across worker threads.
    state_lock = threading.Lock()
    stop_requested = {"flag": False}

    def _handle_result(
        doc_index: int, p: Path, sig: str, new_chunks: list[KbChunk],
        file_elapsed: float,
    ) -> None:
        """Merge one finished file into the index + checkpoint. Serialized."""
        nonlocal done
        chunks.extend(new_chunks)
        done_sigs.add(sig)
        done += 1
        file_times.append(file_elapsed)

        # Checkpoint after every file so a crash resumes here.
        try:
            _atomic_write_json(partial, {
                "schema": _SCHEMA,
                "done_sigs": sorted(done_sigs),
                "chunks": _chunks_to_dicts(chunks),
            })
        except OSError:
            pass

        elapsed = time.monotonic() - start
        remaining_files = total - done
        if file_times and remaining_files > 0:
            # With N workers, wall-clock ETA is roughly the serial estimate
            # divided by the degree of parallelism.
            avg_time = sum(file_times) / len(file_times)
            eta_seconds = avg_time * remaining_files / max(1, workers)
            _log(f"[INFO] {p.name} done in {file_elapsed:.1f}s | "
                 f"ETA: ~{_format_eta(eta_seconds)}")

        _prog(done, total, elapsed, p.name)

    if workers <= 1:
        # Single-worker path keeps the original simple, low-memory behavior.
        for doc_index, p, sig in pending:
            if should_stop is not None and should_stop():
                _log("[WARN] KB indexing paused; will resume on next launch.")
                return KbIndex(chunks=list(chunks), sources=[], built_at=0.0)
            file_start = time.monotonic()
            new_chunks = _process_one_file(
                p, doc_index, on_log, on_sub_progress, llm_client, llm_model
            )
            _handle_result(doc_index, p, sig, new_chunks,
                           time.monotonic() - file_start)
            gc.collect()
    else:
        # Parallel path: saturate the box. Keep at most `workers` files in
        # flight so memory stays bounded on large/scanned PDFs.
        _log(f"[INFO] Indexing {len(pending)} file(s) across {workers} "
             f"worker(s) for maximum throughput.")
        starts: dict[Any, tuple[int, Path, str, float]] = {}

        def _submit(ex: ThreadPoolExecutor, item: tuple[int, Path, str]) -> Any:
            doc_index, p, sig = item
            fut = ex.submit(
                _process_one_file, p, doc_index, on_log, on_sub_progress,
                llm_client, llm_model,
            )
            starts[fut] = (doc_index, p, sig, time.monotonic())
            return fut

        with ThreadPoolExecutor(max_workers=workers) as ex:
            it = iter(pending)
            in_flight: set[Any] = set()
            # Prime the pool with up to `workers` files.
            for _ in range(workers):
                try:
                    in_flight.add(_submit(ex, next(it)))
                except StopIteration:
                    break

            while in_flight:
                completed, in_flight = wait(
                    in_flight, return_when=FIRST_COMPLETED
                )
                for fut in completed:
                    doc_index, p, sig, file_start = starts.pop(fut)
                    try:
                        new_chunks = fut.result()
                    except Exception as e:  # noqa: BLE001
                        _log(f"[WARN] Could not index '{p.name}': {e!r}; "
                             "skipping.")
                        new_chunks = []
                    with state_lock:
                        _handle_result(doc_index, p, sig, new_chunks,
                                       time.monotonic() - file_start)

                    # Cooperative pause: stop submitting new work, let the
                    # in-flight files finish (already checkpointed).
                    if not stop_requested["flag"] and should_stop is not None \
                            and should_stop():
                        stop_requested["flag"] = True
                        _log("[WARN] KB indexing pausing; finishing "
                             "in-flight file(s) then will resume next launch.")

                    # Backfill the pool to keep `workers` files in flight.
                    if not stop_requested["flag"]:
                        try:
                            in_flight.add(_submit(ex, next(it)))
                        except StopIteration:
                            pass
                gc.collect()

        if stop_requested["flag"]:
            return KbIndex(chunks=list(chunks), sources=[], built_at=0.0)

    # Finalize: write the real index, drop the checkpoint.
    sources = [
        KbSource(name=p.name, mtime=_file_sig(p)[1], size=_file_sig(p)[2])
        for p in files
    ]
    index = KbIndex(chunks=chunks, sources=sources, built_at=time.time())
    try:
        _atomic_write_json(index_path, index.to_dict())
        partial.unlink(missing_ok=True)
    except OSError:
        pass
    _log(f"[SUCCESS] KB index built: {len(chunks)} chunk(s) from {total} "
         f"file(s).")
    _prog(total, total, time.monotonic() - start)
    return index
