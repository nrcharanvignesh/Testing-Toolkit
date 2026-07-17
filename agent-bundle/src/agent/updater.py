"""Non-destructive agent version detection.

The agent reads the install-time update configuration and compares its running
version with the published manifest. It deliberately cannot download files,
install dependencies, patch source, or restart the process. New versions are
installed through the normal installer so settings and project data remain
under the installer's preservation contract.
"""

from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Any

import httpx

from agent.version import AGENT_VERSION

DEFAULT_REPO = "nrcharanvignesh/Testing-Toolkit"
DEFAULT_REF = "parts"

_CACHE_TTL_SEC: int = 300
_cached_config: dict[str, Any] | None = None
_cached_config_mtime: float = 0.0
_cached_result: dict[str, Any] | None = None
_cached_result_time: float = 0.0


def install_dir() -> Path:
    """Directory holding the agent's install-time update configuration."""
    return Path(
        os.environ.get("TT_INSTALL_DIR", Path.home() / "TestingToolkitWeb")
    ).expanduser()


def _config_path() -> Path:
    return install_dir() / "update.json"


def _load_config() -> dict[str, Any]:
    global _cached_config, _cached_config_mtime
    path = _config_path()
    try:
        mtime = path.stat().st_mtime
        if _cached_config is not None and mtime == _cached_config_mtime:
            return _cached_config
        data = json.loads(path.read_text(encoding="utf-8"))
        result = data if isinstance(data, dict) else {}
        _cached_config = result
        _cached_config_mtime = mtime
        return result
    except Exception:
        return {}


def _manifest_url_for(repo: str, ref: str) -> str:
    repo = (repo or DEFAULT_REPO).strip()
    ref = (ref or DEFAULT_REF).strip()
    return f"https://api.github.com/repos/{repo}/contents/agent-update.json?ref={ref}"


def resolve_manifest_url() -> str:
    """Resolve the manifest URL without changing the installation."""
    config = _load_config()
    explicit = str(
        config.get("manifest_url", "")
        or os.environ.get("AGENT_MANIFEST_URL", "")
    ).strip()
    if explicit:
        return explicit

    token = str(
        config.get("token", "") or os.environ.get("TT_UPDATE_TOKEN", "")
    ).strip()
    if not token:
        return ""
    return _manifest_url_for(
        str(config.get("repo", "") or os.environ.get("TT_UPDATE_REPO", "")),
        str(config.get("ref", "") or os.environ.get("TT_UPDATE_REF", "")),
    )


def _auth_headers() -> dict[str, str]:
    config = _load_config()
    token = str(
        config.get("token", "") or os.environ.get("TT_UPDATE_TOKEN", "")
    ).strip()
    headers = {
        "Accept": "application/vnd.github.raw",
        "User-Agent": "TestingToolkit-Agent",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _fetch_manifest(url: str) -> dict[str, Any] | None:
    try:
        response = httpx.get(
            url,
            headers=_auth_headers(),
            timeout=10,
            follow_redirects=True,
        )
        if response.status_code != 200:
            return None
        data = response.json()
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def _version_tuple(v: str) -> tuple[int, ...]:
    parts: list[int] = []
    for chunk in str(v).strip().split("."):
        m = re.match(r"\d+", chunk)
        parts.append(int(m.group()) if m else 0)
    return tuple(parts) or (0,)


def _is_newer(latest: str, current: str) -> bool:
    """True only when ``latest`` is STRICTLY greater than ``current``.

    Using a semantic comparison (not ``!=``) means republishing a manifest at
    an equal or older version never prompts a reinstall. Web/UI-only releases
    deploy through Vercel and deliberately do not republish the manifest, so
    installed agents are never nagged to reinstall for a label change.
    """
    a, b = _version_tuple(latest), _version_tuple(current)
    n = max(len(a), len(b))
    a += (0,) * (n - len(a))
    b += (0,) * (n - len(b))
    return a > b


def _is_patch_only(latest: str, current: str) -> bool:
    """True when the version difference is patch-level only (same major.minor).
    Patch updates can be applied via source overlay without full reinstall."""
    a = _version_tuple(latest)
    b = _version_tuple(current)
    if len(a) < 3 or len(b) < 3:
        return False
    return a[0] == b[0] and a[1] == b[1] and a[2] > b[2]


def _invalidate_update_cache() -> None:
    """Clear the cached update check result. Called by tests."""
    global _cached_result, _cached_result_time
    _cached_result = None
    _cached_result_time = 0.0


def check_for_update() -> dict[str, Any]:
    """Report current vs. available version without mutating the installation.

    Results are cached for _CACHE_TTL_SEC to avoid hammering GitHub API on
    repeated UI polls.
    """
    global _cached_result, _cached_result_time
    now = time.monotonic()
    if _cached_result is not None and (now - _cached_result_time) < _CACHE_TTL_SEC:
        return _cached_result
    manifest_url = resolve_manifest_url()
    manifest = _fetch_manifest(manifest_url) if manifest_url else None
    latest = str(manifest.get("version", "")).strip() if manifest else ""
    newer = bool(latest and _is_newer(latest, AGENT_VERSION))
    patch = bool(latest and newer and _is_patch_only(latest, AGENT_VERSION))
    result = {
        "current": AGENT_VERSION,
        "latest": latest or None,
        "update_available": newer,
        "patch_only": patch,
        "configured": bool(manifest_url),
        "reachable": manifest is not None,
        "install_dir": str(install_dir()),
    }
    _cached_result = result
    _cached_result_time = now
    return result


def apply_patch() -> dict[str, Any]:
    """Download and apply a patch-only update via source overlay.

    Only proceeds when the manifest version is patch-level above the current.
    Downloads each changed file from GitHub, verifies SHA-256, writes in-place.
    Returns a result dict with success/failure and files changed."""
    import hashlib
    import logging
    import sys

    log = logging.getLogger(__name__)
    manifest_url = resolve_manifest_url()
    if not manifest_url:
        return {"ok": False, "error": "update not configured"}

    manifest = _fetch_manifest(manifest_url)
    if not manifest:
        return {"ok": False, "error": "cannot reach update server"}

    latest = str(manifest.get("version", "")).strip()
    if not latest or not _is_newer(latest, AGENT_VERSION):
        return {"ok": False, "error": "already up to date"}
    if not _is_patch_only(latest, AGENT_VERSION):
        return {"ok": False, "error": "not a patch update; reinstall required"}

    files = manifest.get("files", [])
    if not files:
        return {"ok": False, "error": "manifest has no files"}

    src_dir = Path(sys.modules["agent"].__file__ or "").resolve().parent.parent
    headers = _auth_headers()
    headers["Accept"] = "application/vnd.github.raw"
    errors: list[str] = []

    # Phase 1: download + verify ALL files before writing anything.
    staged: list[tuple[Path, bytes]] = []
    for entry in files:
        rel_path = str(entry.get("path", ""))
        url = str(entry.get("url", ""))
        expected_hash = str(entry.get("hash", ""))
        if not rel_path or not url or not expected_hash:
            continue

        target = src_dir / rel_path
        if not target.exists():
            continue

        try:
            resp = httpx.get(url, headers=headers, timeout=30,
                             follow_redirects=True)
            if resp.status_code != 200:
                errors.append(f"{rel_path}: HTTP {resp.status_code}")
                continue
            content = resp.content
            actual_hash = hashlib.sha256(content).hexdigest()
            if actual_hash != expected_hash:
                errors.append(f"{rel_path}: hash mismatch")
                continue
            staged.append((target, content))
        except Exception as e:
            errors.append(f"{rel_path}: {type(e).__name__}")

    if not staged:
        return {"ok": False, "error": f"all files failed: {errors}"}

    # Phase 2: apply with rollback on failure.
    backups: list[tuple[Path, bytes]] = []
    applied: list[str] = []
    try:
        for target, content in staged:
            backups.append((target, target.read_bytes()))
            target.write_bytes(content)
            applied.append(str(target.relative_to(src_dir)))
    except Exception as e:
        # Rollback already-written files.
        for bk_path, bk_data in backups:
            try:
                bk_path.write_bytes(bk_data)
            except Exception:
                pass
        return {"ok": False, "error": f"patch aborted, rolled back: {e}"}

    _invalidate_update_cache()
    log.info("[INFO] Patch %s applied: %d file(s), %d skipped",
             latest, len(applied), len(errors))
    return {
        "ok": True,
        "version": latest,
        "applied": applied,
        "errors": errors,
        "restart_required": True,
    }
