"""The history sparkline drawn under the number.

The series comes free. `cgm.core.librelink` already parses `graphData`
off the same response the current value arrives in -- roughly twelve
hours of measurements -- and hands it over on `Reading.history`, where
until now only the trend fit read it. So this costs no request, no
cache and no storage; it is a second reading of data already in hand.

Six rules decide what gets drawn, and each one is there because the
obvious alternative lies:

  - **The Y axis does not shrink to the data.** Its bottom never moves
    at all and its top never drops below the configured one, so a quiet
    flat stretch stays a flat line -- fitting the axis to whatever the
    last few hours happened to do would turn that into a mountain range
    and make a calm reading look alarming. What it will do is grow
    upwards, and only far enough to hold a reading that would otherwise
    have been clipped: a real hyper is the one thing worth redrawing the
    scale for, and the gridline that appears at the new top is what says
    it has happened.
  - **The target range is a band behind the line**, so where the trace
    sits reads without anyone measuring it against an axis. The two
    thresholds a reading must not cross get a dashed line each on top
    of that, because those are the two the band alone does not mark.
  - **The paper is ruled, and identically on every one of these.** A
    line every 50 mg/dL across the plot and a tick every hour beneath
    it, at steps no setting can move, so two of these graphs are read
    against the same scale even when the two configs behind them agree
    about nothing else. The thresholds are the part the reader's own
    numbers put on the chart, which is why they are lines and not
    labels: a level worth a colour is not a level the scale counts in.
  - **The line is red where it was low, and only there.** Not the
    colour of the present reading -- that would repaint the past every
    time the newest point changed band -- but of each stretch that
    actually went under `low_mgdl`, as the phone app draws it. The high
    end stays uncoloured, as the app's does. See TRACE_COLOR.
  - **The line breaks across gaps** rather than spanning them. A joined
    line over a stretch the sensor was not scanning draws data that was
    never measured. The newest point is the exception, because the gap
    in front of it is the service publishing late rather than the sensor
    not reading -- see LAST_GAP_MIN.
  - **The X axis is only as long as the data**, when asked for all of
    it. A window drawn wider than the history behind it is empty space
    that looks like a sensor that stopped rather than one that has not
    been running that long. Asked for a fixed window instead, the line
    is clipped to its left edge rather than started at whichever sample
    first landed inside -- see `edge_point`.

A reading below the bottom is clamped onto it rather than pushed off,
so a 42 draws where AXIS_FLOOR_MGDL is. That is deliberate -- the floor is the
one part of the scale the eye can rely on being in the same place -- and
it is safe here because it is the *number* that says how low a low is,
in digits the size of the card, on a face that has gone red.

The axis labels are the one part of the face that is not fixed text.
They follow `display.unit`, because a "240" shown to someone reading
mmol/L is not a smaller number, it is the wrong one; and the times are
local, because that is the clock the reader is comparing them against.
The step follows the unit as well: 3 mmol/L rather than a converted 50
mg/dL, because 2.8 / 5.6 / 8.3 is not a scale anyone reads off a graph.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import NamedTuple

# How far apart two samples have to be before the line breaks between
# them. The API sends history at one point every fifteen minutes -- see
# GRAPH_RESOLUTION_MIN in cgm.core.librelink -- so twice that is a
# sample that did not arrive rather than one that arrived late. The
# number is repeated here rather than imported because cgm.face imports
# nothing from cgm.core; tests/test_graph.py holds the two in step.
MAX_GAP_MIN = 30.0

# The exception to that rule, and it applies to the newest point only.
#
# One response carries both the current measurement and the history, but
# they are not equally fresh: graphData's newest entry trails
# glucoseMeasurement, by eighteen minutes when this was last measured
# and by thirty in NOTES.md. That trailing distance is how long the
# service takes to publish a sample into the array, not a stretch the
# sensor was not scanning -- the reading at the end of it exists, which
# is the proof it was scanning throughout. So the last point is joined
# across a gap that would break the line anywhere else.
#
# Bounded, because the two cases do look alike from here. If the phone
# really stopped scanning for hours, graphData stops and so does the
# measurement, and joining across that would draw a straight line
# through an afternoon nobody measured. An hour is well past any
# publication lag seen and well short of a gap worth drawing.
LAST_GAP_MIN = 60.0

# The trace is deliberately not the status colour. The digits, the arrow
# and the marker all say what the reading is *now*; colouring an hour of
# history by the present moment would claim the whole line was low the
# instant the last point dipped. It is drawn as data instead, and only
# the newest point takes the status colour, which is what ties it to the
# number above it.
#
# The stretches that really were low are the exception, and the opposite
# of that mistake: not the line painted by the present, but the line
# saying what happened. Each is `color_low` from where the trace went
# under `low_mgdl` to where it came back, cut at the crossing rather
# than at whichever sample was nearer -- see `low_stretches`. That is
# what the phone app draws, and the phone is the other place these
# numbers are read.
#
# The high end stays this colour, however far over it goes, because the
# app's does: watched through a high, it colours nothing there. The top
# is not left unmarked for it -- the dashed very_high line and an axis
# grown to hold the peak both say so -- and a second colour language
# for the same trace would be the thing to avoid.
#
# The red never has to carry that alone. A low stretch is under the
# dashed low line by definition, so where it sits says it as well, for
# a reader who cannot tell this red from the grey around it.
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

# The least air a level label needs above and below it, on top of the
# font's own height. Under that the labels stop reading as a scale and
# start reading as a column of digits stacked against the plot.
#
# It is not the plot that gives way when they no longer fit -- the
# ruling is the same on every one of these graphs and does not thin out
# because one reader had a hyper. The labels do: every second line goes
# unnamed, counted down from the top so the highest one keeps its
# number, and the reader still counts the lines in fifties.
LABEL_AIR = 6

# The floor label hangs below its line, and a few pixels further down
# than the line itself: the anchor puts the top of the text box there,
# and the box has air above the digits that eats into the gap up to
# low_mgdl. Three pixels buys that back.
FLOOR_LABEL_DROP = 3

# The bottom of the Y axis, and not a setting. Every graph of this
# starts at the same place, so the eye learns where the floor is once
# instead of per config file, and two of these side by side are
# comparable. It is also what the plot is sized around: the floor and
# low_mgdl are twenty apart, and their labels have to clear each other.
AXIS_FLOOR_MGDL = 50.0

# What the axis top is rounded up to when a reading goes above it. Round
# numbers, so an axis that has moved still reads as a scale rather than
# as the value of whatever the highest sample happened to be.
AXIS_STEP_MGDL = 50.0

# The dashed reference lines. A dash long enough to read as a line and a
# gap wide enough that it does not read as a solid one.
#
# One pixel, the same weight as the ruling. They were two when they were
# two of the only three lines on the plot; against a ruled plot that
# made them read as the scale rather than as two levels sitting on it.
# Dashed against solid, and coloured against grey, is difference enough.
DASH_ON = 9
DASH_OFF = 7
DASH_WIDTH = 1

# The ruling. Quiet enough to be scenery -- a scale you can measure
# against when you look for it, and not something competing with the
# trace or with the two coloured lines that mean something.
#
# Solid and a single pixel, which is what "this is where the paper is
# ruled" looks like beside a dashed coloured line saying "this is a
# level you care about".
GRID_COLOR = (58, 62, 74)
GRID_WIDTH = 1

# How many mg/dL one mmol/L is. Comparisons in this project are always
# mg/dL; this is only ever used on the way out to a label.
MGDL_PER_MMOL = 18.0

# What the Y axis is ruled at, given in the unit the labels are written
# in rather than converted from one number. A converted 50 mg/dL is 2.8
# mmol/L, and 2.8 / 5.6 / 8.3 / 11.1 is a scale nobody reads; 3 / 6 / 9
# / 12 is the one the phone app draws and the one a reader in mmol/L
# already thinks in. Placement converts back to mg/dL, like every other
# comparison here.
#
# The mg/dL step is AXIS_STEP_MGDL rather than a second 50, and that is
# load-bearing: the axis grows to the next round AXIS_STEP_MGDL above a
# hyper, so it lands on a gridline when it does, and that new line at
# the top is the only thing left saying the scale has moved. In mmol/L
# the two steps do not divide each other and a grown top can fall
# between lines, which is the price of counting in threes.
GRID_STEP = {"mgdl": AXIS_STEP_MGDL, "mmol": 3.0}

# The time axis, in minutes: a stub every hour, a longer one every three
# hours, and only the long ones are labelled.
#
# Fixed, where this used to choose a step that kept the labels under a
# count. That step moved with window_min, so the same face at two window
# lengths was ruled two different ways and no two pictures of it were
# comparable. Three hours of local clock means the same thing at every
# window length, which is worth more than the labels coming out evenly
# spread. It is also the floor under graph.window_min: a shorter window
# can fall between two of these and come out with no label at all.
TICK_MINOR_MIN = 60.0
TICK_MAJOR_MIN = 180.0
TICK_MINOR_PX = 4
TICK_MAJOR_PX = 9
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

    `axis_high_mgdl` is a minimum, not a ceiling: the top sits there on
    an ordinary day and goes as far above it as a reading needs, rounded
    up to AXIS_STEP_MGDL. The bottom is AXIS_FLOOR_MGDL and is not
    settable at all.

    It is mg/dL like every other threshold in this project, including
    under `display.unit = "mmol"`. The labels are converted on the way
    out; the arithmetic never is.
    """

    window_min: float = 480.0
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
        return f"{mgdl / MGDL_PER_MMOL:.1f}"
    return f"{mgdl:.0f}"


def axis_top(points, tuning: GraphTuning) -> float:
    """The top of the Y axis for these points.

    The configured minimum, unless something rose above it, in which
    case the next round step over the highest reading. It never comes
    back down below the configured value, so the axis only ever moves
    for a reason the reader can see on the chart.
    """
    top = tuning.axis_high_mgdl
    if not points:
        return top
    peak = max(p.mgdl for p in points)
    if peak <= top:
        return top
    return math.ceil(peak / AXIS_STEP_MGDL) * AXIS_STEP_MGDL


def grid_step_mgdl(unit: str) -> float:
    """The ruling step in mg/dL, whatever unit it is written in."""
    step = GRID_STEP.get(unit, AXIS_STEP_MGDL)
    return step * MGDL_PER_MMOL if unit == "mmol" else step


def label_stride(pitch_px: float, font_size: float) -> int:
    """How many gridlines to step between one label and the next.

    One while the lines are far enough apart to write a number between,
    two when a grown axis has squeezed them closer than that, and so on.
    The ruling never thins -- only the naming of it does -- so the
    reader is still counting the same lines in the same steps.
    """
    if pitch_px <= 0:
        return 1
    return max(1, math.ceil((font_size + LABEL_AIR) / pitch_px))


def grid_levels(ceiling: float, unit: str) -> list[float]:
    """The mg/dL levels to rule the plot at, floor first and ascending.

    The floor is always one of them -- it is where the scale starts, and
    the one level on this graph guaranteed to sit in the same place on
    everyone else's. Above it the step is GRID_STEP for the display
    unit, counted from zero so the lines land on round numbers in that
    unit rather than on round distances above the floor.

    Counting from zero is what makes the first line a special case in
    mmol/L: the floor is 2.8 and 3 sits 0.2 above it, under two pixels
    away on the plot. A level closer than half a step to the floor is
    dropped, which leaves 2.8 / 6 / 9 / 12 with nothing between the
    first two -- and nothing needed there, since the band and the dashed
    low_mgdl line already mark that stretch.
    """
    step = grid_step_mgdl(unit)
    levels = [AXIS_FLOOR_MGDL]
    n = 1
    while n * step <= ceiling + 1e-9:
        level = n * step
        if level >= AXIS_FLOOR_MGDL + step / 2:
            levels.append(level)
        n += 1
    return levels


class Point(NamedTuple):
    """A point to draw. `GlucosePoint` is one; so is one we worked out.

    Defined here rather than imported because `cgm.face` takes nothing
    from `cgm.core`. Everything this module reads off a measurement is
    these two fields.
    """

    at: datetime
    mgdl: float


def edge_point(points, start: datetime, max_gap_min: float = MAX_GAP_MIN):
    """The value at exactly `start`, or None if nothing crosses it.

    A fixed window begins at a round number of minutes ago and the
    samples do not: they arrive every fifteen, on a grid with no reason
    to line up with it. So the oldest sample inside the window sits
    somewhere in the first fifteen minutes of it, and the trace starts
    up to eleven pixels in from the left of the plot while the band and
    the rules run the full width. It reads as the graph having been
    clipped.

    Nothing is missing there, so nothing is invented to fill it: the
    segment between the last sample before the window and the first one
    inside it was measured, and this is the point where that segment
    crosses the edge. The line is being clipped to the plot rather than
    started late.

    None when there is nothing to cross with -- a history that does not
    reach back that far, which is a real edge and left alone -- or when
    the two samples are too far apart to join, since a gap does not
    stop being a gap for sitting on the boundary.
    """
    inside = [p for p in points if p.at >= start]
    before = [p for p in points if p.at < start]
    if not inside or not before:
        return None

    first, previous = inside[0], before[-1]
    span = (first.at - previous.at).total_seconds()
    if span / 60 > max_gap_min:
        return None

    fraction = (start - previous.at).total_seconds() / span
    return Point(start, previous.mgdl + (first.mgdl - previous.mgdl) * fraction)


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


def segments(
    points,
    max_gap_min: float = MAX_GAP_MIN,
    last_gap_min: float = LAST_GAP_MIN,
) -> list[list]:
    """Split a series wherever the sensor stopped reporting.

    Each returned run is a stretch that may be joined up. A run of one
    is legal and means a single measurement with nothing either side of
    it close enough to connect to.

    The newest point is the exception. It is the current measurement,
    folded into the series by `_with_latest` because graphData
    stops short of it, and the distance back to graphData's own newest
    entry is publication lag rather than missing data -- see
    LAST_GAP_MIN. It is joined across up to that much, and left stranded
    beyond it.
    """
    runs: list[list] = []
    for point in points:
        if runs and (point.at - runs[-1][-1].at).total_seconds() / 60 <= max_gap_min:
            runs[-1].append(point)
        else:
            runs.append([point])

    # Only ever one point can be stranded this way: it is the one the
    # series was extended with, and everything before it came out of the
    # same array at the same resolution.
    if len(runs) > 1 and len(runs[-1]) == 1:
        newest, previous = runs[-1][0], runs[-2][-1]
        if (newest.at - previous.at).total_seconds() / 60 <= last_gap_min:
            runs[-2].append(runs.pop()[0])
    return runs


def low_stretches(run, low_mgdl: float) -> list[list]:
    """The parts of one run that were below `low_mgdl`, oldest first.

    `run` is one of the stretches `segments` returns, so it is joined
    throughout and the only question left is where it was low. A sample
    exactly on the level is not low, the same as `Theme.status` says.

    A stretch that starts or ends between two samples starts or ends
    where the line between them crosses the level, worked out rather
    than rounded to whichever sample was nearer. Rounded, the red would
    begin a whole sample early or late -- up to fifteen minutes of line
    drawn as low that was not, or the other way round -- and would stop
    short of, or run past, the dashed line it is meant to meet.

    A run of one below the level comes back as a stretch of one, which
    is drawn as a dot the same way the run itself is.
    """
    stretches: list[list] = []
    current: list | None = None
    previous = None
    for point in run:
        low = point.mgdl < low_mgdl
        if previous is not None and low != (previous.mgdl < low_mgdl):
            crossing = _crossing(previous, point, low_mgdl)
            if low:
                current = [crossing]
                stretches.append(current)
            else:
                current.append(crossing)
                current = None
        if low:
            if current is None:
                # The run itself starts low: nothing before it to cross.
                current = []
                stretches.append(current)
            current.append(point)
        previous = point
    return stretches


def _crossing(a, b, level: float) -> Point:
    """Where the straight line from `a` to `b` passes `level`.

    Only called with one on each side of it, so the two never share a
    value and the division is safe.
    """
    fraction = (level - a.mgdl) / (b.mgdl - a.mgdl)
    return Point(a.at + (b.at - a.at) * fraction, level)


def time_ticks(
    start: datetime, end: datetime, step_min: float = TICK_MINOR_MIN
) -> list[datetime]:
    """Round local times to rule the X axis at, inside [start, end].

    Both ends arrive in UTC and come back local: the reader is comparing
    these against the clock on the wall, not against the timestamps the
    API sends.

    The step is a fixed length of wall clock, so how many come back is
    whatever the window happens to hold. Called twice -- at
    TICK_MINOR_MIN for the hourly stubs and at TICK_MAJOR_MIN for the
    labelled ones -- and the second result is a subset of the first,
    which is why a long stub simply covers the short one under it.
    """
    span_min = (end - start).total_seconds() / 60.0
    if span_min <= 0:
        return []

    step = step_min
    first = start.astimezone()
    last = end.astimezone()
    midnight = first.replace(hour=0, minute=0, second=0, microsecond=0)
    # The first multiple of the step, counted from local midnight, that
    # is not before the start of the window.
    elapsed = (first - midnight).total_seconds() / 60.0
    at = midnight + timedelta(minutes=step * math.ceil(elapsed / step))

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
    two threshold levels, the card colour the band is mixed into, and
    the red the stretches of trace below `low_mgdl` are drawn in.
    """
    left, top, right, bottom = box
    # The axis is inset by the head's radius at both ends, so a reading
    # clamped onto the top or the bottom of it has its dot land on the
    # edge of the box rather than half outside -- which at the bottom
    # would put it into the low marker's room.
    top += HEAD_RADIUS
    bottom -= HEAD_RADIUS
    span_px = bottom - top

    # The points are windowed first because how high the axis reaches
    # depends on them, and every line below is drawn against that.
    shown = recent(points, tuning.window_min, now)
    floor = AXIS_FLOOR_MGDL
    ceiling = axis_top(shown, tuning)
    span_mgdl = ceiling - floor

    def y_for(mgdl: float) -> float:
        clamped = max(floor, min(ceiling, mgdl))
        return bottom - (clamped - floor) / span_mgdl * span_px

    # The band goes down first, so everything else crosses it rather
    # than disappearing behind it. It is drawn whether or not there is
    # any history: an empty strip with the range still marked says "no
    # data yet" where an empty strip with nothing in it says "broken".
    card = theme.color_bg
    draw.rectangle(
        (left, y_for(theme.high_mgdl), right, y_for(theme.low_mgdl)),
        fill=(*_mix(card[:3], theme.color_in_range, BAND_TINT), card[3]),
    )

    # The ruling: the floor, then a line every step of the display unit
    # up to the top. A fixed step nothing in the config can move, so the
    # trace is measured against the paper rather than against whichever
    # levels this particular reader happens to care about.
    levels = grid_levels(ceiling, unit)
    for level in levels:
        _grid_line(draw, y_for(level), left, right)

    # The two levels a reading is not supposed to be on the wrong side
    # of, drawn over the ruling rather than as part of it. The band
    # already marks low_mgdl as its own lower edge, but a band edge is a
    # change of shade and these two deserve a line: they are the levels
    # the face turns a colour for. Each takes the colour it turns, so
    # the line and the card agree about which end of the scale is which.
    #
    # Neither is labelled. They had a number each when they were two of
    # the only three lines here, and it was the part of the scale that
    # moved from config to config -- the same graph ruled differently
    # for two readers. The line and the colour say which level it is;
    # the number beside it was saying so a third time.
    for level, color in (
        (theme.low_mgdl, theme.color_low),
        (theme.very_high_mgdl, theme.color_very_high),
    ):
        _dashed_line(draw, y_for(level), left, right, color)

    if font is not None:
        # Gridlines carry the numbers and nothing else does. That is the
        # whole rule, the top of the axis included: with axis_high_mgdl
        # on the step the top is simply the last label, and when it is
        # not, the top goes unnamed rather than earning an exception. A
        # hyper that grows the axis rounds to AXIS_STEP_MGDL and lands
        # back on the grid, so the new line arriving at the top is still
        # what says the scale is no longer the one in the config.
        #
        # How many lines get a number is the one thing that moves. The
        # ruling is the same on every one of these graphs, so a grown
        # axis fits more lines into the same strip until the numbers
        # would touch -- and then every second line goes unnamed. That
        # is counted from the top down, which is why the labels are
        # walked in reverse: the highest line keeps its number, so a
        # scale that has moved still says where it now ends.
        #
        # The floor is drawn on its own because it is the one label not
        # centred on its line. It sits at the very bottom, where
        # centring would push half of it under the plot, so it hangs
        # below instead.
        draw.text(
            (left - LABEL_PAD, y_for(floor)),
            format_value(floor, unit),
            font=font,
            fill=LABEL_COLOR,
            anchor="rt",
        )
        pitch = grid_step_mgdl(unit) / span_mgdl * span_px
        stride = label_stride(pitch, font.size)
        for level in levels[:0:-1][::stride]:
            draw.text(
                (left - LABEL_PAD, y_for(level)),
                format_value(level, unit),
                font=font,
                fill=LABEL_COLOR,
                anchor="rm",
            )

    if not shown:
        return

    # Asked for everything, the axis is as long as the history and no
    # longer. Asked for a window, it is that window whether or not the
    # data fills it, so the axis holds still between fetches.
    start = shown[0].at if tuning.all_of_it else now - timedelta(minutes=tuning.window_min)
    span_sec = (now - start).total_seconds()
    width_px = right - left

    # Asked for a window, the line is clipped to its left edge rather
    # than started at whichever sample first fell inside it. Asked for
    # all of it, the edge is the oldest sample and there is nothing
    # before it to clip against.
    if not tuning.all_of_it:
        crossing = edge_point(points, start)
        if crossing is not None:
            shown = [crossing, *shown]

    def x_for(at: datetime) -> float:
        if span_sec <= 0:
            return right
        fraction = (at - start).total_seconds() / span_sec
        return left + max(0.0, min(1.0, fraction)) * width_px

    # The X axis ruled to match the Y: a stub under the plot every hour
    # and a longer one every three, in the grid's own colour. They need
    # no font, so they are drawn whether or not there is one.
    axis_y = y_for(floor)
    for tick in time_ticks(start, now):
        _tick(draw, x_for(tick), axis_y, TICK_MINOR_PX)
    majors = time_ticks(start, now, TICK_MAJOR_MIN)
    for tick in majors:
        _tick(draw, x_for(tick), axis_y, TICK_MAJOR_PX)

    if font is not None:
        # Below the floor's label rather than below the plot: that one
        # hangs into this margin too, and the leftmost time sits far
        # enough left to run into it.
        times_y = axis_y + FLOOR_LABEL_DROP + font.size + LABEL_PAD
        for tick in majors:
            draw.text(
                (x_for(tick), times_y),
                tick.strftime(TIME_FORMAT),
                font=font,
                fill=LABEL_COLOR,
                anchor="mt",
            )

    def plot(points) -> list[tuple[float, float]]:
        return [(x_for(p.at), y_for(p.mgdl)) for p in points]

    # Each run whole first, and its low stretches over the top of it,
    # rather than the run cut into pieces of alternating colour: laid
    # over, the red starts and stops on a line that is already there,
    # so the joins cannot open a gap or lose the curve at a bend.
    for run in segments(shown):
        _trace(draw, plot(run), TRACE_COLOR)
        for stretch in low_stretches(run, theme.low_mgdl):
            _trace(draw, plot(stretch), theme.color_low)

    # The right-hand end is the measurement the digits above are showing,
    # so it is marked in their colour. It is the one place the graph and
    # the number are the same fact, and it says which end is now.
    newest = shown[-1]
    _dot(draw, (x_for(newest.at), y_for(newest.mgdl)), HEAD_RADIUS, accent)


def _trace(draw, plotted: list[tuple[float, float]], color) -> None:
    """A piece of the trace. One point cannot be a line, so it is a dot."""
    if len(plotted) == 1:
        _dot(draw, plotted[0], TRACE_WIDTH / 2, color)
    else:
        draw.line(plotted, fill=color, width=TRACE_WIDTH, joint="curve")


def _grid_line(draw, y: float, left: float, right: float) -> None:
    """A hairline rule. Solid, unlike the threshold lines above it."""
    draw.line([(left, y), (right, y)], fill=GRID_COLOR, width=GRID_WIDTH)


def _tick(draw, x: float, y: float, length: float) -> None:
    """A stub hanging below the plot, marking a time on the X axis."""
    draw.line([(x, y), (x, y + length)], fill=GRID_COLOR, width=GRID_WIDTH)


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
