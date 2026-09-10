"""Render watch face states to a PNG, with no network and no VR.

Two sheets, because they have two audiences.

    python tools/preview.py            -> preview-states.png
    python tools/preview.py --debug    -> preview-debug.png

**`preview-states.png` is the picture at the top of `README.md`**, and
it is the first thing anyone curious about this project sees. The four
colours the face can be -- in range, high, low, very high -- each with
the graph that says how it got there, and then the same face without
one, which is what it shrinks to on a controller. Nothing that needs a
paragraph to explain, and nothing anyone has to scroll.

**`preview-debug.png` is the working sheet.** One tile per thing a
person has to judge and no assertion can: every marker edge side by
side, the message card, a line breaking across a scanning gap, both
ends of the axis under a graph, the labels in mmol/L, and the trend
arrow bent through each shape the last half hour can take. It is not
committed and not linked from anywhere; render it when changing the
face and look at it.

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

# The same day with a second meal on top of it, ending inside the high
# band rather than past it. Yellow is the colour with the least to look
# at and the one easiest to leave untested: it is not an emergency, so
# nothing else on the sheet reaches it.
HIGH = DAY[:23] + [152, 161, 172, 184, 195, 204, 210, 214, 216, 214]

# The same day ending in a hyper that runs off the configured top of the
# axis, which is the one thing that moves it.
HYPER = DAY[:21] + [178, 192, 221, 258, 288, 321, 356, 372, 361, 340, 318, 297]

# And ending under the floor, which does not move.
UNDER = DAY[:23] + [131, 122, 110, 96, 84, 73, 64, 55, 48, 44]

# The same day with a low in the night it has since come out of. One
# sample sits exactly on low_mgdl on the way down, which is not low.
NIGHT_LOW = [103, 94, 85, 77, 70, 64, 59, 57, 61, 68, 82, 105] + DAY[12:]

# The bug this guards against has already happened once: at 32 the trace
# stopped short of the left of the plot and it looked like a drawing
# fault rather than fifteen minutes of missing sample.
assert len(DAY) == len(HIGH) == len(HYPER) == len(UNDER) == len(NIGHT_LOW) == POINTS


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
        history=history(taken_at, values) if values else (),
    )


# The last half hour, as three points fifteen minutes apart -- which is
# the whole of what the arrow is bent through. Written as mg/dL at 30
# minutes ago, 15, and now.
#
# These are the shapes no single angle can tell apart. Averaged, the
# first two are the same gentle rise and the same gentle fall; read as
# they happened, one is still going and the other has turned over.
BENDS = {
    "still climbing": (100, 112, 130),
    "levelling off": (100, 122, 128),
    # Ordinary enough after a meal, and already past the cap: the fold
    # is drawn shallower than it was, so what is lost is how sharp the
    # turn was rather than that it turned.
    "rolled over": (100, 124, 116),
    # A hard climb answered by an equally hard fall. Uncapped the two
    # segments lie on top of each other and the arrow is a bar with a
    # head somewhere in it; capped, the tail comes back past level, so
    # the stretch before now is drawn wrong to keep now readable. Judge
    # whether it still reads as an arrow going somewhere.
    "hairpin": (100, 190, 100),
}


def bent(values: tuple[float, float, float]) -> Reading:
    """A reading carrying only the three points the arrow bends through.

    Nothing else: this is the face the overlay draws, where there is no
    sparkline and the arrow is the only history shown.
    """
    return Reading(
        value_mgdl=values[-1],
        trend=3,
        timestamp_utc=ANCHOR,
        history=history(ANCHOR, list(values)),
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
        history=before + after,
    )


def face(renderer: WatchFaceRenderer, of: Reading) -> Image.Image:
    """Draw one tile at the anchor rather than at the wall clock."""
    return renderer.render(of, now=ANCHOR)


def showcase(plain: WatchFaceRenderer, graphed: WatchFaceRenderer) -> list:
    """What README.md shows. Every colour once, no explanation needed.

    Two columns, so the four coloured states fall into a square and the
    plain face sits under it on its own -- which is the arrangement, not
    a coincidence of the count: the square is what the face does, and
    the one below it is what it looks like on a wrist.
    """
    return [
        # Everything is fine, and the graph says how it got there.
        face(graphed, reading(110, 3, 1, DAY)),
        # High: yellow, and a second meal to be high at the end of.
        face(graphed, reading(214, 5, 1, HIGH)),
        # Low: red, and the trace on the floor.
        face(graphed, reading(44, 1, 1, UNDER)),
        # Very high: orange, and the axis grown to keep the peak on the
        # chart -- the one state that moves the scale.
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
        # The two ends of the axis, which behave differently on purpose
        # and are the two states the ruling is hardest on.
        #
        # A hyper takes the top up to 400, which is eight gridlines in a
        # strip laid out for six. Naming them all is a column of digits
        # against the trace, so every second one goes unnamed from the
        # top down -- whether that still reads as a scale, and whether
        # the new line at 400 still says the axis has moved, is the
        # question this tile is here to answer.
        face(graphed, reading(297, 2, 1, HYPER)),
        # The bottom does not move at all. A 44 is drawn on the 50 line
        # rather than off the card, so the trace runs into the floor and
        # sits on it: check that it still reads as a reading pinned to
        # the bottom of the scale and not as the line being clipped, now
        # that the floor is one rule among six rather than the only one.
        face(graphed, reading(44, 1, 1, UNDER)),
        # A low that is over. The face is green because now is fine, and
        # the stretch under the dashed line is red because then was not:
        # check the red starts and stops on that line rather than at a
        # sample either side of it, and that it reads as history rather
        # than as the face disagreeing with itself.
        face(graphed, reading(110, 3, 1, NIGHT_LOW)),
        # And in mmol/L, where the level labels convert and nothing
        # behind them does.
        face(mmol, reading(110, 3, 1, DAY)),
        # The bend, which is the tile with the most to look at and no
        # assertion behind it. Whether two segments at 84 pixels read as
        # a shape -- and which way that shape is going -- is a question
        # for eyes, and in VR for a headset, where this face is the only
        # history there is.
        *(face(plain, bent(values)) for values in BENDS.values()),
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
