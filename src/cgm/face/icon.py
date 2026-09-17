"""The app's icon: the face's sparkline, cut down to one gesture.

A dark card, the target range as a green band across it, and a trace
that climbs out of the band, comes back, and ends on a green dot inside
it -- the graph under the digits, with everything that needs reading
taken away. No digits, since no number is true for an icon, and no
arrow, since an arrow alone reads as "next" or "play".

Drawn here rather than kept as a picture in the repository, so the
colours are the face's own: the card, the band tint and the trace all
come from `Theme` and `cgm.face.graph`, and a retune of either shows up
in the taskbar without anybody redrawing anything.

The shapes are sized for 16 pixels, not for 256. The trace is several
times thicker than the graph's and the dot is wider than the trace by
far, because in a taskbar or an Explorer list the icon is a smudge, and
what has to survive is a dark tile with a line and a green dot on it.
"""

from __future__ import annotations

import math

from PIL import Image, ImageDraw

from cgm.face.graph import BAND_TINT, GRID_COLOR, TRACE_COLOR, _mix
from cgm.face.renderer import Theme

# The sizes Windows asks an .ico for, from a list view's 16 up to the
# large-icon view's 256. Each is drawn on its own rather than one
# shrunk into the rest, so no size is a resample of a resample.
ICO_SIZES = (16, 20, 24, 32, 40, 48, 64, 128, 256)

# Everything below is on a 256 grid and scaled to the size asked for.
GRID = 256
# Drawn this many times larger and then reduced, since Pillow draws
# without anti-aliasing and a 16px circle is otherwise a plus sign.
SUPERSAMPLE = 8

CARD_INSET = 6
CARD_RADIUS = 56
CARD_EDGE = 6
# The band's top and bottom. Not the graph's thresholds: the icon has no
# axis, so the band only has to sit where the trace can leave it.
BAND = (108, 172)
TRACE_WIDTH = 22
# Where the trace starts and ends across the card, where it rests, how
# high its peak climbs above that, where the peak is, and how wide.
TRACE_X = (44, 190)
TRACE_REST = 150
TRACE_PEAK = 88
PEAK_AT = 0.38
PEAK_WIDTH = 0.19
DOT_RADIUS = 30


def draw_icon(size: int, theme: Theme | None = None) -> Image.Image:
    """The icon as a square RGBA image, `size` pixels a side."""
    if size < 1:
        raise ValueError(f"an icon needs at least one pixel, not {size}")
    theme = theme or Theme()
    big = size * SUPERSAMPLE
    image = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    unit = big / GRID

    def at(value: float) -> float:
        return value * unit

    card = theme.color_bg[:3]
    far = GRID - CARD_INSET
    draw.rounded_rectangle(
        (at(CARD_INSET), at(CARD_INSET), at(far), at(far)),
        radius=at(CARD_RADIUS),
        fill=card,
        # The card is nearly black, and so are most taskbars. The edge
        # is the graph's gridline grey, so the tile has an outline on
        # one without shouting on a light one.
        outline=GRID_COLOR,
        width=max(1, round(at(CARD_EDGE))),
    )
    # Inside the edge, so the band does not cut through the outline.
    # The band sits between the card's rounded corners, so a rectangle
    # is enough.
    draw.rectangle(
        (at(CARD_INSET + CARD_EDGE), at(BAND[0]), at(far - CARD_EDGE), at(BAND[1])),
        fill=_mix(card, theme.color_in_range, BAND_TINT),
    )

    steps = 64
    points = []
    for i in range(steps + 1):
        t = i / steps
        x = TRACE_X[0] + t * (TRACE_X[1] - TRACE_X[0])
        y = TRACE_REST - TRACE_PEAK * math.exp(-(((t - PEAK_AT) / PEAK_WIDTH) ** 2))
        points.append((at(x), at(y)))
    draw.line(points, fill=TRACE_COLOR, width=round(at(TRACE_WIDTH)), joint="curve")
    # A line's ends are square; round the one the dot does not cover.
    _dot(draw, points[0], at(TRACE_WIDTH) / 2, TRACE_COLOR)
    _dot(draw, points[-1], at(DOT_RADIUS), theme.color_in_range)

    return image.resize((size, size), Image.Resampling.LANCZOS)


def save_ico(path, theme: Theme | None = None) -> None:
    """Write a Windows .ico holding every size in ICO_SIZES."""
    images = [draw_icon(size, theme) for size in ICO_SIZES]
    # Pillow takes the largest image and `append_images` as the frames
    # to use for each size, rather than shrinking the largest.
    images[-1].save(
        path,
        format="ICO",
        sizes=[(size, size) for size in ICO_SIZES],
        append_images=images[:-1],
    )


def _dot(draw: ImageDraw.ImageDraw, center, radius: float, color) -> None:
    x, y = center
    draw.ellipse((x - radius, y - radius, x + radius, y + radius), fill=color)

