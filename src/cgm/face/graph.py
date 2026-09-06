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
    sits reads without anyone measuring it against an axis. The two
    thresholds a reading must not cross get a dashed line each on top
    of that, because those are the two the band alone does not mark.
  - **The line breaks across gaps** rather than spanning them. A joined
    line over a stretch the sensor was not scanning draws data that was
    never measured.
  - **The X axis is only as long as the data**, when asked for all of
    it. A window drawn wider than the history behind it is empty space
    that looks like a sensor that stopped rather than one that has not
    been running that long.

Values are clamped into the axis rather than pushed off it, so a reading
past the top rides the edge. It is still visibly past the band, and the
number above says how far.

The axis labels are the one part of the face that is not fixed text.
They follow `display.unit`, because a "240" shown to someone reading
mmol/L is not a smaller number, it is the wrong one; and the times are
local, because that is the clock the reader is comparing them against.
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

# The axis labels. Grey rather than the colour of the line they sit
# beside: red on this face means a low reading, and a red "70" in the
# margin would be saying that when it is only naming a level.
LABEL_COLOR = (150, 155, 168)
LABEL_PAD = 8

# The dashed threshold lines. A dash long enough to read as a line and a
# gap wide enough that it does not read as a solid one.
DASH_ON = 9
DASH_OFF = 7
DASH_WIDTH = 2

# What the time axis is allowed to step by, in minutes, smallest first.
# The first one that fits the span in MAX_TIME_TICKS intervals wins, so
# the labels always land on round wall-clock times.
TIME_STEPS_MIN = (30, 60, 120, 180, 360, 720)
MAX_TIME_TICKS = 4
TIME_FORMAT = "%H:%M"


@dataclass
class GraphTuning:
    """How much history the sparkline shows, and how it is scaled.

    `window_min` is how far back to draw. **Zero means all of it** --
    everything the response carried, which is about twelve hours -- and
    the X axis then spans the oldest point to the newest instead of a
    fixed length. That is the setting for reading the graph; a fixed
    window is the one for glancing at it, because the axis does not
    move underneath you between fetches.

    The axis bounds are mg/dL like every other threshold in this
    project, including under `display.unit = "mmol"`. The labels are
    converted on the way out; the arithmetic never is.
    """

    window_min: float = 180.0
    axis_low_mgdl: float = 40.0
    axis_high_mgdl: float = 300.0

    @property
    def all_of_it(self) -> bool:
        return self.window_min <= 0


def format_value(mgdl: float, unit: str) -> str:
    """A glucose number as the face writes it.

    The same rule as `Reading.display_value`, which cannot be called
    from here: `cgm.face` imports nothing from `cgm.core`.
    tests/test_graph.py asserts the two agree.
    """
    if unit == "mmol":
        return f"{mgdl / 18.0:.1f}"
    return f"{mgdl:.0f}"


def recent(points, window_min: float, now: datetime) -> list:
    """The points inside the window ending at `now`, oldest first.

    `now` is the reading's own timestamp rather than the wall clock, the
    same anchor `Reading.slope_mgdl_per_min` fits over. A stale reading
    then keeps describing the stretch it was taken in, instead of
    sliding out of its own graph while the age readout beside it says
    the same thing more plainly.

    A window of zero or less means every point there is, still ending at
    `now`: nothing newer than the reading is part of the stretch that
    reading describes, however the window was asked for.
    """
    if window_min <= 0:
        return [p for p in points if p.at <= now]
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


def time_ticks(start: datetime, end: datetime) -> list[datetime]:
    """Round local times to label the X axis with, inside [start, end].

    Both ends arrive in UTC and come back local: the reader is comparing
    these against the clock on the wall, not against the timestamps the
    API sends.
    """
    span_min = (end - start).total_seconds() / 60.0
    if span_min <= 0:
        return []

    step = TIME_STEPS_MIN[-1]
    for candidate in TIME_STEPS_MIN:
        if span_min / candidate <= MAX_TIME_TICKS:
            step = candidate
            break

    first = start.astimezone()
    last = end.astimezone()
    midnight = first.replace(hour=0, minute=0, second=0, microsecond=0)
    # The first multiple of the step, counted from local midnight, that
    # is not before the start of the window.
    elapsed = (first - midnight).total_seconds() / 60.0
    at = midnight + timedelta(minutes=step * -(-int(elapsed) // step))

    ticks = []
    while at <= last:
        ticks.append(at)
        at += timedelta(minutes=step)
    return ticks


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
    unit: str = "mgdl",
    font=None,
) -> None:
    """Draw the graph inside `box`, with its labels in the margins.

    `box` is the plot rectangle only. The Y labels go to the left of it
    and the times underneath, so the caller decides how much room they
    get by where it puts the box.

    `accent` is the status colour the rest of the face is drawn in, used
    for the newest point alone. `theme` supplies the target range, the
    two threshold levels and the card colour the band is mixed into.
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

    # The band goes down first, so everything else crosses it rather
    # than disappearing behind it. It is drawn whether or not there is
    # any history: an empty strip with the range still marked says "no
    # data yet" where an empty strip with nothing in it says "broken".
    card = theme.color_bg
    draw.rectangle(
        (left, y_for(theme.high_mgdl), right, y_for(theme.low_mgdl)),
        fill=(*_mix(card[:3], theme.color_in_range, BAND_TINT), card[3]),
    )

    # The two levels a reading is not supposed to be on the wrong side
    # of. The band already marks low_mgdl as its own lower edge, but a
    # band edge is a change of shade and these two deserve a line: they
    # are the levels the face turns a colour for.
    for level in (theme.low_mgdl, theme.very_high_mgdl):
        _dashed_line(draw, y_for(level), left, right, theme.color_low)
        if font is not None:
            draw.text(
                (left - LABEL_PAD, y_for(level)),
                format_value(level, unit),
                font=font,
                fill=LABEL_COLOR,
                anchor="rm",
            )

    shown = recent(points, tuning.window_min, now)
    if not shown:
        return

    # Asked for everything, the axis is as long as the history and no
    # longer. Asked for a window, it is that window whether or not the
    # data fills it, so the axis holds still between fetches.
    start = shown[0].at if tuning.all_of_it else now - timedelta(minutes=tuning.window_min)
    span_sec = (now - start).total_seconds()
    width_px = right - left

    def x_for(at: datetime) -> float:
        if span_sec <= 0:
            return right
        fraction = (at - start).total_seconds() / span_sec
        return left + max(0.0, min(1.0, fraction)) * width_px

    if font is not None:
        for tick in time_ticks(start, now):
            draw.text(
                (x_for(tick), bottom + HEAD_RADIUS + LABEL_PAD),
                tick.strftime(TIME_FORMAT),
                font=font,
                fill=LABEL_COLOR,
                anchor="mt",
            )

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


def _dashed_line(draw, y: float, left: float, right: float, color) -> None:
    """A horizontal dashed rule. Pillow draws solid lines only."""
    x = left
    while x < right:
        draw.line(
            [(x, y), (min(x + DASH_ON, right), y)], fill=color, width=DASH_WIDTH
        )
        x += DASH_ON + DASH_OFF


def _dot(draw, center: tuple[float, float], radius: float, color) -> None:
    x, y = center
    draw.ellipse((x - radius, y - radius, x + radius, y + radius), fill=color)
