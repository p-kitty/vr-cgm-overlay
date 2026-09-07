"""Render watch face states to a PNG, with no network and no VR.

Two sheets, because they have two audiences.

    python tools/preview.py            -> preview-states.png
    python tools/preview.py --debug    -> preview-debug.png

**`preview-states.png` is the picture at the top of `README.md`**, and
it is the first thing anyone curious about this project sees. Four
tiles: what the face looks like when everything is fine, when it is
low, when it is high, and what it shrinks to on a controller. Nothing
that needs a paragraph to explain, and nothing anyone has to scroll.

**`preview-debug.png` is the working sheet.** One tile per thing a
person has to judge and no assertion can: every marker edge side by
side, the message card, a line breaking across a scanning gap, the
labels in mmol/L. It is not committed and not linked from anywhere;
render it when changing the face and look at it.

Neither sheet is a substitute for `tests/`. Anything with an edge in it
-- which status a value falls in, where a line may break, how far the
axis grows -- is asserted there. A tile earns its place on either sheet
only by showing something that has to be looked at.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from PIL import Image

from cgm.core.librelink import GRAPH_RESOLUTION_MIN, GlucosePoint, Reading
from cgm.face.graph import GraphTuning
from cgm.face.renderer import WatchFaceRenderer

GAP = 24
BACKDROP = (48, 50, 58, 255)

# The instant the sheets are drawn at, fixed rather than taken from the
# clock. `preview-states.png` is committed and shown in README.md, and
# the times under the graph come from this: rendered at the wall clock
# it would come out different every hour, so running the tool at all
# would dirty the working tree. 13:40 in a UTC+9 timezone, which puts
# the eight hour window's labels on 06:00 to 12:00.
#
# The labels are local, so the sheet is only reproducible per timezone.
# That is enough for the job -- it stops moving under the person
# rendering it -- and a UTC axis would be worse, since these are meant
# to be read against a clock on a wall.
ANCHOR = datetime(2026, 9, 7, 4, 40, tzinfo=timezone.utc)

# Exactly the default window: 480 minutes at one point every fifteen is
# 33 of them, and the count matters. One short and the trace starts a
# few pixels in from the left of the plot while the band and the rules
# run the whole width, which reads as the graph having been clipped
# rather than as the sample data being fifteen minutes thin.
POINTS = 33

# An overnight flat stretch, breakfast, and the settle after it.
DAY = [
    103, 101, 99, 97, 96, 95, 94, 96, 98, 101, 105, 112,
    128, 154, 181, 203, 214, 211, 199, 184, 170, 158, 148, 141,
    136, 132, 128, 125, 122, 119, 117, 115, 110,
]

# The same day ending in a hyper that runs off the configured top of the
# axis, which is the one thing that moves it.
HYPER = DAY[:21] + [178, 192, 221, 258, 288, 321, 356, 372, 361, 340, 318, 297]

# And ending under the floor, which does not move.
UNDER = DAY[:23] + [131, 122, 110, 96, 84, 73, 64, 55, 48, 44]

# The bug this guards against has already happened once: at 32 the trace
# stopped short of the left of the plot and it looked like a drawing
# fault rather than fifteen minutes of missing sample.
assert len(DAY) == len(HYPER) == len(UNDER) == POINTS


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
    taken_at = ANCHOR - timedelta(minutes=age_min)
    return Reading(
        value_mgdl=mgdl,
        trend=trend,
        timestamp_utc=taken_at,
        is_high=mgdl > 180,
        is_low=mgdl < 70,
        history=history(taken_at, values) if values else (),
    )


def gapped(mgdl: float, trend: int) -> Reading:
    """A reading whose history stops for an hour and a half in the middle.

    The sensor not being scanned is ordinary, and the line has to break
    across it rather than draw a straight run through a stretch nothing
    was measured in. Both halves together still fill the window, so the
    only thing missing from the picture is the thing being shown.
    """
    taken_at = ANCHOR
    before = history(taken_at - timedelta(minutes=195), DAY[:20])
    after = history(taken_at, [126, 118, 110, 104, 99, 103, 108, mgdl])
    return Reading(
        value_mgdl=mgdl,
        trend=trend,
        timestamp_utc=taken_at,
        is_high=False,
        is_low=False,
        history=before + after,
    )


def face(renderer: WatchFaceRenderer, of: Reading) -> Image.Image:
    """Draw one tile at the anchor rather than at the wall clock."""
    return renderer.render(of, now=ANCHOR)


def showcase(plain: WatchFaceRenderer, graphed: WatchFaceRenderer) -> list:
    """What README.md shows. Four states, no explanation needed."""
    return [
        # Everything is fine, and the graph says how it got there.
        face(graphed, reading(110, 3, 1, DAY)),
        # Low: red, and the trace on the floor.
        face(graphed, reading(44, 1, 1, UNDER)),
        # High: orange, and the axis grown to keep the peak on the chart.
        face(graphed, reading(297, 2, 1, HYPER)),
        # And the same face without the graph, which is what rides the
        # controller in VR.
        face(plain, reading(112, 3, 1)),
    ]


def debug(
    plain: WatchFaceRenderer, graphed: WatchFaceRenderer, mmol: WatchFaceRenderer
) -> list:
    """The working sheet: what the showcase leaves out."""
    return [
        # Every marker edge, to be told apart at a glance: top for high,
        # a heavier top for very high, bottom for low.
        face(plain, reading(214, 5, 3)),
        face(plain, reading(268, 4, 2)),
        face(plain, reading(64, 1, 1)),
        # Stale, on a low reading, because that is where two rules meet:
        # an hour-old 58 must go grey and outlined rather than red and
        # bottom-lit, or it would still be claiming the arm is dropping.
        face(plain, reading(58, 2, 41)),
        plain.render_message("NO CONNECTION", detail="no reading yet"),
        # A gap in scanning. The line breaks rather than spanning it.
        face(graphed, gapped(107, 2)),
        # And in mmol/L, where the level labels convert and nothing
        # behind them does.
        face(mmol, reading(110, 3, 1, DAY)),
    ]


def sheet(tiles: list, cols: int = 2) -> Image.Image:
    """Lay the tiles out in a grid, measured off the tiles themselves.

    They are not all one size -- a card with a graph is taller -- so a
    row is as tall as its tallest tile and the shorter ones are centred
    in it rather than left hanging from the top.
    """
    rows = [tiles[i : i + cols] for i in range(0, len(tiles), cols)]
    col_width = max(tile.width for tile in tiles)
    row_heights = [max(t.height for t in row) for row in rows]
    image = Image.new(
        "RGBA",
        (
            cols * col_width + (cols + 1) * GAP,
            sum(row_heights) + (len(rows) + 1) * GAP,
        ),
        BACKDROP,
    )
    y = GAP
    for row, height in zip(rows, row_heights):
        for i, tile in enumerate(row):
            image.alpha_composite(
                tile,
                (GAP + i * (col_width + GAP), y + (height - tile.height) // 2),
            )
        y += height + GAP
    return image


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--debug",
        action="store_true",
        help="render the working sheet instead of the one README shows",
    )
    args = parser.parse_args(argv)

    plain = WatchFaceRenderer()
    graphed = WatchFaceRenderer(graph=GraphTuning())
    mmol = WatchFaceRenderer(unit="mmol", graph=GraphTuning())

    if args.debug:
        tiles, name = debug(plain, graphed, mmol), "preview-debug.png"
    else:
        tiles, name = showcase(plain, graphed), "preview-states.png"

    out = Path(__file__).parent.parent / name
    sheet(tiles).save(out)
    print(f"wrote: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
