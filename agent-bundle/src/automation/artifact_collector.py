"""
automation/artifact_collector.py
Manages artifact output directory structure and collection.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any


class ArtifactCollector:
    """Creates and manages artifact directory structure for a test case.

    Structure:
        output_dir/{tc_id}/screenshots/
        output_dir/{tc_id}/video/
        output_dir/{tc_id}/scripts/
    """

    def __init__(self, output_dir: Path, tc_id: str) -> None:
        self._base = output_dir / tc_id
        self._screenshot_dir = self._base / "screenshots"
        self._video_dir = self._base / "video"
        self._script_dir = self._base / "scripts"

        # Create all dirs upfront
        self._screenshot_dir.mkdir(parents=True, exist_ok=True)
        self._video_dir.mkdir(parents=True, exist_ok=True)
        self._script_dir.mkdir(parents=True, exist_ok=True)

    @property
    def base_dir(self) -> Path:
        return self._base

    @property
    def screenshot_dir(self) -> Path:
        return self._screenshot_dir

    @property
    def video_dir(self) -> Path:
        return self._video_dir

    @property
    def script_dir(self) -> Path:
        return self._script_dir

    def collect_video(self) -> Path | None:
        """Find and return the first video file in the video directory.

        Playwright saves videos as .webm files in the record_video_dir.
        Returns the path if found, else None.
        """
        video_files = list(self._video_dir.glob("*.webm"))
        if video_files:
            return video_files[0]
        return None

    def save_script(self, script_content: str, tc_id: str) -> Path:
        """Write a generated script to the scripts directory.

        Args:
            script_content: Python script source.
            tc_id: Test case ID (used in filename).

        Returns:
            Path to the written script file.
        """
        filename = f"{tc_id}_replay.py"
        path = self._script_dir / filename
        path.write_text(script_content, encoding="utf-8")
        return path

    def list_screenshots(self) -> list[Path]:
        """Return all screenshot files sorted by name."""
        files = list(self._screenshot_dir.glob("*.png"))
        return sorted(files)

    def manifest(self) -> dict[str, Any]:
        """Return a manifest dict summarizing collected artifacts.

        Keys: screenshots (list of paths), video (path or None),
              scripts (list of paths), base_dir.
        """
        screenshots = self.list_screenshots()
        video = self.collect_video()
        scripts = sorted(self._script_dir.glob("*.py"))

        return {
            "base_dir": str(self._base),
            "screenshots": [str(p) for p in screenshots],
            "video": str(video) if video else None,
            "scripts": [str(p) for p in scripts],
            "screenshot_count": len(screenshots),
            "has_video": video is not None,
            "script_count": len(scripts),
        }
