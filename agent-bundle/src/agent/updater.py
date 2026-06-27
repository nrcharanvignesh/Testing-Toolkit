"""
updater.py
Background self-update mechanism. Checks manifest every 60 seconds.
Downloads delta files and restarts the agent process when updates are available.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

import httpx

from agent.version import AGENT_VERSION

MANIFEST_URL: str = ""  # Set at startup from env or config
CHECK_INTERVAL_SEC: int = 60
_stop_event = threading.Event()


def start_update_loop(manifest_url: str) -> None:
    """Start the background update checker thread."""
    global MANIFEST_URL
    MANIFEST_URL = manifest_url
    t = threading.Thread(target=_update_loop, daemon=True, name="updater")
    t.start()


def _update_loop() -> None:
    while not _stop_event.is_set():
        try:
            _check_and_apply()
        except Exception:
            pass
        _stop_event.wait(CHECK_INTERVAL_SEC)


def _check_and_apply() -> None:
    if not MANIFEST_URL:
        return

    try:
        resp = httpx.get(MANIFEST_URL, timeout=10)
        if resp.status_code != 200:
            return
        manifest: dict[str, Any] = resp.json()
    except Exception:
        return

    remote_version = manifest.get("version", "")
    if not remote_version or remote_version == AGENT_VERSION:
        return

    force = manifest.get("force", False)
    files = manifest.get("files", [])

    if not files:
        return

    # Download changed files
    agent_dir = Path(__file__).resolve().parent
    for file_info in files:
        rel_path = file_info.get("path", "")
        url = file_info.get("url", "")
        expected_hash = file_info.get("hash", "")
        if not rel_path or not url:
            continue

        try:
            r = httpx.get(url, timeout=30)
            if r.status_code != 200:
                continue
            content = r.content

            if expected_hash:
                actual_hash = hashlib.sha256(content).hexdigest()
                if actual_hash != expected_hash:
                    continue

            dest = agent_dir.parent / rel_path
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(content)
        except Exception:
            continue

    # Restart the agent process
    _restart()


def _restart() -> None:
    """Restart the agent process."""
    python = sys.executable
    args = [python, "-m", "agent"]
    if sys.platform == "win32":
        subprocess.Popen(args, creationflags=subprocess.CREATE_NEW_PROCESS_GROUP)
        os._exit(0)
    else:
        os.execv(python, args)


def stop() -> None:
    _stop_event.set()
