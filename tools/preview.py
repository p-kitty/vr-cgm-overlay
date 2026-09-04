"""Render every watch face state onto one sheet, with no network and no VR.

Guards against discovering the face is unreadable only once the headset
is on.

    python tools/preview.py
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from PIL import Image

from librelink import Reading
from renderer import HEIGHT, WIDTH, WatchFaceRenderer

GAP = 24
BACKDROP = (48, 50, 58, 255)


def reading(mgdl: float, trend: int, age_min: float) -> Reading:
    return Reading(
        value_mgdl=mgdl,
        value_mmol=mgdl / 18.0,
        trend=trend,
        timestamp_utc=datetime.now(timezone.utc) - timedelta(minutes=age_min),
        is_high=mgdl > 180,
        is_low=mgdl < 70,
    )


def main() -> int:
    renderer = WatchFaceRenderer()

    tiles = [
        renderer.render(reading(112, 3, 1)),      # in range, flat
        renderer.render(reading(88, 2, 2)),       # in range, falling
        renderer.render(reading(64, 1, 1)),       # low, falling fast
        renderer.render(reading(214, 5, 3)),      # high, rising fast
        renderer.render(reading(268, 4, 2)),      # very high
        renderer.render(reading(133, 4, 27)),     # stale
        renderer.render_message("NO CONNECTION", detail="no reading yet"),
        renderer.render_message("CONNECTING"),
    ]

    cols = 2
    rows = (len(tiles) + cols - 1) // cols
    sheet = Image.new(
        "RGBA",
        (cols * WIDTH + (cols + 1) * GAP, rows * HEIGHT + (rows + 1) * GAP),
        BACKDROP,
    )
    for i, tile in enumerate(tiles):
        x = GAP + (i % cols) * (WIDTH + GAP)
        y = GAP + (i // cols) * (HEIGHT + GAP)
        sheet.alpha_composite(tile, (x, y))

    out = Path(__file__).parent.parent / "preview-states.png"
    sheet.save(out)
    print(f"wrote: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
