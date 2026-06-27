"""
prefs_store.py
Non-secret preference storage. Plain JSON in user home dir.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Final

PREFS_PATH: Final[Path] = Path.home() / ".utility_tools_prefs.json"

KEY_ORG: Final[str] = "organization"
KEY_PROJECT: Final[str] = "project"
KEY_OUTPUT_DIR: Final[str] = "output_dir"
KEY_THEME: Final[str] = "theme"          # 'light' | 'dark'
KEY_LAST_TAB: Final[str] = "last_tab"


def _load_all() -> dict[str, str]:
    if not PREFS_PATH.exists():
        return {}
    try:
        data = json.loads(PREFS_PATH.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return {str(k): str(v) for k, v in data.items()}
    except Exception:
        pass
    return {}


def _write_all(data: dict[str, str]) -> bool:
    try:
        PREFS_PATH.write_text(
            json.dumps(data, indent=2, ensure_ascii=True),
            encoding="utf-8",
        )
        return True
    except Exception:
        return False


def get_pref(key: str, default: str = "") -> str:
    return _load_all().get(key, default).strip()


def save_pref(key: str, value: str) -> bool:
    if value is None:
        return False
    data = _load_all()
    data[key] = str(value).strip()
    return _write_all(data)


def clear_pref(key: str) -> bool:
    data = _load_all()
    if key in data:
        del data[key]
        return _write_all(data)
    return True
