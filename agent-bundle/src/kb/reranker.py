"""
reranker.py
Final-stage precision reranking via the GenAI gateway.

Primary path: the gateway's native POST /rerank endpoint
(azure.cohere-rerank-v3-english by default), a purpose-built cross-encoder
that is faster and cheaper than asking a chat model to order passages.

Fallback: llm_rerank() asks a chat model to order candidate ids (one
completion call). Callers use native_rerank() first and fall back to
llm_rerank() only when /rerank errors, and finally keep the fused (RRF)
order if both fail.

All reranking is API-based; there is no local model. ASCII-only; fully
type-hinted.
"""

from __future__ import annotations

import json
import re
import ssl as _ssl
from typing import Any, Final, Protocol

import httpx

_INT_RE: Final[re.Pattern[str]] = re.compile(r"\d+")
_RERANK_PATH: Final[str] = "/rerank"


class Reranker(Protocol):
    name: str

    def rerank(
        self, query: str, candidates: list[tuple[str, str]], top_k: int,
    ) -> list[tuple[str, str]]:
        """candidates: list of (id, text). Returns (id, score) best-first."""
        ...


# ---------------------------------------------------------------------
# Native gateway /rerank (cross-encoder). Preferred path.
# ---------------------------------------------------------------------
def native_rerank(
    base_url: str,
    api_key: str,
    model: str,
    query: str,
    candidates: list[tuple[str, str]],
    top_k: int,
    ssl_verify: Any = True,
    timeout_sec: float = 30.0,
) -> list[tuple[str, float]] | None:
    """Rerank candidates via the gateway POST /rerank endpoint.

    Sends documents as index-tagged objects so the response order can be
    mapped back to candidate ids regardless of whether the gateway echoes
    the document text. Returns (id, relevance_score) best-first, or None on
    any failure so the caller can fall back to the LLM reranker / RRF order.
    """
    if not (api_key or "").strip() or not candidates:
        return None
    url = f"{(base_url or '').rstrip('/')}{_RERANK_PATH}"
    # id -> index; the gateway returns results by index (or echoes id).
    docs = [
        {"text": " ".join((text or "").split())[:2000], "id": str(i)}
        for i, (_cid, text) in enumerate(candidates)
    ]
    body: dict[str, Any] = {
        "model": model,
        "query": query,
        "documents": docs,
        "top_n": max(1, int(top_k)),
        "return_documents": False,
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "x-api-key": api_key,
        "content-type": "application/json",
        "accept": "application/json",
    }
    try:
        with httpx.Client(
            headers=headers, verify=ssl_verify,
            timeout=httpx.Timeout(timeout_sec),
        ) as client:
            resp = client.post(url, json=body)
    except (httpx.HTTPError, _ssl.SSLError):
        return None
    if resp.status_code != 200:
        return None
    try:
        data = resp.json()
    except Exception:
        return None

    results = data.get("results")
    if not isinstance(results, list) or not results:
        return None

    out: list[tuple[str, float]] = []
    for r in results:
        if not isinstance(r, dict):
            continue
        # Prefer explicit id; fall back to the numeric index key the gateway
        # may return as "index".
        raw_id = r.get("id")
        idx: int | None = None
        if raw_id is not None:
            try:
                idx = int(str(raw_id))
            except (TypeError, ValueError):
                idx = None
        if idx is None and "index" in r:
            try:
                idx = int(r["index"])
            except (TypeError, ValueError):
                idx = None
        if idx is None or not (0 <= idx < len(candidates)):
            continue
        score = r.get("relevance_score", 0.0)
        try:
            score_f = float(score)
        except (TypeError, ValueError):
            score_f = 0.0
        out.append((candidates[idx][0], score_f))
    if not out:
        return None
    out.sort(key=lambda kv: kv[1], reverse=True)
    return out[:max(0, int(top_k))]


# ---------------------------------------------------------------------
# Fallback: LLM-as-reranker (completions API only). Use on a SMALL
# candidate set; it costs one API call. Returns ids best-first.
# ---------------------------------------------------------------------
def llm_rerank(
    client: Any,
    model: str,
    query: str,
    candidates: list[tuple[str, str]],
    top_k: int,
) -> list[str] | None:
    """Ask the LLM to order candidate ids by relevance to the query. Returns
    an ordered list of ids, or None on any failure (caller keeps RRF order).
    Deterministic prompt; temperature 0.0."""
    if client is None or not candidates:
        return None
    listing = []
    for idx, (_cid, text) in enumerate(candidates):
        snippet = " ".join((text or "").split())[:500]
        listing.append(f"[{idx}] {snippet}")
    user = (
        "You are ranking passages by how well they help answer the QUERY.\n"
        "Return ONLY a JSON array of passage indices, most relevant first, "
        "including only passages that are actually relevant.\n\n"
        f"QUERY:\n{query}\n\nPASSAGES:\n" + "\n".join(listing)
    )
    try:
        _ = client.complete_async  # presence check only
    except Exception:
        return None
    try:
        import asyncio

        coro = client.complete_async(
            model=model,
            system="You are a precise passage reranker. Output JSON only.",
            user=user, max_tokens=256, temperature=0.0,
        )
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if loop and loop.is_running():
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor(1) as ex:
                out = ex.submit(asyncio.run, coro).result(timeout=30)
        else:
            out = asyncio.run(coro)
        text = getattr(out, "text", "") or ""
        m = re.search(r"\[[^\]]*\]", text)
        if not m:
            return None
        order = json.loads(m.group(0))
        ids: list[str] = []
        for v in order:
            try:
                i = int(v)
            except (TypeError, ValueError):
                continue
            if 0 <= i < len(candidates):
                ids.append(candidates[i][0])
        return ids[:max(0, int(top_k))] or None
    except Exception:
        return None
