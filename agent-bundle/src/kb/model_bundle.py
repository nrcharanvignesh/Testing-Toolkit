"""
model_bundle.py
Resolve the project-local "models" directory that holds the pre-downloaded
fastembed model cache, so dense embedding and reranking can run FULLY OFFLINE
(no Hugging Face / Google Cloud Storage calls at runtime).

Expected layout (Hugging Face snapshot format, produced by fetch_models.py):

    <project>/models/
        models--qdrant--bge-small-en-v1.5-onnx-q/snapshots/<hash>/...
        models--Xenova--ms-marco-MiniLM-L-6-v2/snapshots/<hash>/...

When the app is frozen by PyInstaller (one-file), the bundled folder is
unpacked under sys._MEIPASS, so the base directory differs from source runs.
This module hides that difference.

Why this is needed (do NOT use HF_HUB_OFFLINE with fastembed):
    Passing local_files_only=True to the model constructor is the correct,
    supported way to force offline load from cache_dir. The HF_HUB_OFFLINE=1
    environment variable is NOT safe with fastembed - it triggers a known
    fallback that tries to download from Google Cloud Storage instead of
    using the local cache (qdrant/fastembed issue 615), which fails on a
    locked-down network.

ASCII only. No third-party imports (pure stdlib), fully type-hinted.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Optional

# Name of the bundled cache folder, relative to the project root / _MEIPASS.
MODELS_DIRNAME: str = "models"

# A valid Hugging Face cache entry is a directory named "models--<org>--<name>".
_HF_CACHE_PREFIX: str = "models--"


def _looks_populated(root: Path) -> bool:
    """True if `root` is a directory containing at least one HF snapshot folder
    (models--<org>--<name>)."""
    try:
        if not root.is_dir():
            return False
        for child in root.iterdir():
            if child.is_dir() and child.name.startswith(_HF_CACHE_PREFIX):
                return True
    except OSError:
        return False
    return False


def _candidate_dirs() -> list[Path]:
    """All locations that might hold the bundled model cache, most explicit
    first.

    The installer copies models to ``<install>/agent/models`` and exports that
    path as ``TT_MODELS_DIR`` while launching the agent from
    ``<install>/agent/src``. Source layouts keep ``models`` next to ``src`` (so
    one level above this package), and PyInstaller unpacks it under _MEIPASS.
    We must check ALL of these - historically only ``src/models`` was checked,
    which never matches the real install layout, so dense embeddings silently
    fell back to a blocked online download. Order matters: the explicit env var
    wins, then the real install/source layouts, then the legacy path.
    """
    cands: list[Path] = []

    # 1) Explicit override set by the installer (authoritative).
    env_dir = (os.environ.get("TT_MODELS_DIR") or "").strip()
    if env_dir:
        cands.append(Path(env_dir).expanduser())

    # 2) PyInstaller one-file unpack dir.
    meipass: Optional[str] = getattr(sys, "_MEIPASS", None)
    if meipass:
        cands.append(Path(meipass) / MODELS_DIRNAME)

    # 3) Real install / source layouts, relative to this file:
    #    .../<root>/src/kb/model_bundle.py
    #    -> parent.parent       == <root>/src        (legacy, rarely correct)
    #    -> parent.parent.parent== <root>            == AGENT_DIR/models  (install)
    here = Path(__file__).resolve()
    src_dir = here.parent.parent          # <root>/src
    root_dir = src_dir.parent             # <root>  (e.g. AGENT_DIR)
    cands.append(root_dir / MODELS_DIRNAME)   # AGENT_DIR/models (install layout)
    cands.append(src_dir / MODELS_DIRNAME)    # src/models (legacy fallback)

    # 4) Install dir hint, if provided without an explicit models path.
    install_dir = (os.environ.get("TT_INSTALL_DIR") or "").strip()
    if install_dir:
        cands.append(Path(install_dir).expanduser() / "agent" / MODELS_DIRNAME)

    # De-duplicate while preserving order.
    seen: set[str] = set()
    unique: list[Path] = []
    for c in cands:
        key = str(c)
        if key not in seen:
            seen.add(key)
            unique.append(c)
    return unique


def bundled_models_dir() -> Optional[str]:
    """Absolute path to the local model cache if it exists and looks populated.

    Returns the first candidate directory (str) that contains at least one
    Hugging Face snapshot folder, else None so callers fall back to the default
    online download path and degrade gracefully to lexical retrieval.
    """
    for root in _candidate_dirs():
        if _looks_populated(root):
            return str(root)
    return None


def has_model(repo_cache_name: str) -> bool:
    """True if a specific "models--<org>--<name>" folder is present and has a
    non-empty snapshot. Used for diagnostics, not required for loading.
    """
    root_str: Optional[str] = bundled_models_dir()
    if root_str is None:
        return False
    snapshots: Path = Path(root_str) / repo_cache_name / "snapshots"
    if not snapshots.is_dir():
        return False
    for snap in snapshots.iterdir():
        if snap.is_dir() and any(snap.iterdir()):
            return True
    return False
