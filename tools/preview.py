"""Render every watch face state onto one sheet, with no network and no VR.

Guards against discovering the face is unreadable only once the headset
is on.

    python tools/preview.py
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from PIL import Image

from cgm.core.librelink import Reading
from cgm.face.renderer import HEIGHT, WIDTH, WatchFaceRenderer

GAP = 24
BACKDROP = (48, 50, 58, 255)


def reading(mgdl: float, trend: int, age_min: float) -> Reading:
    return Reading(
        value_mgdl=mgdl,
        trend=trend,
        timestamp_utc=datetime.now(timezone.utc) - timedelta(minutes=age_min),
        is_high=mgdl > 180,
        is_low=mgdl < 70,
    )


def main() -> int:
    renderer = WatchFaceRenderer()

    # Every status appears once, because each one now has a marker edge of
    # its own and the point of the sheet is to see them side by side: left
    # for in range, top for high, a heavier top for very high, bottom for
    # low, and the full outline for stale.
    tiles = [
        renderer.render(reading(112, 3, 1)),      # in range, flat
        renderer.render(reading(88, 2, 2)),       # in range, falling
        renderer.render(reading(64, 1, 1)),       # low, falling fast
        renderer.render(reading(214, 5, 3)),      # high, rising fast
        renderer.render(reading(268, 4, 2)),      # very high
        renderer.render(reading(133, 4, 27)),     # stale
        # A stale low: the outline has to win over the bottom edge, or an
        # hour-old reading would still be claiming the arm is dropping now.
        renderer.render(reading(58, 2, 41)),
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
