"""Render the watch face states onto one sheet, with no network and no VR.

Guards against discovering the face is unreadable only once the headset
is on.

    python tools/preview.py

**One tile per thing worth looking at, and no more.** The sheet is for
eyes, and a sheet nobody scrolls to the bottom of has stopped being
that. Anything provable by assertion belongs in `tests/`, which already
covers the status bands, the marker edges, the trend angle, the gap
rule and the axis; a tile earns its place here only by showing
something a person has to judge.

So there are two groups and no overlap between them:

  - **The face on its own**, 512x256, which is what the overlay draws.
    One tile per marker edge, because the whole point of the edges is
    being told apart at a glance, and that is a judgement.
  - **The face with a graph**, which is what `--window` draws. One tile
    per drawing rule that changes the picture -- the axis growing, the
    floor holding, a line breaking, the labels converting. The statuses
    are not repeated here: they are the same colours on a taller card.
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from PIL import Image

from cgm.core.librelink import GRAPH_RESOLUTION_MIN, GlucosePoint, Reading
from cgm.face.graph import GraphTuning
from cgm.face.renderer import WatchFaceRenderer

GAP = 24
BACKDROP = (48, 50, 58, 255)

# Eight hours at the fifteen minutes apart the API sends, which is the
# default window. An overnight flat stretch, breakfast, and the settle
# after it.
DAY = [
    101, 99, 97, 96, 95, 94, 96, 98, 101, 105, 112, 128,
    154, 181, 203, 214, 211, 199, 184, 170, 158, 148, 141, 136,
    132, 128, 125, 122, 119, 117, 115, 110,
]

# The same day ending in a hyper that runs off the configured top of the
# axis, which is the one thing that moves it.
HYPER = DAY[:20] + [178, 192, 221, 258, 288, 321, 356, 372, 361, 340, 318, 297]

# And ending under the floor, which does not move.
UNDER = DAY[:22] + [131, 122, 110, 96, 84, 73, 64, 55, 48, 44]


def history(taken_at: datetime, values, step_min: float = GRAPH_RESOLUTION_MIN):
    """A series ending on `taken_at`, one point every `step_min`.

    `values` is oldest first and its last entry is the measurement
    itself, the way the API's own history is folded together in
    `cgm.core.librelink._parse_graph_data`.
    """
    last = len(values) - 1
    return tuple(
        GlucosePoint(taken_at - timedelta(minutes=(last - i) * step_min), value)
        for i, value in enumerate(values)
    )


def reading(mgdl: float, trend: int, age_min: float, values=None) -> Reading:
    taken_at = datetime.now(timezone.utc) - timedelta(minutes=age_min)
    return Reading(
        value_mgdl=mgdl,
        trend=trend,
        timestamp_utc=taken_at,
        is_high=mgdl > 180,
        is_low=mgdl < 70,
        history=history(taken_at, values) if values else (),
    )


def gapped(mgdl: float, trend: int) -> Reading:
    """A reading whose history stops for two hours in the middle.

    The sensor not being scanned is ordinary, and the line has to break
    across it rather than draw a straight run through a stretch nothing
    was measured in.
    """
    taken_at = datetime.now(timezone.utc)
    before = history(taken_at - timedelta(minutes=195), DAY[:14])
    after = history(taken_at, [126, 118, 110, 104, 99, 103, 108, mgdl])
    return Reading(
        value_mgdl=mgdl,
        trend=trend,
        timestamp_utc=taken_at,
        is_high=False,
        is_low=False,
        history=before + after,
    )


def main() -> int:
    plain = WatchFaceRenderer()
    graphed = WatchFaceRenderer(graph=GraphTuning())
    mmol = WatchFaceRenderer(unit="mmol", graph=GraphTuning())

    tiles = [
        # The face the overlay draws. One per marker edge: left for in
        # range, top for high, a heavier top for very high, bottom for
        # low, the full outline for stale.
        plain.render(reading(112, 3, 1)),
        plain.render(reading(214, 5, 3)),
        plain.render(reading(268, 4, 2)),
        plain.render(reading(64, 1, 1)),
        # Stale, on a low reading, because that is where the two rules
        # meet: an hour-old 58 must go grey and outlined rather than red
        # and bottom-lit, or it would still be claiming the arm is
        # dropping now.
        plain.render(reading(58, 2, 41)),
        plain.render_message("NO CONNECTION", detail="no reading yet"),
        # The face --window draws, and the everyday one: a meal rise
        # inside the band, the floor ruled, both thresholds dashed, and
        # eight hours of times underneath.
        graphed.render(reading(110, 3, 1, DAY)),
        # The axis moving. It grows to the next round 50 above the peak
        # rather than flattening it against the top, and the number that
        # appears up there is how that is announced.
        graphed.render(reading(297, 2, 1, HYPER)),
        # The axis not moving. The floor stays where it is and the trace
        # runs along it; the digits are what say 44.
        graphed.render(reading(44, 1, 1, UNDER)),
        # A gap in scanning. The line breaks rather than spanning it.
        graphed.render(gapped(107, 2)),
        # And in mmol/L, where the level labels convert and nothing
        # behind them does.
        mmol.render(reading(110, 3, 1, DAY)),
    ]

    # The tiles are not all one size, so the grid is measured off them
    # rather than off a pair of constants.
    cols = 2
    rows = [tiles[i : i + cols] for i in range(0, len(tiles), cols)]
    col_width = max(tile.width for tile in tiles)
    sheet = Image.new(
        "RGBA",
        (
            cols * col_width + (cols + 1) * GAP,
            sum(max(t.height for t in row) for row in rows) + (len(rows) + 1) * GAP,
        ),
        BACKDROP,
    )
    y = GAP
    for row in rows:
        for i, tile in enumerate(row):
            sheet.alpha_composite(tile, (GAP + i * (col_width + GAP), y))
        y += max(t.height for t in row) + GAP

    out = Path(__file__).parent.parent / "preview-states.png"
    sheet.save(out)
    print(f"wrote: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
