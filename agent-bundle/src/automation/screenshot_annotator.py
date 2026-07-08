"""
automation/screenshot_annotator.py
Annotates screenshots with bounding boxes, step numbers, and pass/fail indicators.
Uses Pillow (PIL).
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


# -- Color constants (RGB) --
_COLOR_PASS = (0, 180, 0)
_COLOR_FAIL = (220, 30, 30)
_COLOR_ERROR = (220, 120, 0)
_COLOR_SKIP = (128, 128, 128)
_COLOR_WHITE = (255, 255, 255)
_COLOR_BLACK = (0, 0, 0)

_STATUS_COLORS: dict[str, tuple[int, int, int]] = {
    "pass": _COLOR_PASS,
    "fail": _COLOR_FAIL,
    "error": _COLOR_ERROR,
    "skip": _COLOR_SKIP,
}

# Badge dimensions
_BADGE_WIDTH = 160
_BADGE_HEIGHT = 32
_BADGE_MARGIN = 8


def _get_font(size: int = 14) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    """Load a monospace font, fallback to default."""
    try:
        return ImageFont.truetype("consola.ttf", size)
    except (OSError, IOError):
        try:
            return ImageFont.truetype("cour.ttf", size)
        except (OSError, IOError):
            return ImageFont.load_default()


def annotate_screenshot(
    screenshot_path: Path,
    step_num: int,
    status: str,
    bounding_box: tuple[int, int, int, int] | None = None,
    label: str = "",
) -> Path:
    """Annotate a screenshot with step info and save alongside original.

    Draws:
    - Bounding box (colored by status) if provided.
    - Step number badge in top-left corner.
    - Pass/fail indicator badge in top-right corner.
    - Optional label text below the step badge.

    Args:
        screenshot_path: Path to the raw screenshot PNG.
        step_num: Step number to display.
        status: "pass" | "fail" | "error" | "skip"
        bounding_box: Optional (x, y, width, height) to highlight.
        label: Optional text label for the step action.

    Returns:
        Path to the annotated screenshot file.
    """
    if not screenshot_path.exists():
        return screenshot_path

    img = Image.open(screenshot_path)
    draw = ImageDraw.Draw(img)
    font = _get_font(14)
    font_small = _get_font(12)
    color = _STATUS_COLORS.get(status, _COLOR_SKIP)

    # -- Bounding box --
    if bounding_box is not None:
        x, y, w, h = bounding_box
        for offset in range(3):  # 3px thick border
            draw.rectangle(
                [x - offset, y - offset, x + w + offset, y + h + offset],
                outline=color,
            )

    # -- Step number badge (top-left) --
    badge_x = _BADGE_MARGIN
    badge_y = _BADGE_MARGIN
    draw.rectangle(
        [badge_x, badge_y, badge_x + _BADGE_WIDTH, badge_y + _BADGE_HEIGHT],
        fill=color,
    )
    step_text = f" Step {step_num}"
    draw.text(
        (badge_x + 4, badge_y + 6),
        step_text,
        fill=_COLOR_WHITE,
        font=font,
    )

    # -- Status badge (top-right) --
    img_width = img.width
    status_badge_x = img_width - _BADGE_WIDTH - _BADGE_MARGIN
    status_badge_y = _BADGE_MARGIN
    draw.rectangle(
        [status_badge_x, status_badge_y,
         status_badge_x + _BADGE_WIDTH, status_badge_y + _BADGE_HEIGHT],
        fill=color,
    )
    status_text = f" {status.upper()}"
    draw.text(
        (status_badge_x + 4, status_badge_y + 6),
        status_text,
        fill=_COLOR_WHITE,
        font=font,
    )

    # -- Label below step badge --
    if label:
        label_y = badge_y + _BADGE_HEIGHT + 4
        # Background for readability
        draw.rectangle(
            [badge_x, label_y, badge_x + _BADGE_WIDTH * 2, label_y + 20],
            fill=(0, 0, 0, 180) if img.mode == "RGBA" else _COLOR_BLACK,
        )
        draw.text(
            (badge_x + 4, label_y + 2),
            label[:60],
            fill=_COLOR_WHITE,
            font=font_small,
        )

    # -- Save annotated copy --
    stem = screenshot_path.stem
    annotated_path = screenshot_path.parent / f"{stem}_annotated.png"
    img.save(annotated_path, "PNG")
    return annotated_path
