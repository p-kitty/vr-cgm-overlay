"""The history sparkline drawn under the number.

The series comes free. `cgm.core.librelink` already parses `graphData`
off the same response the current value arrives in -- roughly twelve
hours of measurements -- and hands it over on `Reading.history`, where
until now only the trend fit read it. So this costs no request, no
cache and no storage; it is a second reading of data already in hand.

Four rules decide what gets drawn, and each one is there because the
obvious alternative lies:

  - **The Y axis is fixed**, not fitted to the data. Auto-scaling turns
    a quiet flat stretch into a mountain range, which makes a calm
    reading look alarming at exactly the glance this face exists for.
  - **The target range is a band behind the line**, so where the trace
    sits reads without anyone measuring it against an axis that is not
    drawn.
  - **The line breaks across gaps** rather than spanning them. A joined
    line over a stretch the sensor was not scanning draws data that was
    never measured.
  - **Only the recent past is shown.** All twelve hours at 512px wide
    puts the points a few pixels apart, which is a texture rather than
    a shape.

Values are clamped into the axis rather than pushed off it, so a reading
past the top rides the edge. It is still visibly past the band, and the
number above says how far.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

# How far apart two samples have to be before the line breaks between
# them. The API sends history at one point every fifteen minutes -- see
# GRAPH_RESOLUTION_MIN in cgm.core.librelink -- so twice that is a
# sample that did not arrive rather than one that arrived late. The
# number is repeated here rather than imported because cgm.face imports
# nothing from cgm.core; tests/test_graph.py holds the two in step.
MAX_GAP_MIN = 30.0

# The trace is deliberately not the status colour. The digits, the arrow
# and the marker all say what the reading is *now*; colouring an hour of
# history by the present moment would claim the whole line was low the
# instant the last point dipped. It is drawn as data instead, and only
# the newest point takes the status colour, which is what ties it to the
# number above it.
TRACE_COLOR = (226, 228, 235)
TRACE_WIDTH = 3
HEAD_RADIUS = 5

# How much of the in-range colour is mixed into the card to make the
# band. Enough to find without looking for it, not enough to compete
# with the trace crossing it.
BAND_TINT = 0.18


@dataclass
class GraphTuning:
    """How much history the sparkline shows, and how it is scaled.

    The axis bounds are mg/dL like every other threshold in this
    project, including under `display.unit = "mmol"`. Nothing on the
    graph is labelled with a number, so there is nothing here for a
    display unit to change.
    """

    window_min: float = 180.0
    axis_low_mgdl: float = 40.0
    axis_high_mgdl: float = 300.0


def recent(points, window_min: float, now: datetime) -> list:
    """The points inside the window ending at `now`, oldest first.

    `now` is the reading's own timestamp rather than the wall clock, the
    same anchor `Reading.slope_mgdl_per_min` fits over. A stale reading
    then keeps describing the stretch it was taken in, instead of
    sliding out of its own graph while the age readout beside it says
    the same thing more plainly.
    """
    cutoff = now - timedelta(minutes=window_min)
    return [p for p in points if cutoff <= p.at <= now]


def segments(points, max_gap_min: float = MAX_GAP_MIN) -> list[list]:
    """Split a series wherever the sensor stopped reporting.

    Each returned run is a stretch that may be joined up. A run of one
    is legal and means a single measurement with nothing either side of
    it close enough to connect to.
    """
    runs: list[list] = []
    for point in points:
        if runs and (point.at - runs[-1][-1].at).total_seconds() / 60 <= max_gap_min:
            runs[-1].append(point)
        else:
            runs.append([point])
    return runs


def _mix(base: tuple[int, int, int], tint: tuple[int, int, int], amount: float):
    """Blend `tint` into `base`, opaquely.

    Pillow's drawing does not composite: an ink with alpha in it
    replaces the pixel rather than blending into it, so a translucent
    band would punch a hole through the card instead of tinting it. The
    blend is done here and drawn at the card's own alpha, which also
    keeps the card one uniform transparency in VR.
    """
    return tuple(round(b + (t - b) * amount) for b, t in zip(base, tint))


def draw_sparkline(
    draw,
    box: tuple[float, float, float, float],
    points,
    *,
    tuning: GraphTuning,
    theme,
    now: datetime,
    accent: tuple[int, int, int],
) -> None:
    """Draw the band and the trace inside `box` on an existing canvas.

    `accent` is the status colour the rest of the face is drawn in, used
    for the newest point alone. `theme` supplies the target range and
    the card colour the band is mixed into.
    """
    left, top, right, bottom = box
    # The axis is inset by the head's radius at both ends, so a reading
    # clamped onto the top or the bottom of it has its dot land on the
    # edge of the box rather than half outside -- which at the bottom
    # would put it into the low marker's room.
    top += HEAD_RADIUS
    bottom -= HEAD_RADIUS
    span_mgdl = tuning.axis_high_mgdl - tuning.axis_low_mgdl
    span_px = bottom - top

    def y_for(mgdl: float) -> float:
        clamped = max(tuning.axis_low_mgdl, min(tuning.axis_high_mgdl, mgdl))
        return bottom - (clamped - tuning.axis_low_mgdl) / span_mgdl * span_px

    # The band goes down first, so the trace crosses it rather than
    # disappearing behind it. It is drawn whether or not there is any
    # history: an empty strip with the range still marked says "no data
    # yet" where an empty strip with nothing in it says "broken".
    card = theme.color_bg
    draw.rectangle(
        (left, y_for(theme.high_mgdl), right, y_for(theme.low_mgdl)),
        fill=(*_mix(card[:3], theme.color_in_range, BAND_TINT), card[3]),
    )

    shown = recent(points, tuning.window_min, now)
    if not shown:
        return

    start = now - timedelta(minutes=tuning.window_min)
    width_px = right - left

    def x_for(at: datetime) -> float:
        fraction = (at - start).total_seconds() / (tuning.window_min * 60.0)
        return left + max(0.0, min(1.0, fraction)) * width_px

    for run in segments(shown):
        plotted = [(x_for(p.at), y_for(p.mgdl)) for p in run]
        if len(plotted) == 1:
            _dot(draw, plotted[0], TRACE_WIDTH / 2, TRACE_COLOR)
        else:
            draw.line(plotted, fill=TRACE_COLOR, width=TRACE_WIDTH, joint="curve")

    # The right-hand end is the measurement the digits above are showing,
    # so it is marked in their colour. It is the one place the graph and
    # the number are the same fact, and it says which end is now.
    newest = shown[-1]
    _dot(draw, (x_for(newest.at), y_for(newest.mgdl)), HEAD_RADIUS, accent)


def _dot(draw, center: tuple[float, float], radius: float, color) -> None:
    x, y = center
    draw.ellipse((x - radius, y - radius, x + radius, y + radius), fill=color)
