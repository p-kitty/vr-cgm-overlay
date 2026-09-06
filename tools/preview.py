"""Render every watch face state onto one sheet, with no network and no VR.

Guards against discovering the face is unreadable only once the headset
is on.

    python tools/preview.py

The sheet has two kinds of tile on it. The first nine are the face as
the overlay draws it by default -- 512x256, no history. The rest are the
same face with `[graph]` on, which is what `--window` shows: the same
card grown a sparkline, in the states that decide whether the sparkline
is worth its space. Both sizes appear because both ship.
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


def gapped(mgdl: float, trend: int, age_min: float) -> Reading:
    """A reading whose history stops for an hour in the middle.

    The sensor not being scanned is ordinary, and the line has to break
    across it rather than draw a straight run through a stretch nothing
    was measured in.
    """
    taken_at = datetime.now(timezone.utc) - timedelta(minutes=age_min)
    before = history(taken_at - timedelta(minutes=105), [96, 104, 118, 131, 142])
    after = history(taken_at, [126, 118, 110, mgdl])
    return Reading(
        value_mgdl=mgdl,
        trend=trend,
        timestamp_utc=taken_at,
        is_high=False,
        is_low=mgdl < 70,
        history=before + after,
    )


# A hyper that runs off the configured top of the axis, which is the one
# thing that moves it: it grows to the next round step above the peak
# rather than clipping the trace flat.
HYPER = [132, 148, 191, 243, 288, 321, 356, 372, 361, 340, 318, 297]

# And a low that goes under the floor. The floor does not move, so this
# is drawn on it -- the digits above are what say how far under.
UNDER = [104, 96, 88, 79, 71, 63, 55, 47, 44]

# Twelve hours, at the fifteen minutes apart the API sends: an
# overnight flat stretch, breakfast, and the settle after it. This is
# what `window_min = 0` actually has to draw.
DAY = [
    118, 112, 106, 101, 98, 96, 95, 97, 99, 102, 104, 103,
    101, 99, 97, 96, 95, 94, 96, 98, 101, 105, 112, 128,
    154, 181, 203, 214, 211, 199, 184, 170, 158, 148, 141, 136,
    132, 128, 125, 122, 119, 117, 115, 114, 113, 112, 111, 110,
]


def main() -> int:
    plain = WatchFaceRenderer()
    # The default: eight hours. The tiles that show one drawing rule at
    # a time use three, because their series are that long and a window
    # wider than its data is a different thing to look at.
    graphed = WatchFaceRenderer(graph=GraphTuning(window_min=180))
    eight = WatchFaceRenderer(graph=GraphTuning())
    # window_min = 0: everything the response carried, with the X axis
    # as long as the history rather than a fixed length.
    allday = WatchFaceRenderer(graph=GraphTuning(window_min=0))
    # The labels are the one part of the face that changes with the
    # display unit, so one tile has to be drawn in the other one.
    mmol = WatchFaceRenderer(unit="mmol", graph=GraphTuning())

    # Every status appears once, because each one now has a marker edge of
    # its own and the point of the sheet is to see them side by side: left
    # for in range, top for high, a heavier top for very high, bottom for
    # low, and the full outline for stale.
    tiles = [
        plain.render(reading(112, 3, 1)),      # in range, flat
        plain.render(reading(88, 2, 2)),       # in range, falling
        plain.render(reading(64, 1, 1)),       # low, falling fast
        plain.render(reading(214, 5, 3)),      # high, rising fast
        plain.render(reading(268, 4, 2)),      # very high
        plain.render(reading(133, 4, 27)),     # stale
        # A stale low: the outline has to win over the bottom edge, or an
        # hour-old reading would still be claiming the arm is dropping now.
        plain.render(reading(58, 2, 41)),
        plain.render_message("NO CONNECTION", detail="no reading yet"),
        plain.render_message("CONNECTING"),
        # With the sparkline. A meal rise and its settle, which is the
        # shape the graph exists to make obvious a whole hour before the
        # arrow can say it is over.
        graphed.render(
            reading(147, 4, 1, [104, 99, 102, 118, 163, 194, 188, 171, 158, 147])
        ),
        # A quiet run. This is the one to check the fixed axis on: the
        # trace has to stay a flat line rather than being stretched into
        # a mountain range by scaling to its own spread.
        graphed.render(
            reading(103, 3, 1, [108, 105, 109, 102, 99, 104, 107, 101, 105, 103])
        ),
        # Falling into a low: the trace crosses out of the band at the
        # same moment the card goes red.
        graphed.render(
            reading(62, 1, 1, [122, 118, 109, 98, 91, 83, 74, 68, 62])
        ),
        # Off the top of the axis. Clamped rather than clipped, so it
        # rides the edge instead of vanishing.
        graphed.render(
            reading(324, 4, 2, [188, 210, 244, 271, 296, 318, 324])
        ),
        # A gap in scanning. The line must break, not span it.
        graphed.render(gapped(107, 2, 1)),
        # Two points, the most a freshly started sensor has. Enough for a
        # line and no more, which is what graph.window_min's floor allows.
        graphed.render(reading(96, 3, 1, [92, 96])),
        # No history at all. The band is still drawn, so the strip reads
        # as an empty graph rather than as something having failed.
        graphed.render(reading(112, 3, 1)),
        # Stale, with a graph: the frame goes round the sparkline too.
        graphed.render(
            reading(134, 3, 31, [151, 148, 141, 137, 133, 130, 134])
        ),
        graphed.render_message("NO CONNECTION", detail="no reading yet"),
        # The default window, eight hours of the same day. The times
        # underneath step to whatever keeps them to four labels, so this
        # and the three-hour tiles above should not be labelled at the
        # same interval.
        eight.render(reading(110, 3, 1, DAY)),
        # All twelve. The axis is as long as the history, so this one
        # reaches further left without leaving any of it empty.
        allday.render(reading(110, 3, 1, DAY)),
        # A hyper past the top of the axis: it grows to the next round
        # step, and the number that appears at the top is how the reader
        # is told the scale is no longer the one in the config.
        eight.render(reading(297, 2, 1, HYPER)),
        # And under the floor, which does not move. The trace runs along
        # the bottom; the digits say 44.
        eight.render(reading(44, 1, 1, UNDER)),
        # The same graph in mmol/L: the level labels convert, and
        # nothing behind them does.
        mmol.render(reading(110, 3, 1, DAY)),
    ]

    # The tiles are no longer all one size, so the grid is measured off
    # them rather than off a pair of constants.
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
