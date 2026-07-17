"""
ado_extract.py
Async ADO work item extractor. Refactored to accept a RuntimeConfig
instance instead of reading module-level environment variables.

Output layout per work item:
  <work_dir>/<wi_id>/
      _meta.json
      _description.txt / _description.html
      _ac.txt / _ac.html
      _comments.txt / _comments.json
      attachments/<original_filenames>
      inline_images/<sanitized_filenames>
      _inline_images.json   (mapping URL -> local filename)
"""

from __future__ import annotations

import asyncio
import base64
import gc
import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Final

import httpx
from selectolax.parser import HTMLParser

from core.runtime_config import (
    API_VER_COMMENTS,
    API_VER_WI,
    API_VER_WIQL,
    RuntimeConfig,
)


# ---------------------------------------------------------------------
# Logging (callable can be redirected to GUI)
# ---------------------------------------------------------------------
LogFn = Callable[[str], None]


def _stdout_info(msg: str) -> None:
    print(f"[INFO] {msg}", flush=True)


def _stderr_error(msg: str) -> None:
    print(f"[ERROR] {msg}", file=sys.stderr, flush=True)


def _stdout_success(msg: str) -> None:
    print(f"[SUCCESS] {msg}", flush=True)


# Module-level redirectable hooks
log_info: LogFn = _stdout_info
log_error: LogFn = _stderr_error
log_success: LogFn = _stdout_success


def set_loggers(info: LogFn, error: LogFn, success: LogFn) -> None:
    global log_info, log_error, log_success
    log_info = info
    log_error = error
    log_success = success


# ---------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------
@dataclass(slots=True)
class WIPaths:
    wi_id: int
    root: Path
    attachments_dir: Path
    meta_json: Path
    desc_txt: Path
    ac_txt: Path
    comments_txt: Path

    @classmethod
    def for_id(cls, wi_id: int, work_dir: Path) -> "WIPaths":
        root = work_dir / str(wi_id)
        return cls(
            wi_id=wi_id,
            root=root,
            attachments_dir=root / "attachments",
            meta_json=root / "_meta.json",
            desc_txt=root / "_description.txt",
            ac_txt=root / "_ac.txt",
            comments_txt=root / "_comments.txt",
        )

    def ensure(self) -> None:
        self.attachments_dir.mkdir(parents=True, exist_ok=True)


@dataclass(slots=True)
class ExtractResult:
    wi_id: int
    ok: bool
    title: str = ""
    board_lane: str = ""
    n_attachments: int = 0
    n_comments: int = 0
    error: str = ""


# ---------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------
def build_auth_header(pat: str) -> dict[str, str]:
    token = base64.b64encode(f":{pat}".encode("ascii")).decode("ascii")
    return {"Authorization": f"Basic {token}", "Accept": "application/json"}


# ---------------------------------------------------------------------
# HTML to text
# ---------------------------------------------------------------------
_BLOCK_TAG_RE: Final[re.Pattern[str]] = re.compile(
    r"</?(p|div|br|li|tr|h[1-6]|blockquote)[^>]*>", re.IGNORECASE
)
_LIST_ITEM_RE: Final[re.Pattern[str]] = re.compile(r"<li[^>]*>", re.IGNORECASE)
_MULTI_NL_RE: Final[re.Pattern[str]] = re.compile(r"\n\s*\n+")
_MULTI_WS_RE: Final[re.Pattern[str]] = re.compile(r"[ \t]+")
_IMG_TAG_RE: Final[re.Pattern[str]] = re.compile(r"<img[^>]*?>", re.IGNORECASE)
_IMG_SRC_ATTR_RE: Final[re.Pattern[str]] = re.compile(
    r"""src=["']([^"']+)["']""", re.IGNORECASE
)
_DATA_URI_RE: Final[re.Pattern[str]] = re.compile(
    r"^data:image/([a-z0-9+]+);base64,(.+)$", re.IGNORECASE | re.DOTALL
)


def html_to_text(html: str | None) -> str:
    if not html or not html.strip():
        return ""
    pre = _LIST_ITEM_RE.sub("\n- ", html)
    pre = _BLOCK_TAG_RE.sub("\n", pre)
    tree = HTMLParser(pre)
    raw = tree.text(separator=" ").strip()
    raw = _MULTI_WS_RE.sub(" ", raw)
    raw = _MULTI_NL_RE.sub("\n\n", raw)
    return raw.strip()


def extract_image_urls(html: str | None) -> list[str]:
    if not html:
        return []
    out: list[str] = []
    for m in _IMG_TAG_RE.finditer(html):
        sm = _IMG_SRC_ATTR_RE.search(m.group(0))
        if sm:
            out.append(sm.group(1))
    return out


# ---------------------------------------------------------------------
# HTTP with retry
# ---------------------------------------------------------------------
async def _request_with_retry(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    cfg: RuntimeConfig,
    **kwargs: Any,
) -> httpx.Response:
    last_exc: Exception | None = None
    for attempt in range(cfg.retry_count):
        try:
            r = await client.request(method, url, **kwargs)
            if r.status_code in (429, 503):
                retry_after = float(
                    r.headers.get("Retry-After",
                                  cfg.retry_backoff_sec * (attempt + 1))
                )
                await asyncio.sleep(retry_after)
                continue
            r.raise_for_status()
            return r
        except (httpx.HTTPError, httpx.TimeoutException) as e:
            last_exc = e
            await asyncio.sleep(cfg.retry_backoff_sec * (attempt + 1))
    raise RuntimeError(
        f"Request failed after {cfg.retry_count} attempts: {method} {url}"
    ) from last_exc


# ---------------------------------------------------------------------
# WIQL
# ---------------------------------------------------------------------
async def run_wiql(
    client: httpx.AsyncClient,
    cfg: RuntimeConfig,
    query: str,
) -> list[int]:
    url = (
        f"https://dev.azure.com/{cfg.organization}/{cfg.project}/_apis/wit/wiql"
        f"?api-version={API_VER_WIQL}"
    )
    r = await _request_with_retry(
        client, "POST", url, cfg,
        json={"query": query},
        headers={"Content-Type": "application/json"},
    )
    payload = r.json()
    ids = [int(item["id"]) for item in payload.get("workItems", [])]
    log_info(f"WIQL returned {len(ids)} work item ids")
    return ids


# ---------------------------------------------------------------------
# Work item fetch
# ---------------------------------------------------------------------
async def fetch_work_item(
    client: httpx.AsyncClient, cfg: RuntimeConfig, wi_id: int,
) -> dict[str, Any]:
    url = (
        f"https://dev.azure.com/{cfg.organization}/{cfg.project}"
        f"/_apis/wit/workitems/{wi_id}"
        f"?$expand=all&api-version={API_VER_WI}"
    )
    r = await _request_with_retry(client, "GET", url, cfg)
    return r.json()


async def fetch_comments(
    client: httpx.AsyncClient, cfg: RuntimeConfig, wi_id: int,
) -> list[dict[str, Any]]:
    url = (
        f"https://dev.azure.com/{cfg.organization}/{cfg.project}"
        f"/_apis/wit/workItems/{wi_id}/comments"
        f"?api-version={API_VER_COMMENTS}&$top=200"
    )
    out: list[dict[str, Any]] = []
    next_url: str | None = url
    while next_url:
        r = await _request_with_retry(client, "GET", next_url, cfg)
        payload = r.json()
        out.extend(payload.get("comments", []))
        next_url = payload.get("nextPage") or None
    out.sort(key=lambda c: c.get("createdDate", ""))
    return out


async def download_attachment(
    client: httpx.AsyncClient,
    cfg: RuntimeConfig,
    url: str,
    dest: Path,
) -> int:
    timeout = httpx.Timeout(cfg.download_timeout_sec)
    last_exc: Exception | None = None

    # Pass 1: streaming with retry
    for attempt in range(cfg.retry_count):
        try:
            size = 0
            async with client.stream("GET", url, timeout=timeout) as r:
                r.raise_for_status()
                with dest.open("wb") as f:
                    async for chunk in r.aiter_bytes(chunk_size=65536):
                        f.write(chunk)
                        size += len(chunk)
            return size
        except (httpx.HTTPError, httpx.TimeoutException) as e:
            last_exc = e
            if dest.exists():
                try:
                    dest.unlink()
                except Exception:
                    pass
            if attempt < cfg.retry_count - 1:
                await asyncio.sleep(cfg.retry_backoff_sec * (attempt + 1))

    # Pass 2: non-streaming fallback
    try:
        r = await client.get(url, timeout=timeout)
        r.raise_for_status()
        dest.write_bytes(r.content)
        return len(r.content)
    except Exception as e:
        if dest.exists():
            try:
                dest.unlink()
            except Exception:
                pass
        raise RuntimeError(
            f"Download failed after {cfg.retry_count} streaming attempts and "
            f"1 non-streaming fallback. Last streaming error: {last_exc!r}. "
            f"Fallback error: {e!r}"
        ) from last_exc


def _safe_filename(name: str) -> str:
    bad = '<>:"/\\|?*'
    cleaned = "".join("_" if c in bad else c for c in name)
    cleaned = cleaned.strip(". ")
    return cleaned[:200] if cleaned else "unnamed_attachment"


# ---------------------------------------------------------------------
# Per-WI extract
# ---------------------------------------------------------------------
async def extract_one(
    client: httpx.AsyncClient,
    cfg: RuntimeConfig,
    wi_id: int,
    work_dir: Path,
    sem: asyncio.Semaphore,
    progress_cb: Callable[[int], None] | None = None,
) -> ExtractResult:
    async with sem:
        paths = WIPaths.for_id(wi_id, work_dir)
        paths.ensure()

        try:
            wi, comments = await asyncio.gather(
                fetch_work_item(client, cfg, wi_id),
                fetch_comments(client, cfg, wi_id),
            )
        except Exception as e:
            log_error(f"WI {wi_id} fetch failed: {e!r}")
            if progress_cb:
                progress_cb(wi_id)
            return ExtractResult(wi_id=wi_id, ok=False, error=repr(e))

        fields = wi.get("fields", {}) or {}
        title = str(fields.get("System.Title", "")).strip()
        board_lane = str(
            fields.get("System.BoardColumn") or fields.get("System.State", "")
        ).strip()
        desc_html = fields.get("System.Description", "") or ""
        ac_html = fields.get("Microsoft.VSTS.Common.AcceptanceCriteria", "") or ""

        paths.desc_txt.write_text(html_to_text(desc_html), encoding="utf-8")
        paths.ac_txt.write_text(html_to_text(ac_html), encoding="utf-8")
        (paths.root / "_description.html").write_text(desc_html, encoding="utf-8")
        (paths.root / "_ac.html").write_text(ac_html, encoding="utf-8")

        comment_lines: list[str] = []
        comment_records: list[dict[str, Any]] = []
        for c in comments:
            author = (c.get("createdBy") or {}).get("displayName", "unknown")
            when = c.get("createdDate", "")
            c_html = c.get("text", "") or ""
            text = html_to_text(c_html)
            comment_lines.append(f"[{when} | {author}]")
            comment_lines.append(text)
            comment_lines.append("")
            comment_records.append(
                {"author": author, "when": when, "html": c_html}
            )
        paths.comments_txt.write_text(
            "\n".join(comment_lines).strip() + "\n", encoding="utf-8"
        )
        (paths.root / "_comments.json").write_text(
            json.dumps(comment_records, indent=2, ensure_ascii=True),
            encoding="utf-8",
        )

        # Inline images: collect unique URLs in document order
        inline_urls: list[str] = []
        inline_urls.extend(extract_image_urls(desc_html))
        inline_urls.extend(extract_image_urls(ac_html))
        for rec in comment_records:
            inline_urls.extend(extract_image_urls(rec["html"]))

        seen_urls: set[str] = set()
        unique_inline_urls: list[str] = []
        for u in inline_urls:
            if u not in seen_urls:
                seen_urls.add(u)
                unique_inline_urls.append(u)

        inline_dir = paths.root / "inline_images"
        inline_map: dict[str, str] = {}
        _dl_sem = asyncio.Semaphore(6)

        async def _download_inline(i: int, url: str) -> None:
            m = _DATA_URI_RE.match(url)
            if m:
                ext = m.group(1).lower().replace("+xml", "")
                if ext not in {"png", "jpg", "jpeg", "gif", "bmp",
                               "tiff", "tif", "svg", "webp"}:
                    ext = "png"
                try:
                    data = base64.b64decode(m.group(2))
                    fname = f"_inline_{i:03d}_data.{ext}"
                    (inline_dir / fname).write_bytes(data)
                    inline_map[url] = fname
                except Exception as e:
                    log_error(f"WI {wi_id} bad data URI: {e!r}")
                return
            base = url.split("?")[0].split("/")[-1] or "image.png"
            filename = _safe_filename(f"img_{i:03d}_{base}")
            if not Path(filename).suffix:
                filename += ".png"
            local = inline_dir / filename
            async with _dl_sem:
                try:
                    await download_attachment(client, cfg, url, local)
                    inline_map[url] = filename
                except Exception as e:
                    log_error(f"WI {wi_id} inline image failed: {url} - {e!r}")

        if unique_inline_urls:
            inline_dir.mkdir(parents=True, exist_ok=True)
            await asyncio.gather(
                *(_download_inline(i, u)
                  for i, u in enumerate(unique_inline_urls))
            )

        if unique_inline_urls:
            (paths.root / "_inline_images.json").write_text(
                json.dumps(inline_map, indent=2, ensure_ascii=True),
                encoding="utf-8",
            )

        relations = wi.get("relations", []) or []
        att_relations = [
            r for r in relations
            if r.get("rel") == "AttachedFile" and r.get("url")
        ]

        # Pre-compute unique dest paths (sequential to avoid collisions),
        # then download concurrently.
        att_tasks: list[tuple[str, Path]] = []
        for rel in att_relations:
            att_name = _safe_filename(
                (rel.get("attributes") or {}).get("name", "attachment.bin")
            )
            dest = paths.attachments_dir / att_name
            i = 1
            while dest.exists() or any(d == dest for _, d in att_tasks):
                stem, ext = os.path.splitext(att_name)
                dest = paths.attachments_dir / f"{stem}_{i}{ext}"
                i += 1
            att_tasks.append((rel["url"], dest))

        n_ok = 0

        async def _download_att(url: str, dest: Path) -> bool:
            async with _dl_sem:
                try:
                    await download_attachment(client, cfg, url, dest)
                    return True
                except Exception as e:
                    log_error(
                        f"WI {wi_id} attachment '{dest.name}' failed: {e!r}"
                    )
                    return False

        if att_tasks:
            results = await asyncio.gather(
                *(_download_att(u, d) for u, d in att_tasks)
            )
            n_ok = sum(1 for r in results if r)

        n_comments_count = len(comments) if isinstance(comments, list) else 0

        wi_url = f"https://dev.azure.com/{cfg.organization}/_workitems/edit/{wi_id}"
        meta = {
            "wi_id": wi_id,
            "wi_url": wi_url,
            "title": title,
            "type": fields.get("System.WorkItemType", ""),
            "state": fields.get("System.State", ""),
            "board_lane": board_lane,
            "iteration": fields.get("System.IterationPath", ""),
            "area": fields.get("System.AreaPath", ""),
            "n_attachments_listed": len(att_relations),
            "n_attachments_downloaded": n_ok,
            "n_comments": n_comments_count,
        }
        paths.meta_json.write_text(
            json.dumps(meta, indent=2, ensure_ascii=True), encoding="utf-8"
        )

        del wi, comments, relations, att_relations
        gc.collect()

        if progress_cb:
            progress_cb(wi_id)

        return ExtractResult(
            wi_id=wi_id,
            ok=True,
            title=title,
            board_lane=board_lane,
            n_attachments=n_ok,
            n_comments=n_comments_count,
        )


# ---------------------------------------------------------------------
# Batch entry
# ---------------------------------------------------------------------
async def extract_many(
    cfg: RuntimeConfig,
    progress_cb: Callable[[int], None] | None = None,
) -> list[ExtractResult]:
    if not cfg.work_item_ids:
        log_error("No work item ids supplied")
        return []
    cfg.work_dir.mkdir(parents=True, exist_ok=True)

    headers = build_auth_header(cfg.pat)
    timeout = httpx.Timeout(cfg.http_timeout_sec)
    limits = httpx.Limits(
        max_connections=cfg.concurrency * 2,
        max_keepalive_connections=cfg.concurrency,
    )
    sem = asyncio.Semaphore(cfg.concurrency)

    results: list[ExtractResult] = []
    async with httpx.AsyncClient(
        headers=headers, timeout=timeout, limits=limits, http2=False,
        verify=cfg.build_ssl(),
    ) as client:
        tasks = [
            extract_one(client, cfg, wid, cfg.work_dir, sem, progress_cb)
            for wid in cfg.work_item_ids
        ]
        for coro in asyncio.as_completed(tasks):
            res = await coro
            results.append(res)

    ok = sum(1 for r in results if r.ok)
    log_success(f"Extracted {ok}/{len(results)} work items into {cfg.work_dir}")
    return results
