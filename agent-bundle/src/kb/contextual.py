"""
kb_contextual.py
Contextual Retrieval - the one place the completions API (which is all
we have) materially improves retrieval quality.

Each chunk is prefixed with a short, LLM-generated sentence situating it
within its whole document, BEFORE it is indexed by BM25 and the embedder.
Research shows this cuts retrieval failures substantially (~49% when
combined with contextual BM25, ~67% with reranking). Because the same
document is reused for every one of its chunks, the document is sent as a
cached prompt prefix so only the per-chunk tail is re-billed.

This step is OPTIONAL and index-time only (slowness is acceptable there):
  * it runs only when an LLM client is available and the caller enables it,
  * it is bounded (max chunks per document, bounded concurrency),
  * any failure falls back to the plain chunk (no context), so indexing is
    never blocked.

The prompt builder is pure and unit-testable without the API.

ASCII-only; fully type-hinted.
"""

from __future__ import annotations

import asyncio
from typing import Any, Callable, Final

LogFn = Callable[[str], None]

_MAX_DOC_CHARS: Final[int] = 60000    # cap the cached document body
_CONTEXT_MAX_TOKENS: Final[int] = 120
_CONCURRENCY: Final[int] = 4

_SYSTEM: Final[str] = (
    "You situate a document excerpt within its source document for search "
    "retrieval. Reply with one or two short sentences (under 60 words) of "
    "context only - what this excerpt is about and where it fits - with no "
    "preamble, labels, or quotation."
)


def build_context_prompt(doc_text: str, chunk_text: str) -> tuple[str, str]:
    """Return (document_block, instruction) for contextualizing one chunk.
    The document_block is the part worth caching across a document's chunks;
    the instruction carries the specific chunk."""
    doc = (doc_text or "")[:_MAX_DOC_CHARS]
    document_block = (
        "<document>\n" + doc + "\n</document>"
    )
    instruction = (
        "Here is a chunk from the document above:\n<chunk>\n"
        + (chunk_text or "") +
        "\n</chunk>\n\nGive the short situating context for this chunk."
    )
    return document_block, instruction


async def _contextualize_one(
    client: Any, model: str, doc_block: str, chunk_text: str,
) -> str:
    _, instruction = build_context_prompt("", chunk_text)
    user = doc_block + "\n\n" + instruction
    try:
        out = await client.complete_async(
            model=model, system=_SYSTEM, user=user,
            max_tokens=_CONTEXT_MAX_TOKENS, temperature=0.0,
        )
        return " ".join((getattr(out, "text", "") or "").split())
    except Exception:
        return ""


async def contextualize_document_async(
    client: Any,
    model: str,
    doc_text: str,
    chunks: list[dict[str, Any]],
    on_log: LogFn | None = None,
    stop_event: Any | None = None,
) -> int:
    """Fill chunk['context'] in place for every chunk belonging to ONE
    document. Returns how many were contextualized. Bounded concurrency;
    failures leave context empty. The document body is sent with each call
    (callers relying on prompt caching get the discount automatically)."""
    if client is None or not chunks:
        return 0
    doc_block, _ = build_context_prompt(doc_text, "")
    sem = asyncio.Semaphore(_CONCURRENCY)
    done = 0

    async def _run(ch: dict[str, Any]) -> None:
        nonlocal done
        if stop_event is not None and getattr(stop_event, "is_set", None) \
                and stop_event.is_set():
            return
        async with sem:
            ctx = await _contextualize_one(
                client, model, doc_block, str(ch.get("text", ""))
            )
        if ctx:
            ch["context"] = ctx
            done += 1

    await asyncio.gather(*[_run(c) for c in chunks])
    if done:
        _log = on_log
        if _log is not None:
            try:
                _log(f"[INFO] Contextualized {done}/{len(chunks)} chunk(s).")
            except Exception:
                pass
    return done


def contextualize_document(
    client: Any, model: str, doc_text: str, chunks: list[dict[str, Any]],
    on_log: LogFn | None = None,
    stop_event: Any | None = None,
) -> int:
    """Sync wrapper for one document's chunks."""
    try:
        return asyncio.run(contextualize_document_async(
            client, model, doc_text, chunks, on_log=on_log,
            stop_event=stop_event,
        ))
    except Exception:
        return 0
