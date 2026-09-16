"""Watch face drawing.

Produces the RGBA image that gets pushed to the VR overlay. It needs no
headset, so tools/preview.py can render every state to a file.

The layout is built around being readable in half a second out of the
corner of your eye:
  - the number is as large as it fits
  - out-of-range shows as colour, so it reads before the digits do
  - and as a marker on one edge of the card, so it still reads for the
    1-in-20 viewers whose colour vision would collapse the palette
  - stale data goes grey, so an old value is never mistaken for a live one
All text is ASCII, so it survives fonts without CJK glyphs.

The card grows a row for the history's average and a history sparkline
below all that when they are asked for, and stays 256 tall when they
are not -- so the frontend that wants a glanceable number only still
gets exactly the face it always had. See `cgm.face.average` and
`cgm.face.graph`, and `WatchFaceRenderer.width` and `.height`, which are
the size to build a texture or a window from: neither is one number.

Status is carried on two independent channels. Colour gives severity;
the marker's *position* gives direction -- above range lights the top
edge, below range the bottom -- and position does not depend on seeing
colour at all. tools/check_palette.py asserts the colour half holds up;
the position half is why it does not have to hold up alone.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from cgm.face.average import history_average
from cgm.face.graph import GraphTuning, draw_sparkline, format_value

log = logging.getLogger(__name__)

# The face itself. The layout below is tuned to this -- font sizes,
# where the arrow sits next to the digits, how thick a marker reads --
# so it is a constant rather than an argument: a second size would be a
# second layout to keep in step with the first. Scaling to a window is
# done by resampling the finished image (see cgm.desk.window.compose).
HEIGHT = 256

# The width is not a constant, because it is not a choice. The card is
# exactly as wide as the widest reading it can show, with its arrow, and
# TEXT_MARGIN either side -- see WatchFaceRenderer._card_width. It used
# to be a flat 512, which left a column of nothing right of the arrow,
# and under the sparkline that column was not nothing: it stretched the
# time axis, so a rise drawn there looked gentler than the same rise on
# the phone. Sizing the card by what it holds narrows the graph without
# leaving any empty space to show for it.
#
# The margin the text keeps from both side edges of the card.
TEXT_MARGIN = 44
# The arrow: how long it is, and how far its middle sits right of the
# digits. Nothing it can be bent into reaches further right than half
# its length, so that is all the width the card gives it.
ARROW_LENGTH = 84
ARROW_GAP = 66
# The widest text each unit can put in the digits, with 8 standing for
# the widest figure the font has. Three figures in mg/dL, since
# LibreLink tops out at 500; two and a decimal in mmol/L, which is what
# makes that card the wider of the two.
WIDEST_VALUE = {"mgdl": "888", "mmol": "88.8"}

# What the card grows by when the sparkline is on. The face above it
# does not move at all: every element keeps the coordinates it had, and
# the strip is added underneath.
#
# The size is set by the labels, not by taste.
#
# The tightest pair is now two adjacent gridlines, 50 mg/dL apart on a
# scale of 250 -- a fifth of the plot, whatever the plot is. At the
# 136px this leaves that is 27 pixels against an 18px axis font, so six
# labels stack up the side with about seven pixels of air between them.
#
# That is the loosest this constraint has ever been. It used to be the
# floor and low_mgdl, twenty mg/dL apart, at ten pixels; they cleared
# each other only because the floor's label hangs below its line
# instead of straddling it, and everything shorter was tried and
# failed -- at 104 they touched, and at 64 the strip read as a bar
# rather than a graph. low_mgdl lost its label when the plot was ruled,
# so the height is no longer pinned by that pair. It has not been cut
# because 136px of plot is what makes eight hours of trace read as a
# shape at arm's length, which a headset confirmed and which is the
# thing the strip is actually for.
#
# The rest of the strip is the two rows of labels underneath.
GRAPH_HEIGHT = 184

# What the card grows by when the average row is on, between the unit
# and age row and the sparkline. Everything above it keeps its place and
# everything below it moves down by exactly this, graph included.
#
# The row is a section of its own rather than a third line of small grey
# text under the unit and the age. Stacked that way the three ran
# together and the average read as part of the reading. So a rule cuts
# it off from the face above, and inside it the label is quiet and the
# number is not -- the same label-and-value split the eye already reads
# in "mg/dL ... 1m", one step down in size.
AVERAGE_HEIGHT = 66
# Where the rule and the row sit, in canvas coordinates. The rule clears
# the unit row's descenders; the row clears the plot's top label below.
AVERAGE_RULE_Y = 242
AVERAGE_ROW_Y = 274
AVERAGE_RULE_COLOR = (58, 62, 74)
AVERAGE_LABEL_FONT = 26
AVERAGE_LABEL_COLOR = (120, 125, 138)
AVERAGE_VALUE_FONT = 38
# Lighter than the unit, darker than the digits: a number worth reading,
# and still not the reading.
AVERAGE_VALUE_COLOR = (206, 210, 220)

# Where the trace lives inside that strip.
#
# The right edge lines up with the text above -- the age ends
# TEXT_MARGIN in from the edge -- so the graph reads as the same column of information
# rather than a panel bolted on. The left does not: it gives up a
# gutter for the level labels, which are right-aligned into it and so
# still start inside the 44 the rest of the face keeps.
#
# The bottom margin holds two things, the row of times and then the low
# marker's own room. The marker must not touch the labels or the plot,
# or a low would look like the graph had a floor drawn under it.
GRAPH_MARGIN_X = 44
GRAPH_LABEL_GUTTER = 46
GRAPH_TOP = 244
GRAPH_BOTTOM_MARGIN = 60

# How old a reading gets before the face goes grey, when nobody says.
# `display.stale_after_min` takes its default from here, so the preview
# sheets and the running face agree about when grey starts.
#
# A floor set by the poll cycle, not a preference: with a 60 second
# interval a healthy reading's age sawtooths up to about 122 seconds, so
# anything under 2.1 blinks grey during ordinary fetching. 2.5 clears
# that and still greys about a minute after an update goes missing. The
# measurement behind it is beside the key in config.example.toml.
STALE_AFTER_MIN = 2.5

# Tried in order; all ship with Windows.
FONT_CANDIDATES = [
    "C:/Windows/Fonts/segoeuib.ttf",  # Segoe UI Bold
    "C:/Windows/Fonts/arialbd.ttf",   # Arial Bold
    "C:/Windows/Fonts/DejaVuSans-Bold.ttf",
]


@dataclass
class Theme:
    """Colours and thresholds. Thresholds are mg/dL even in mmol mode.

    Green for in range and red for low match the official FreeStyle
    Libre app. That is deliberate: the phone is the other place these
    numbers are read, and a value that means "fine" in one colour there
    and another colour here is its own hazard. The colour language is
    shared on purpose.

    It is not, however, a safe palette on its own. Green against red is
    the axis the common colour vision deficiencies remove, and under
    simulation the pair is closer than any other on the face. Abbott's
    own answer to that is voice accessibility, which a VR overlay cannot
    borrow -- there is no screen reader here, and the whole point is to
    not have to read the digits. So the direction is carried by the
    marker position in STATUS_MARKERS instead, and colour is the
    redundant channel for the pairs that share a marker edge.

    The one part that is not free to move is how light these are. A
    saturated red is unusable here: under protanopia (255, 0, 0) sits at
    2.78 contrast against the card, well under the 4.5 legibility floor,
    because protanopes lose sensitivity to exactly those wavelengths.
    The red below is already about as red as stays legible.

    Do not retune these by eye. tools/check_palette.py simulates the
    palette under protanopia and deuteranopia; run it after any change
    here, and read its warnings as well as its exit code.
    """

    low_mgdl: float = 70.0
    high_mgdl: float = 180.0
    very_high_mgdl: float = 240.0

    color_in_range: tuple[int, int, int] = (126, 231, 135)   # green
    color_low: tuple[int, int, int] = (255, 107, 107)        # red
    color_high: tuple[int, int, int] = (255, 214, 70)        # yellow
    color_very_high: tuple[int, int, int] = (238, 104, 32)   # deep orange
    color_stale: tuple[int, int, int] = (126, 131, 143)      # grey
    color_bg: tuple[int, int, int, int] = (14, 16, 22, 225)  # translucent black

    def status(self, mgdl: float) -> str:
        """Name the band a reading falls in.

        High is split in two so "drifting over range" and "far over range"
        do not look alike: yellow up to very_high, deep orange past it.
        """
        if mgdl < self.low_mgdl:
            return "low"
        if mgdl > self.very_high_mgdl:
            return "very_high"
        if mgdl > self.high_mgdl:
            return "high"
        return "in_range"

    def status_color(self, mgdl: float) -> tuple[int, int, int]:
        """Pick the status colour for a reading."""
        return {
            "low": self.color_low,
            "very_high": self.color_very_high,
            "high": self.color_high,
            "in_range": self.color_in_range,
        }[self.status(mgdl)]


# The rounded card is the VR one: the corners are fully transparent, the
# compositor shows the game through them, and the card reads as an object
# floating over the scene. A window has nothing behind it, so it asks for
# 0 instead -- see WatchFaceRenderer's `rounded`.
CARD_RADIUS = 32
MARKER_THICKNESS = 14
# A marker starts this far in from the corner. It began as the arc's
# radius, because a bar starting at the corner would be clipped into a
# wedge by it, and it is a constant of its own now that a square card can
# ask for no arc at all: the status signal is which edge lights up, and
# the bar being the same length whichever frontend is showing it keeps
# that one thing identical between them.
MARKER_INSET = 32
# The stale frame: an outline this far inside the card's edge, this wide.
FRAME_INSET = 2
FRAME_WIDTH = 6

# The one strip of the card no marker ever lights, as how far in from
# the right-hand edge it starts and stops -- counted from that edge,
# since the card's width depends on the unit: from where the top and
# bottom bars stop to where the stale frame starts, the whole height.
# Nothing lights it because there is no right-hand marker, and the
# desktop window counts on that -- it stands its gear and its VR mark
# here, where an opaque label cannot cut a hole in a lit edge. A marker
# on the right would leave them nowhere to go.
CLEAR_COLUMN = (MARKER_INSET, FRAME_INSET + FRAME_WIDTH)

# Status -> which edge of the card lights up. Position is the half of the
# signal that does not depend on colour vision: above range lights the
# top, below range the bottom, and the two are never confusable however
# the hues are perceived. In range keeps the left bar the face has always
# had, and stale outlines the whole card, so no state is signalled by the
# *absence* of a marker -- absence is harder to notice than a mark in a
# different place.
STATUS_MARKERS = {
    "in_range": "left",
    "high": "top",
    "very_high": "top_heavy",  # same edge as high; severity is the colour
    "low": "bottom",
    "stale": "frame",
}


# TrendArrow value -> arrow angle in degrees. 0 points right, positive up.
# Arrows are drawn rather than typeset: Segoe UI and the other stock
# Windows fonts have no U+2197/U+2198 glyphs and render tofu boxes.
#
# This is the last fallback. When there is enough history the angles
# come from TrendTuning instead, and are not restricted to these five.
TREND_ANGLES = {1: -90.0, 2: -45.0, 3: 0.0, 4: 45.0, 5: 90.0}

# The most the two segments of a bent arrow may fold against each other.
# Each is free to swing +/-90 on its own, so an uncapped bend can meet
# at a hairpin -- and at 84 pixels a hairpin is a blob with a head
# somewhere in it rather than a signal. The head segment is the recent
# one and keeps the angle it earned; the tail is pulled in to within
# this of it, so what is lost is how sharp the turn was rather than
# where the arm is going now.
MAX_BEND_DEG = 60.0


@dataclass(frozen=True)
class TrendShape:
    """The arrow to draw, and where it came from.

    `angles` is one per segment, tail first, so the head sits on the
    last of them. Empty means there is no arrow at all -- a TrendArrow
    outside the five documented values, on a reading with no history.

    `rates` is what those segments were measured at, in mg/dL per
    minute, and is empty for the API fallback because there is no rate
    behind its five positions. It is carried for the log rather than for
    the drawing: `fast_mgdl_min` is the magnification between the two,
    and reading them against each other over real days is how that
    number was settled.
    """

    angles: tuple[float, ...]
    source: str
    rates: tuple[float, ...] = ()

    def describe(self, arrow: str = "") -> str:
        """One line saying what was drawn and which source drew it.

        Both the fetch log and `--dry-run` print this. A bend that
        quietly stopped appearing would otherwise read as calm glucose.
        """
        if not self.rates:
            return f"{arrow} (API)"
        rates = "/".join(f"{rate:+.2f}" for rate in self.rates)
        angles = "/".join(f"{angle:+.0f}" for angle in self.angles)
        return f"{rates} mg/dL/min ({self.source}, {angles} deg)"


@dataclass
class TrendTuning:
    """How a rate of change becomes an arrow angle.

    Abbott's TrendArrow is five buckets on thresholds it does not
    publish and nothing here can adjust. The history the same response
    already carries says more than that, and the arrow is drawn as a
    vector anyway, so it can point anywhere rather than snapping to five
    positions -- a reading climbing gently and one climbing hard both
    come out as the same arrow otherwise.

    One number sets the whole scale: fast_mgdl_min is the rate at which
    a segment stands straight up, and everything below it is in
    proportion -- half that rate is the familiar 45 degree diagonal.
    Nothing steeper than 90 exists to draw, so that is where it stops.
    Bending the arrow through two segments makes that number a
    magnification on a shape rather than a scale on a single angle,
    which is the difference between a setting nobody notices and the one
    that decides whether the face is readable or twitchy.

    2.0 is measured, not guessed. The arrow is there to show the recent
    movement of the graph, so what it is judged on is how much of that
    movement survives as shape. Across 4,929 fetches from 2026-09-11 to
    09-16, at 2.0 the two segments differ by 15 degrees or more on 63%
    of bends, and both sit pinned at 90 -- a straight arrow, the shape
    gone -- on 1.8%, about 16 minutes a day and mostly at night. 1.5
    buys three points of bend for four times the pinning; 3.0 halves the
    pinning that is already rare and flattens the median bend from 23
    degrees to 16. Jitter is not what limits it: with glucose inside 8
    mg/dL over half an hour, 90% of head segments stay under 14 degrees.
    On the face, the arrow reads as bent rather than stuck.

    This lives with the renderer rather than the API client because the
    arithmetic is cheap and `config.toml` is re-read while running:
    working the angles out at draw time is what lets a tuning edit land
    within a second, the same as placement does. `local` rides on the
    same reload, so the two arrows can be compared by switching between
    them with the headset on rather than by restarting twice.
    """

    local: bool = True
    fast_mgdl_min: float = 2.0

    def shape_for(self, reading) -> TrendShape:
        """The arrow to draw for a reading, and the name of its source.

        The one place that choice is made. The face and the fetch log
        both ask here, so the log cannot end up claiming a source the
        face is not using.

        Three shapes, in the order of how much of the last half hour is
        actually there. The bend is the answer; the other two are what
        is left when the points for it are not:

          - `bend`: three points inside the last 45 minutes, drawn as
            two segments with the head on the newer one
          - `fit`: three points anywhere in the fit's window, drawn as
            the single straight arrow this used to draw always
          - `api`: fewer than that, or `local` off, drawn as one of
            TrendArrow's five positions
        """
        if self.local:
            rates = reading.segment_rates()
            if rates is not None:
                return TrendShape(self._folded(rates), "bend", rates)
            slope = reading.slope_mgdl_per_min()
            if slope is not None:
                return TrendShape((self.angle_for_slope(slope),), "fit", (slope,))

        angle = TREND_ANGLES.get(reading.trend)
        return TrendShape(() if angle is None else (angle,), "api")

    def angle_for_slope(self, slope: float) -> float:
        """Map mg/dL per minute onto an angle, 0 level and +/-90 vertical."""
        fraction = slope / self.fast_mgdl_min
        return 90.0 * max(-1.0, min(1.0, fraction))

    def _folded(self, rates) -> tuple[float, ...]:
        """Segment angles, with the fold between them capped.

        Measured against the head, which is the segment describing now
        and so the one that has to arrive unaltered.
        """
        angles = [self.angle_for_slope(rate) for rate in rates]
        head = angles[-1]
        return tuple(
            max(head - MAX_BEND_DEG, min(head + MAX_BEND_DEG, angle))
            for angle in angles
        )


def _draw_arrow(
    draw: ImageDraw.ImageDraw,
    center: tuple[float, float],
    angles: tuple[float, ...],
    length: float,
    color: tuple[int, int, int],
    width: int = 13,
) -> None:
    """Draw an arrow along one or more segments, as vector shapes.

    `angles` gives each segment its own angle, tail first, so the head
    sits on the last of them and the joints in between are where the
    arrow bends. The segments share `length` equally, and the middle of
    the path -- measured along it, not across its bounding box -- lands
    on `center`. With a single angle that is the midpoint of the one
    segment, which is exactly where a straight arrow has always been
    drawn, so bending it moved nothing that was already there.

    Screen Y grows downwards, so the sine is negated to make a positive
    angle point up.
    """
    span = length / len(angles)
    points = [(0.0, 0.0)]
    for angle in angles:
        rad = math.radians(angle)
        x, y = points[-1]
        points.append((x + math.cos(rad) * span, y - math.sin(rad) * span))

    # Halfway along the path: a joint when there is an even number of
    # segments, and inside one when there is an odd number.
    half = len(angles) / 2
    whole = int(half)
    part = half - whole
    ax, ay = points[whole]
    if part:
        bx, by = points[whole + 1]
        ax, ay = ax + (bx - ax) * part, ay + (by - ay) * part
    shift_x, shift_y = center[0] - ax, center[1] - ay
    points = [(x + shift_x, y + shift_y) for x, y in points]

    # The head belongs to the last segment, whatever the ones before it
    # are doing.
    rad = math.radians(angles[-1])
    dx, dy = math.cos(rad), -math.sin(rad)
    px, py = -dy, dx  # unit vector perpendicular to travel

    tip = points[-1]
    head_len = length * 0.42
    head_half_width = length * 0.30

    # Stop the shaft short of the head so it does not poke out of the
    # tip. `joint="curve"` rounds the bend, which at this width is the
    # difference between a turn and a notch cut out of the outside of it.
    shaft_end = (tip[0] - dx * head_len * 0.75, tip[1] - dy * head_len * 0.75)
    draw.line(points[:-1] + [shaft_end], fill=color, width=width, joint="curve")

    base = (tip[0] - dx * head_len, tip[1] - dy * head_len)
    draw.polygon(
        [
            tip,
            (base[0] + px * head_half_width, base[1] + py * head_half_width),
            (base[0] - px * head_half_width, base[1] - py * head_half_width),
        ],
        fill=color,
    )


def unit_label(unit: str) -> str:
    """How the display unit is spelled out.

    On the face, in the window's title bar and in what --dry-run prints.
    One function because three copies of the same conditional is three
    chances for one of them to disagree with the number beside it.
    """
    return "mmol/L" if unit == "mmol" else "mg/dL"


def face_image(
    renderer: "WatchFaceRenderer",
    reading,
    error: str | None,
    *,
    stale_after_min: float,
) -> Image.Image:
    """The face to show for what the poller currently holds.

    A reading is drawn even when the last fetch failed: it keeps ageing
    on screen and greys out, which is the honest thing for a value that
    was true a while ago. Only a poller that has never had a reading at
    all falls back to a message card, because there is nothing yet to
    put an age on.

    Both frontends ask here, so a window and a headset cannot end up
    disagreeing about what an empty poller looks like.
    """
    if reading is not None:
        return renderer.render(reading, stale_after_min=stale_after_min)
    return renderer.render_message(error or "WAITING", detail="no reading yet")


def _load_font(size: int) -> ImageFont.FreeTypeFont:
    for path in FONT_CANDIDATES:
        if Path(path).exists():
            try:
                return ImageFont.truetype(path, size)
            except OSError:
                continue
    log.warning("no TrueType font found, falling back to the bitmap font")
    return ImageFont.load_default()


class WatchFaceRenderer:
    """Builds the glucose watch face image.

    Loading fonts is expensive, so it happens once here and is reused for
    every later frame.
    """

    def __init__(
        self,
        theme: Theme | None = None,
        unit: str = "mgdl",
        trend: TrendTuning | None = None,
        graph: GraphTuning | None = None,
        rounded: bool = True,
        average: bool = False,
    ) -> None:
        self.theme = theme or Theme()
        self.unit = unit
        self.trend = trend or TrendTuning()
        # None is the switch as well as the absence of tuning: one
        # object says both whether there is a sparkline and how it is
        # scaled, so the two cannot be set to disagree. Which frontend
        # gets one is `cgm.main`'s decision, not this class's.
        self.graph = graph
        # The average row, which frontend gets it being `cgm.main`'s
        # decision for the same reason the graph is.
        self.average = average
        # The corner arc is for a compositor. Anything that keeps its
        # alpha channel keeps it; the Tk window, which flattens the card
        # onto an opaque backdrop, asks for square corners instead, or
        # the four transparent arcs come out as wedges bitten out of the
        # picture. Which frontend gets which is `cgm.main`'s decision,
        # like the graph above it.
        self.corner_radius = CARD_RADIUS if rounded else 0
        self._font_value = _load_font(150)
        self._font_small = _load_font(38)
        self._font_message = _load_font(52)
        # Messages too long for the card at that size, at the size they fit.
        self._fitted_fonts: dict[str, ImageFont.FreeTypeFont] = {}
        self.width = self._card_width()
        self._average_height = AVERAGE_HEIGHT if average else 0
        self.height = (
            HEIGHT + self._average_height + (GRAPH_HEIGHT if graph is not None else 0)
        )
        # Smaller than anything else on the card on purpose: the axis
        # labels are there to be read when you go looking for them, not
        # to compete with the number for the half-second glance. Small
        # enough, too, that the six gridline labels stack up the side of
        # the plot without touching -- see GRAPH_HEIGHT. If they ever
        # stop clearing each other this is the thing to move, not the
        # card: the strip is sized by the trace now, not by the labels.
        self._font_axis = _load_font(18)
        self._font_average_label = _load_font(AVERAGE_LABEL_FONT)
        self._font_average_value = _load_font(AVERAGE_VALUE_FONT)

    # -- public API ---------------------------------------------------------

    def render(
        self, reading, *, stale_after_min: float = STALE_AFTER_MIN, now=None
    ) -> Image.Image:
        """Draw the watch face for a reading.

        Readings older than stale_after_min go grey with the age
        emphasised. The last value stays on screen when the network drops,
        so it has to be obvious when it is no longer current.

        `now` is the instant the face is being drawn at, and defaults to
        the wall clock, which is what both frontends want. Passing one
        makes the whole card reproducible -- the age readout, whether it
        has gone stale, and the times under the graph -- which is what
        lets tools/preview.py commit a PNG that does not change every
        time it is rendered.
        """
        age = reading.age_minutes(now)
        is_stale = age >= stale_after_min

        mgdl = reading.value_mgdl
        # Stale wins over the glucose band on both channels. An hour-old
        # low is not a low now, so it must not light the low edge.
        status = "stale" if is_stale else self.theme.status(mgdl)
        color = self.theme.color_stale if is_stale else self.theme.status_color(mgdl)

        img, draw = self._new_canvas(color, STATUS_MARKERS[status])

        value_text = reading.display_value(self.unit)
        unit_text = unit_label(self.unit)

        # Left-aligned, leaving the right side for the arrow and the age.
        draw.text(
            (TEXT_MARGIN, 118),
            value_text,
            font=self._font_value,
            fill=color,
            anchor="lm",
        )

        value_right = TEXT_MARGIN + draw.textlength(value_text, font=self._font_value)

        # The arrow sits right next to the number to minimise eye travel.
        shape = self.trend.shape_for(reading)
        if shape.angles:
            _draw_arrow(
                draw, (value_right + ARROW_GAP, 116), shape.angles, ARROW_LENGTH, color
            )

        draw.text(
            (46, 206), unit_text, font=self._font_small, fill=(160, 165, 178), anchor="lm"
        )

        # The age is only coloured when stale, to draw attention then.
        age_text = self._format_age(age)
        draw.text(
            (self.width - TEXT_MARGIN, 206),
            age_text,
            font=self._font_small,
            fill=color if is_stale else (160, 165, 178),
            anchor="rm",
        )

        if self.average:
            self._draw_average(draw, reading)

        if self.graph is not None:
            draw_sparkline(
                draw,
                self._graph_box(),
                reading.history,
                tuning=self.graph,
                theme=self.theme,
                # The reading's own timestamp, not the wall clock: the
                # right-hand edge of the graph is the measurement the
                # digits are showing. Same anchor the slope is fitted
                # over, so the arrow and the trace describe one window.
                now=reading.timestamp_utc,
                accent=color,
                unit=self.unit,
                font=self._font_axis,
            )

        return img

    def render_message(self, message: str, *, detail: str = "") -> Image.Image:
        """Draw a status or error card.

        Keeps "no reading at all" visually distinct from a real value.
        """
        img, draw = self._new_canvas(self.theme.color_stale, STATUS_MARKERS["stale"])
        # Centred on the card rather than at the coordinates the graphless
        # face uses, so the message sits in the middle of a card that has
        # grown a sparkline strip too. No graph is drawn here: a message
        # card has no history behind it, and an empty band under "NO
        # CONNECTION" would be one more thing to read for nothing.
        middle = self.height // 2
        draw.text(
            (self.width // 2, middle - 24 if detail else middle),
            message,
            font=self._message_font(message, draw),
            fill=(226, 228, 235),
            anchor="mm",
        )
        if detail:
            draw.text(
                (self.width // 2, middle + 40),
                detail,
                font=self._font_small,
                fill=(150, 155, 168),
                anchor="mm",
            )
        return img

    # -- internals ----------------------------------------------------------

    def _card_width(self) -> int:
        """How wide the card is: its widest reading, the arrow, and margins.

        Measured off the font that actually loaded rather than written
        down, so a machine that falls back to another face still gets a
        card that holds its digits.
        """
        measure = ImageDraw.Draw(Image.new("L", (1, 1)))
        widest = max(
            "0123456789", key=lambda d: measure.textlength(d, font=self._font_value)
        )
        template = WIDEST_VALUE["mmol" if self.unit == "mmol" else "mgdl"]
        digits = measure.textlength(template.replace("8", widest), font=self._font_value)
        return math.ceil(TEXT_MARGIN + digits + ARROW_GAP + ARROW_LENGTH / 2 + TEXT_MARGIN)

    def _message_font(self, message: str, draw: ImageDraw.ImageDraw):
        """The message size, or smaller if that would run past the margins.

        The card is only as wide as a reading needs, and "NO CONNECTION"
        at full size is wider than that.
        """
        room = self.width - 2 * TEXT_MARGIN
        length = draw.textlength(message, font=self._font_message)
        if length <= room:
            return self._font_message
        if message not in self._fitted_fonts:
            size = int(self._font_message.size * room / length)
            self._fitted_fonts[message] = _load_font(size)
        return self._fitted_fonts[message]

    def _draw_average(self, draw: ImageDraw.ImageDraw, reading) -> None:
        """A rule, then the span as a quiet label and the mean as a number.

        Grey whatever the reading is. The status colours say what the
        glucose is now, and a mean over half a day is not now.

        The span is in the label, measured rather than assumed, because
        twelve hours is what the API usually sends and not a promise: a
        fresh sensor sends less, and an average that does not say what
        it is an average of reads as more than it is. Too little history
        to average is written as dashes rather than left blank, so the
        row does not look like it failed to draw.
        """
        draw.line(
            (TEXT_MARGIN, AVERAGE_RULE_Y, self.width - TEXT_MARGIN, AVERAGE_RULE_Y),
            fill=AVERAGE_RULE_COLOR,
            width=2,
        )
        stats = history_average(reading.history)
        if stats is None:
            label, value = "avg", "--"
        else:
            label = f"{stats.span_min / 60:.0f}h avg"
            value = format_value(stats.mgdl, self.unit)
        draw.text(
            (TEXT_MARGIN + 2, AVERAGE_ROW_Y),
            label,
            font=self._font_average_label,
            fill=AVERAGE_LABEL_COLOR,
            anchor="lm",
        )
        draw.text(
            (self.width - TEXT_MARGIN, AVERAGE_ROW_Y),
            value,
            font=self._font_average_value,
            fill=AVERAGE_VALUE_COLOR,
            anchor="rm",
        )

    def _graph_box(self) -> tuple[float, float, float, float]:
        """The plot rectangle, in canvas coordinates.

        The labels are drawn outside it -- levels in the gutter to its
        left, times in the margin below -- so this is what decides how
        much room each of them gets.
        """
        return (
            GRAPH_MARGIN_X + GRAPH_LABEL_GUTTER,
            GRAPH_TOP + self._average_height,
            self.width - GRAPH_MARGIN_X,
            self.height - GRAPH_BOTTOM_MARGIN,
        )

    def _new_canvas(self, accent: tuple[int, int, int], marker: str):
        """Background card with the status marker on one edge."""
        img = Image.new("RGBA", (self.width, self.height), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        draw.rounded_rectangle(
            (0, 0, self.width - 1, self.height - 1),
            radius=self.corner_radius,
            fill=self.theme.color_bg,
        )
        self._draw_marker(draw, marker, accent)
        return img, draw

    def _draw_marker(
        self, draw: ImageDraw.ImageDraw, marker: str, color: tuple[int, int, int]
    ) -> None:
        """Light one edge of the card, so status reads without the digits.

        The edges are the card's, not the face's: with a sparkline on,
        the bottom marker lights the bottom of the whole card and the
        frame goes round the graph as well. A marker that stopped at
        y=256 would be a line across the middle of the card, which is
        not an edge and does not read as a direction.
        """
        fill = (*color, 255)
        thick = MARKER_THICKNESS
        near, far = MARKER_INSET, self.width - 1 - MARKER_INSET

        if marker == "frame":
            # The whole outline, which is the one shape that cannot be
            # mistaken for a direction. Stale is not "high" or "low"; it is
            # "do not read this as either".
            inset = FRAME_INSET
            draw.rounded_rectangle(
                (inset, inset, self.width - 1 - inset, self.height - 1 - inset),
                # The outline runs inside the card, so its radius is that
                # much tighter and follows the arc instead of cutting
                # across it. Never below zero, though: a square card
                # would take it negative and Pillow rejects that.
                radius=max(0, self.corner_radius - inset),
                outline=fill,
                width=FRAME_WIDTH,
            )
            return

        if marker == "left":
            box = (0, MARKER_INSET, thick, self.height - 1 - MARKER_INSET)
        elif marker == "top":
            box = (near, 0, far, thick)
        elif marker == "top_heavy":
            # Twice as deep as `top`, so the step up in severity is visible
            # even where the yellow and the orange are not.
            box = (near, 0, far, thick * 2)
        elif marker == "bottom":
            box = (near, self.height - 1 - thick, far, self.height - 1)
        else:
            raise ValueError(f"unknown marker: {marker!r}")

        draw.rounded_rectangle(box, radius=thick // 2, fill=fill)

    @staticmethod
    def _format_age(minutes: float) -> str:
        if minutes < 1:
            return "now"
        if minutes < 60:
            return f"{int(minutes)}m"
        hours = minutes / 60
        if hours < 24:
            return f"{int(hours)}h"
        return f"{int(hours / 24)}d"
