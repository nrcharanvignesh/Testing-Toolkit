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

import sys
from pathlib import Path
from typing import Optional

# Name of the bundled cache folder, relative to the project root / _MEIPASS.
MODELS_DIRNAME: str = "models"

# A valid Hugging Face cache entry is a directory named "models--<org>--<name>".
_HF_CACHE_PREFIX: str = "models--"


def _base_dir() -> Path:
    """Project root in source runs, or the PyInstaller unpack dir when frozen."""
    meipass: Optional[str] = getattr(sys, "_MEIPASS", None)
    if meipass:
        return Path(meipass)
    return Path(__file__).resolve().parent.parent


def bundled_models_dir() -> Optional[str]:
    """Absolute path to the local model cache if it exists and looks populated.

    Returns the directory path (str) when it contains at least one Hugging
    Face snapshot folder, else None so callers fall back to the default
    online download path and degrade gracefully to lexical retrieval.
    """
    root: Path = _base_dir() / MODELS_DIRNAME
    if not root.is_dir():
        return None
    for child in root.iterdir():
        if child.is_dir() and child.name.startswith(_HF_CACHE_PREFIX):
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
