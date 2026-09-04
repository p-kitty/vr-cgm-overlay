"""Watch face drawing.

Produces the RGBA image that gets pushed to the VR overlay. It needs no
headset, so tools/preview.py can render every state to a file.

The layout is built around being readable in half a second out of the
corner of your eye:
  - the number is as large as it fits
  - out-of-range shows as colour, so it reads before the digits do
  - stale data goes grey, so an old value is never mistaken for a live one
All text is ASCII, so it survives fonts without CJK glyphs.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

log = logging.getLogger(__name__)

WIDTH, HEIGHT = 512, 256

# Tried in order; all ship with Windows.
FONT_CANDIDATES = [
    "C:/Windows/Fonts/segoeuib.ttf",  # Segoe UI Bold
    "C:/Windows/Fonts/arialbd.ttf",   # Arial Bold
    "C:/Windows/Fonts/DejaVuSans-Bold.ttf",
]


@dataclass
class Theme:
    """Colours and thresholds. Thresholds are mg/dL even in mmol mode."""

    low_mgdl: float = 70.0
    high_mgdl: float = 180.0
    very_high_mgdl: float = 240.0

    color_in_range: tuple[int, int, int] = (126, 231, 135)   # green
    color_low: tuple[int, int, int] = (255, 107, 107)        # red
    color_high: tuple[int, int, int] = (255, 230, 64)        # yellow
    color_very_high: tuple[int, int, int] = (255, 143, 38)   # deep orange
    color_stale: tuple[int, int, int] = (150, 150, 155)      # grey
    color_bg: tuple[int, int, int, int] = (14, 16, 22, 225)  # translucent black

    def status_color(self, mgdl: float) -> tuple[int, int, int]:
        """Pick the status colour for a reading.

        High is split in two so "drifting over range" and "far over range"
        do not look alike: yellow up to very_high, deep orange past it.
        """
        if mgdl < self.low_mgdl:
            return self.color_low
        if mgdl > self.very_high_mgdl:
            return self.color_very_high
        if mgdl > self.high_mgdl:
            return self.color_high
        return self.color_in_range


# TrendArrow value -> arrow angle in degrees. 0 points right, positive up.
# Arrows are drawn rather than typeset: Segoe UI and the other stock
# Windows fonts have no U+2197/U+2198 glyphs and render tofu boxes.
TREND_ANGLES = {1: -90.0, 2: -45.0, 3: 0.0, 4: 45.0, 5: 90.0}


def _draw_arrow(
    draw: ImageDraw.ImageDraw,
    center: tuple[float, float],
    angle_deg: float,
    length: float,
    color: tuple[int, int, int],
    width: int = 13,
) -> None:
    """Draw an arrow at the given angle as vector shapes.

    Screen Y grows downwards, so the sine is negated to make a positive
    angle point up.
    """
    rad = math.radians(angle_deg)
    dx, dy = math.cos(rad), -math.sin(rad)
    px, py = -dy, dx  # unit vector perpendicular to travel

    cx, cy = center
    half = length / 2
    tail = (cx - dx * half, cy - dy * half)
    tip = (cx + dx * half, cy + dy * half)

    head_len = length * 0.42
    head_half_width = length * 0.30

    # Stop the shaft short of the head so it does not poke out of the tip.
    shaft_end = (tip[0] - dx * head_len * 0.75, tip[1] - dy * head_len * 0.75)
    draw.line([tail, shaft_end], fill=color, width=width)

    base = (tip[0] - dx * head_len, tip[1] - dy * head_len)
    draw.polygon(
        [
            tip,
            (base[0] + px * head_half_width, base[1] + py * head_half_width),
            (base[0] - px * head_half_width, base[1] - py * head_half_width),
        ],
        fill=color,
    )


def _load_font(size: int) -> ImageFont.FreeTypeFont:
    for path in FONT_CANDIDATES:
        if Path(path).exists():
            try:
                return ImageFont.truetype(path, size)
            except OSError:
                continue
    log.warning("no TrueType font found, falling back to the bitmap font")
    return ImageFont.load_default()


class WatchFaceRenderer:
    """Builds the glucose watch face image.

    Loading fonts is expensive, so it happens once here and is reused for
    every later frame.
    """

    def __init__(self, theme: Theme | None = None, unit: str = "mgdl") -> None:
        self.theme = theme or Theme()
        self.unit = unit
        self._font_value = _load_font(150)
        self._font_small = _load_font(38)
        self._font_message = _load_font(52)

    # -- public API ---------------------------------------------------------

    def render(self, reading, *, stale_after_min: float = 10.0) -> Image.Image:
        """Draw the watch face for a reading.

        Readings older than stale_after_min go grey with the age
        emphasised. The last value stays on screen when the network drops,
        so it has to be obvious when it is no longer current.
        """
        age = reading.age_minutes()
        is_stale = age >= stale_after_min

        mgdl = reading.value_mgdl
        color = self.theme.color_stale if is_stale else self.theme.status_color(mgdl)

        img, draw = self._new_canvas(color)

        value_text = reading.display_value(self.unit)
        unit_text = "mmol/L" if self.unit == "mmol" else "mg/dL"

        # Left-aligned, leaving the right side for the arrow and the age.
        draw.text((44, 118), value_text, font=self._font_value, fill=color, anchor="lm")

        value_right = 44 + draw.textlength(value_text, font=self._font_value)

        # The arrow sits right next to the number to minimise eye travel.
        angle = TREND_ANGLES.get(reading.trend)
        if angle is not None:
            _draw_arrow(draw, (value_right + 66, 116), angle, 84, color)

        draw.text(
            (46, 206), unit_text, font=self._font_small, fill=(160, 165, 178), anchor="lm"
        )

        # The age is only coloured when stale, to draw attention then.
        age_text = self._format_age(age)
        draw.text(
            (WIDTH - 44, 206),
            age_text,
            font=self._font_small,
            fill=color if is_stale else (160, 165, 178),
            anchor="rm",
        )

        return img

    def render_message(self, message: str, *, detail: str = "") -> Image.Image:
        """Draw a status or error card.

        Keeps "no reading at all" visually distinct from a real value.
        """
        img, draw = self._new_canvas(self.theme.color_stale)
        draw.text(
            (WIDTH // 2, 104 if detail else 128),
            message,
            font=self._font_message,
            fill=(226, 228, 235),
            anchor="mm",
        )
        if detail:
            draw.text(
                (WIDTH // 2, 168),
                detail,
                font=self._font_small,
                fill=(150, 155, 168),
                anchor="mm",
            )
        return img

    # -- internals ----------------------------------------------------------

    def _new_canvas(self, accent: tuple[int, int, int]):
        """Rounded background card with a status-coloured accent bar."""
        img = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        draw.rounded_rectangle(
            (0, 0, WIDTH - 1, HEIGHT - 1), radius=32, fill=self.theme.color_bg
        )
        # Accent bar down the left edge, so status reads without the digits.
        draw.rounded_rectangle((0, 0, 14, HEIGHT - 1), radius=7, fill=(*accent, 255))
        return img, draw

    @staticmethod
    def _format_age(minutes: float) -> str:
        if minutes < 1:
            return "now"
        if minutes < 60:
            return f"{int(minutes)}m"
        hours = minutes / 60
        if hours < 24:
            return f"{int(hours)}h"
        return f"{int(hours / 24)}d"
