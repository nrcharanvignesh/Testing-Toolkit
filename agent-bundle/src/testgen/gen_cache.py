"""
gen_cache.py
A small, dependency-free persistent cache that makes repeat test-case
generation near-instant.

Two things are cached, both keyed purely by the hash of their inputs so
a hit is only ever returned for byte-identical inputs:

  1. RETRIEVAL CONTEXT - the result of the (expensive) navigate + map
     pass over the project knowledge base, keyed by the KB fingerprint,
     the work-item dump, the retrieval model, and the token budgets.
     This is the slowest part for large KBs; caching it removes most of
     the wall-clock cost on a re-run.

  2. PER-ITEM PAYLOAD - the validated JSON produced for a single work
     item, keyed by the system prompt, that item's dump, the resolved
     KB context, and the generation model. Regenerating the same item
     against an unchanged KB is then a disk read, not an API round trip.

Storage: one JSON file per key under <cache_dir>, plus an in-process
dict. Everything degrades gracefully - any I/O problem disables the
relevant operation rather than raising. Disabling the cache entirely
(enabled=False) turns every call into a miss.

ASCII-only; type-hinted; no third-party imports.
"""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any, Final

_HASH_SEP: Final[str] = "\x1e"          # ASCII record separator
_SCHEMA: Final[int] = 1


def _sha(parts: list[str]) -> str:
    h = hashlib.sha256()
    h.update(_HASH_SEP.join(parts).encode("utf-8", errors="replace"))
    return h.hexdigest()


def kb_fingerprint(index: Any) -> str:
    """Stable fingerprint of a KbIndex: its source files (name, mtime,
    size) plus chunk count and total tokens. Matches the granularity the
    index-currency check already uses, so the cache invalidates whenever
    the KB documents change."""
    try:
        srcs = sorted(
            (str(getattr(s, "name", "")),
             round(float(getattr(s, "mtime", 0.0)), 3),
             int(getattr(s, "size", 0)))
            for s in getattr(index, "sources", []) or []
        )
        parts = [f"{n}|{m}|{sz}" for n, m, sz in srcs]
        parts.append(f"chunks={len(getattr(index, 'chunks', []) or [])}")
        parts.append(f"tokens={int(getattr(index, 'total_tokens', 0))}")
        return _sha(parts)
    except Exception:
        return _sha(["unfingerprintable", "kb_metadata_error"])


def context_key(
    kb_fp: str, work_item_dump: str, fast_model: str, budget_tag: str,
) -> str:
    return "ctx_" + _sha(["ctx", str(_SCHEMA), kb_fp, fast_model,
                          budget_tag, work_item_dump])


def generation_key(
    system_prompt: str, work_item_dump: str, kb_context: str,
    primary_model: str, extra_tag: str = "",
) -> str:
    return "gen_" + _sha(["gen", str(_SCHEMA), primary_model, extra_tag,
                          system_prompt, kb_context, work_item_dump])


class GenCache:
    """Disk + memory cache. Keys are opaque strings (use the *_key
    helpers). Values must be JSON-serializable."""

    def __init__(self, cache_dir: Path | str, enabled: bool = True) -> None:
        self.enabled: bool = bool(enabled)
        self.dir: Path = Path(cache_dir)
        self._mem: dict[str, Any] = {}
        self.hits: int = 0
        self.misses: int = 0
        if self.enabled:
            try:
                self.dir.mkdir(parents=True, exist_ok=True)
            except OSError:
                self.enabled = False

    def _path(self, key: str) -> Path:
        # Keys from the helpers are already safe (prefix + hex digest).
        safe = "".join(c for c in key if c.isalnum() or c in ("_", "-"))
        return self.dir / f"{safe}.json"

    def get(self, key: str) -> Any | None:
        if not self.enabled:
            self.misses += 1
            return None
        if key in self._mem:
            self.hits += 1
            return self._mem[key]
        path = self._path(key)
        try:
            if path.exists():
                data = json.loads(path.read_text(encoding="utf-8"))
                value = data.get("value")
                self._mem[key] = value
                self.hits += 1
                return value
        except (OSError, json.JSONDecodeError, ValueError):
            pass
        self.misses += 1
        return None

    def set(self, key: str, value: Any) -> None:
        if not self.enabled:
            return
        self._mem[key] = value
        try:
            payload = {"schema": _SCHEMA, "saved_at": time.time(),
                       "value": value}
            self._path(key).write_text(
                json.dumps(payload, ensure_ascii=True), encoding="utf-8"
            )
        except (OSError, TypeError, ValueError):
            # A non-serializable value or unwritable dir just means no
            # persistence; the in-memory copy still serves this session.
            pass

    def clear(self) -> int:
        """Delete every cached file. Returns how many were removed."""
        self._mem.clear()
        n = 0
        if not self.dir.exists():
            return 0
        for p in self.dir.glob("*.json"):
            try:
                p.unlink()
                n += 1
            except OSError:
                pass
        return n

    def stats(self) -> str:
        total = self.hits + self.misses
        rate = (100.0 * self.hits / total) if total else 0.0
        return (f"cache hits={self.hits} misses={self.misses} "
                f"({rate:.0f}% hit)")
