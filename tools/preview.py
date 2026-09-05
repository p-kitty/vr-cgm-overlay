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

from librelink import GlucosePoint, Reading
from renderer import HEIGHT, WIDTH, WatchFaceRenderer

GAP = 24
BACKDROP = (48, 50, 58, 255)


def history(
    mgdl: float, taken_at: datetime, slope: float, minutes: int = 20
) -> tuple[GlucosePoint, ...]:
    """A straight run of samples a minute apart, ending on the reading.

    Enough to fit: the arrow angle comes from the slope through these,
    so a tile with no history is a tile showing the API fallback.
    """
    return tuple(
        GlucosePoint(
            taken_at - timedelta(minutes=ago),
            mgdl - slope * ago,
        )
        for ago in range(minutes, -1, -1)
    )


def reading(
    mgdl: float, trend: int, age_min: float, slope: float | None = None
) -> Reading:
    """A reading; with `slope` set it carries history to fit that slope.

    Without one the history is a single point, which is too little to
    fit -- so `trend` is what the arrow falls back to, exactly as it
    does on a fresh sensor.
    """
    taken_at = datetime.now(timezone.utc) - timedelta(minutes=age_min)
    return Reading(
        value_mgdl=mgdl,
        value_mmol=mgdl / 18.0,
        trend=trend,
        timestamp_utc=taken_at,
        is_high=mgdl > 180,
        is_low=mgdl < 70,
        history=() if slope is None else history(mgdl, taken_at, slope),
    )


def main() -> int:
    renderer = WatchFaceRenderer()

    # Every status appears once, because each one now has a marker edge of
    # its own and the point of the sheet is to see them side by side: left
    # for in range, top for high, a heavier top for very high, bottom for
    # low, and the full outline for stale.
    #
    # The last row is about the arrow rather than the status. A fitted
    # slope points anywhere, so the angles between the official five are
    # what has to be looked at: a gentle drift and a hard climb are the
    # same arrow on the phone and have to stop being the same here.
    tiles = [
        renderer.render(reading(112, 3, 1, slope=0.0)),     # in range, flat
        renderer.render(reading(88, 2, 2, slope=-1.0)),     # in range, falling
        renderer.render(reading(64, 1, 1, slope=-2.4)),     # low, falling fast
        renderer.render(reading(214, 5, 3, slope=2.6)),     # high, rising fast
        renderer.render(reading(268, 4, 2, slope=1.2)),     # very high
        renderer.render(reading(133, 4, 27, slope=0.8)),    # stale
        # A stale low: the outline has to win over the bottom edge, or an
        # hour-old reading would still be claiming the arm is dropping now.
        renderer.render(reading(58, 2, 41, slope=-1.5)),
        renderer.render_message("NO CONNECTION", detail="no reading yet"),
        renderer.render_message("CONNECTING"),
        # A drift of a third of the flat threshold: barely off level, and
        # the API would round it to a flat arrow.
        renderer.render(reading(120, 3, 1, slope=0.33)),
        # Between flat and fast, so between 45 and 90 degrees.
        renderer.render(reading(120, 4, 1, slope=1.5)),
        # No history to fit: the arrow falls back to TrendArrow=4 and
        # snaps to the official 45 degrees.
        renderer.render(reading(120, 4, 1)),
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
