"""
file_sig.py
Content-based file signatures for incremental KB indexing.

The indexer must decide, cheaply and correctly, whether a document needs
re-extraction. Keying on (mtime, size) is cheap but wrong in common cases:
re-copying a file, restoring from backup, or a cloud-sync client rewriting it
all bump mtime while the bytes are identical - forcing a needless, expensive
re-extract + re-chunk (and, with an LLM client, re-contextualize) of the whole
document.

This module keys on a SHA-256 of the file contents instead, so a document is
re-indexed only when its bytes actually change. To keep re-scans cheap we never
re-hash a file whose (mtime, size) is unchanged: a small JSON cache stored next
to the index maps absolute path -> {mtime, size, sha}. A scan therefore costs
one stat() per file plus a dict lookup, and only genuinely-changed files are
hashed. The hashing itself is trivial next to extraction, which already reads
every byte.

ASCII only; stdlib only; fully type-hinted.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

# Number of hex chars kept from the SHA-256 digest. 16 hex = 64 bits, which is
# far beyond collision risk for a single KB folder and keeps the index compact.
_SHA_LEN = 16
_READ_CHUNK = 1024 * 1024  # 1 MiB streamed reads keep memory flat on big files.
_CACHE_SCHEMA = 1


def hash_cache_path(index_path: Path | str) -> Path:
    """Path of the hash cache that sits beside a given index file."""
    index_path = Path(index_path)
    return index_path.with_name(index_path.name + ".hashes.json")


def load_hash_cache(cache_path: Path | str) -> dict[str, Any]:
    """Load the path -> {mtime, size, sha} cache. Returns an empty cache on any
    problem (missing/corrupt/old schema) so a bad cache only costs a re-hash."""
    cache_path = Path(cache_path)
    try:
        data = json.loads(cache_path.read_text(encoding="utf-8"))
        if int(data.get("schema", 0)) != _CACHE_SCHEMA:
            return {"schema": _CACHE_SCHEMA, "entries": {}}
        entries = data.get("entries")
        if not isinstance(entries, dict):
            return {"schema": _CACHE_SCHEMA, "entries": {}}
        return {"schema": _CACHE_SCHEMA, "entries": entries}
    except (OSError, json.JSONDecodeError, ValueError, TypeError):
        return {"schema": _CACHE_SCHEMA, "entries": {}}


def save_hash_cache(cache_path: Path | str, cache: dict[str, Any]) -> None:
    """Persist the hash cache atomically. Never raises."""
    cache_path = Path(cache_path)
    payload = {
        "schema": _CACHE_SCHEMA,
        "entries": cache.get("entries", {}),
    }
    try:
        tmp = cache_path.with_name(cache_path.name + ".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=True), encoding="utf-8")
        os.replace(str(tmp), str(cache_path))
    except OSError:
        pass


def _compute_sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        while True:
            block = fh.read(_READ_CHUNK)
            if not block:
                break
            h.update(block)
    return h.hexdigest()[:_SHA_LEN]


def file_sha(path: Path, cache: dict[str, Any]) -> str:
    """Content SHA for ``path``, reusing the cache when (mtime, size) match.

    Updates ``cache`` in place when a (re)hash happens. Returns "" if the file
    cannot be read so callers can degrade gracefully (an unreadable file simply
    won't match any cached signature, which is the safe, re-try-next-time
    behavior).
    """
    entries: dict[str, Any] = cache.setdefault("entries", {})
    key = str(path)
    try:
        st = path.stat()
        mtime = round(float(st.st_mtime), 3)
        size = int(st.st_size)
    except OSError:
        return ""

    hit = entries.get(key)
    if (
        isinstance(hit, dict)
        and int(hit.get("size", -1)) == size
        and round(float(hit.get("mtime", -1.0)), 3) == mtime
        and hit.get("sha")
    ):
        return str(hit["sha"])

    try:
        sha = _compute_sha(path)
    except OSError:
        return ""
    entries[key] = {"mtime": mtime, "size": size, "sha": sha}
    return sha


def prune_hash_cache(cache: dict[str, Any], live_paths: list[Path]) -> None:
    """Drop cache entries for files no longer present, so the cache can't grow
    without bound as documents come and go."""
    entries: dict[str, Any] = cache.get("entries", {})
    if not entries:
        return
    keep = {str(p) for p in live_paths}
    for stale in [k for k in entries if k not in keep]:
        entries.pop(stale, None)
