"""Status banding, the trend arrow's angle, and the age readout.

Whether the face is legible is a question for `tools/preview.py` and a
pair of eyes, and whether the palette survives colour blindness is one
for `tools/check_palette.py`. What can be asserted here is the part with
edges in it: which band a value falls in, and where the boundaries lie.

The face is the alert here -- haptics do not work on Quest 3 -- so a
threshold comparison being out by one boundary is the difference between
a low announcing itself and a low looking ordinary.

The arrow is here for the same reason: it is a mapping with boundaries
in it. `tests/test_librelink.py` asserts that the rates behind it are
right; what is asserted here is that they reach the drawing as the
angles they should, that the bend between two of them is capped before
it folds into a blob, and that a reading with too little history behind
it still gets an arrow rather than none.

Whether a bent arrow reads at 84 pixels is not a question with an edge
in it, so it is not asked here. That one is for `tools/preview.py
--debug` and a pair of eyes, and in VR for a headset.
"""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from cgm.core.librelink import GlucosePoint, Reading

from cgm.face.renderer import (
    HEIGHT,
    MAX_BEND_DEG,
    STATUS_MARKERS,
    TREND_ANGLES,
    WIDTH,
    Theme,
    TrendShape,
    TrendTuning,
    WatchFaceRenderer,
    _draw_arrow,
    face_image,
    unit_label,
)

THEME = Theme()
TREND = TrendTuning()

format_age = WatchFaceRenderer._format_age


def reading(trend: int = 3, slope: float | None = None, history=None) -> Reading:
    """A reading whose history, if any, runs at `slope` mg/dL per minute.

    `history` takes the points directly, for the cases where the shape
    of the last half hour is the thing being asserted rather than one
    rate through all of it.
    """
    taken_at = datetime.now(timezone.utc)
    if history is None:
        history = ()
        if slope is not None:
            history = tuple(
                GlucosePoint(taken_at - timedelta(minutes=ago), 100.0 - slope * ago)
                for ago in range(20, -1, -1)
            )
    else:
        history = tuple(
            GlucosePoint(taken_at - timedelta(minutes=ago), mgdl)
            for ago, mgdl in history
        )
    return Reading(
        value_mgdl=100.0,
        trend=trend,
        timestamp_utc=taken_at,
        is_high=False,
        is_low=False,
        history=history,
    )


class StatusColor(unittest.TestCase):
    def test_a_value_in_range_is_the_in_range_colour(self):
        self.assertEqual(THEME.status_color(100.0), THEME.color_in_range)

    def test_below_low_is_the_low_colour(self):
        self.assertEqual(THEME.status_color(69.0), THEME.color_low)

    def test_above_high_is_the_high_colour(self):
        self.assertEqual(THEME.status_color(200.0), THEME.color_high)

    def test_above_very_high_is_the_very_high_colour(self):
        # Split from high so drifting over range and being far over it
        # do not look the same.
        self.assertEqual(THEME.status_color(300.0), THEME.color_very_high)

    def test_the_thresholds_themselves_are_in_the_lower_band(self):
        # The comparisons are strict, so a threshold value belongs to the
        # band below it: 70 is not yet low and 180 is not yet high. This
        # is the arbitrary half of the decision, which is why it is
        # written down.
        self.assertEqual(THEME.status_color(THEME.low_mgdl), THEME.color_in_range)
        self.assertEqual(THEME.status_color(THEME.high_mgdl), THEME.color_in_range)
        self.assertEqual(THEME.status_color(THEME.very_high_mgdl), THEME.color_high)

    def test_just_past_each_threshold_changes_band(self):
        self.assertEqual(THEME.status_color(69.9), THEME.color_low)
        self.assertEqual(THEME.status_color(180.1), THEME.color_high)
        self.assertEqual(THEME.status_color(240.1), THEME.color_very_high)

    def test_configured_thresholds_are_used(self):
        # config.toml can move all three, and the bands have to move with
        # them rather than staying on the defaults.
        theme = Theme(low_mgdl=80.0, high_mgdl=160.0, very_high_mgdl=220.0)
        self.assertEqual(theme.status_color(75.0), theme.color_low)
        self.assertEqual(theme.status_color(100.0), theme.color_in_range)
        self.assertEqual(theme.status_color(170.0), theme.color_high)
        self.assertEqual(theme.status_color(230.0), theme.color_very_high)

    def test_thresholds_stay_in_mgdl(self):
        # A mmol reading of 5.6 is 100 mg/dL and perfectly in range. If
        # the mmol number ever reached this function it would read as a
        # severe low, so the value passed in is always mg/dL.
        self.assertEqual(THEME.status_color(5.6), THEME.color_low)


class StatusMarkers(unittest.TestCase):
    """The half of the signal that does not depend on colour vision."""

    def test_every_band_has_a_marker(self):
        # status() and STATUS_MARKERS are edited in different places, and
        # a band with no entry would raise only when that band was drawn.
        bands = {THEME.status(v) for v in (50.0, 100.0, 200.0, 300.0)}
        self.assertEqual(bands | {"stale"}, set(STATUS_MARKERS))

    def test_above_and_below_range_light_opposite_edges(self):
        # The whole point of the marker: these two call for opposite
        # responses, so they must not share an edge whatever the colours
        # do. Both high bands sit on the top edge, low on the bottom.
        self.assertTrue(STATUS_MARKERS["high"].startswith("top"))
        self.assertTrue(STATUS_MARKERS["very_high"].startswith("top"))
        self.assertEqual(STATUS_MARKERS["low"], "bottom")

    def test_stale_is_not_a_direction(self):
        # An old reading is neither high nor low, so it takes the outline
        # rather than an edge that would read as one.
        self.assertEqual(STATUS_MARKERS["stale"], "frame")

    def test_every_band_is_marked_differently(self):
        self.assertEqual(len(set(STATUS_MARKERS.values())), len(STATUS_MARKERS))


class CardCorners(unittest.TestCase):
    """The arc is for a compositor, so a frontend without one drops it."""

    @staticmethod
    def corners(image):
        return [
            image.getpixel(xy)
            for xy in (
                (0, 0),
                (image.width - 1, 0),
                (0, image.height - 1),
                (image.width - 1, image.height - 1),
            )
        ]

    def test_the_rounded_card_leaves_its_corners_transparent(self):
        # What the VR compositor shows the game through, and the default
        # because the overlay is the frontend with something behind it.
        face = WatchFaceRenderer().render(reading())
        for pixel in self.corners(face):
            self.assertEqual(pixel[3], 0)

    def test_the_square_card_fills_them_in(self):
        # The window flattens the face onto an opaque backdrop, so a
        # transparent corner is a wedge bitten out of the picture rather
        # than a shape.
        face = WatchFaceRenderer(rounded=False).render(reading())
        for pixel in self.corners(face):
            self.assertEqual(pixel, THEME.color_bg)

    def test_the_stale_frame_survives_square_corners(self):
        # The outline is drawn 2px inside the card's own radius, which
        # goes negative on a square card and Pillow raises on that.
        stale = WatchFaceRenderer(rounded=False).render(
            reading(), stale_after_min=0.0
        )
        self.assertEqual(
            stale.getpixel((WIDTH // 2, 2)), (*THEME.color_stale, 255)
        )

    def test_the_marker_keeps_its_inset_either_way(self):
        # The status signal is which edge lights up, and the bar being
        # the same length in both frontends keeps that one thing
        # identical however the face is being shown. In range lights the
        # left edge, so the lit run down column 0 is the whole marker.
        lit = [
            {
                y
                for y in range(face.height)
                if face.getpixel((0, y))[:3] == THEME.color_in_range
            }
            for face in (
                WatchFaceRenderer().render(reading()),
                WatchFaceRenderer(rounded=False).render(reading()),
            )
        ]
        self.assertTrue(lit[0])
        self.assertEqual(lit[0], lit[1])


class TrendAngle(unittest.TestCase):
    """Slope to arrow angle: 0 is level, +90 straight up.

    The five official buckets become one continuous scale here, set by
    a single rate. What has to hold is that the familiar angles still
    land where they always did -- otherwise a face that used to read at
    a glance now needs measuring.
    """

    def test_flat_is_level(self):
        self.assertEqual(TREND.angle_for_slope(0.0), 0.0)

    def test_the_fast_rate_is_vertical(self):
        # The one number the scale is built from.
        self.assertAlmostEqual(TREND.angle_for_slope(TREND.fast_mgdl_min), 90.0)

    def test_half_the_fast_rate_is_the_diagonal(self):
        # Where the API would have said "rising": the same angle here,
        # so the scale stays anchored to the arrows people know.
        self.assertAlmostEqual(TREND.angle_for_slope(TREND.fast_mgdl_min / 2), 45.0)

    def test_nothing_points_past_vertical(self):
        # There is no steeper arrow to draw, and one wrapping past the
        # top would read as the opposite direction.
        for slope in (2.5, 6.0, 100.0):
            with self.subTest(slope=slope):
                self.assertEqual(TREND.angle_for_slope(slope), 90.0)
                self.assertEqual(TREND.angle_for_slope(-slope), -90.0)

    def test_falling_mirrors_rising(self):
        for slope in (0.3, 1.0, 1.5, 2.0, 9.0):
            with self.subTest(slope=slope):
                self.assertAlmostEqual(
                    TREND.angle_for_slope(-slope), -TREND.angle_for_slope(slope)
                )

    def test_a_slow_drift_is_visible_but_shallow(self):
        # The whole point of fitting the slope: the API rounds this to a
        # level arrow. A sixth of the fast rate is a sixth of the way up.
        self.assertAlmostEqual(TREND.angle_for_slope(0.33), 90.0 * 0.33 / 2.0)

    def test_the_angle_is_in_proportion_throughout(self):
        # One rate sets the scale, so every angle below it is simply its
        # share of 90 degrees. There is no second threshold to bend it.
        for fraction in (0.1, 0.25, 0.5, 0.75, 1.0):
            with self.subTest(fraction=fraction):
                slope = TREND.fast_mgdl_min * fraction
                self.assertAlmostEqual(TREND.angle_for_slope(slope), 90.0 * fraction)

    def test_the_angle_never_goes_backwards(self):
        # A faster rise must never draw a shallower arrow, at any of the
        # joins between the three segments of the mapping.
        angles = [TREND.angle_for_slope(i / 20) for i in range(0, 61)]
        self.assertEqual(angles, sorted(angles))

    def test_a_configured_rate_moves_the_whole_scale(self):
        # Someone who wants the arrow to react harder to a slow drift
        # lowers this, and every angle has to follow.
        tuning = TrendTuning(fast_mgdl_min=1.0)
        self.assertAlmostEqual(tuning.angle_for_slope(0.5), 45.0)
        # The same slope the default scale draws as a gentle 45 now
        # stands the arrow fully upright.
        self.assertAlmostEqual(tuning.angle_for_slope(1.0), 90.0)
        self.assertAlmostEqual(TREND.angle_for_slope(1.0), 45.0)


class TrendShapeSource(unittest.TestCase):
    """Which of the three arrows the drawing actually uses.

    They are ordered by how much of the last half hour is really there,
    so what has to hold is both that the best available one is picked
    and that the drop to the next is not silent -- the log reads the
    same object the face draws, and `source` is what it names.
    """

    # Three points a quarter of an hour apart: the last half hour of the
    # chart, which is what the arrow is a picture of.
    CLIMB = ((30, 100.0), (15, 115.0), (0, 130.0))
    # The same climb, rolled over. A fit calls this a rise.
    TURNED = ((30, 100.0), (15, 130.0), (0, 115.0))
    # graphData lagging 40 minutes behind the current measurement, which
    # NOTES.md records as ordinary. The last three points now reach 55
    # minutes back, too far to be the last half hour, but the fit's own
    # window still holds them.
    LAGGED = ((70, 90.0), (55, 100.0), (40, 115.0), (0, 145.0))

    def test_the_bend_is_preferred(self):
        shape = TREND.shape_for(reading(trend=3, history=self.CLIMB))
        self.assertEqual(shape.source, "bend")
        self.assertEqual(len(shape.angles), 2)

    def test_a_turn_draws_two_different_angles(self):
        # The whole reason for the bend. Fitted, this reading is a rise;
        # drawn as it happened, it rose and is now falling, and the head
        # -- the last segment -- is the half that says so.
        shape = TREND.shape_for(reading(trend=3, history=self.TURNED))
        self.assertGreater(shape.angles[0], 0)
        self.assertLess(shape.angles[-1], 0)

    def test_the_angles_are_the_rates_through_the_scale(self):
        # Nothing between the rate and the angle but fast_mgdl_min.
        shape = TREND.shape_for(reading(trend=3, history=self.CLIMB))
        for rate, angle in zip(shape.rates, shape.angles):
            self.assertAlmostEqual(angle, TREND.angle_for_slope(rate))

    def test_too_little_for_a_bend_falls_back_to_the_fit(self):
        # The path NOTES.md says will be taken in practice, so it is
        # not a theoretical fallback: it is what a lagging graphData
        # leaves, and the arrow goes back to the single straight vector
        # it used to always be.
        shape = TREND.shape_for(reading(trend=3, history=self.LAGGED))
        self.assertEqual(shape.source, "fit")
        self.assertEqual(len(shape.angles), 1)

    def test_no_history_falls_back_to_the_api_arrow(self):
        # A fresh sensor, or a gap in scanning. The arrow snaps back to
        # the five official positions rather than disappearing.
        for trend, expected in TREND_ANGLES.items():
            with self.subTest(trend=trend):
                shape = TREND.shape_for(reading(trend))
                self.assertEqual(shape.source, "api")
                self.assertEqual(shape.angles, (expected,))

    def test_an_unknown_api_trend_leaves_no_arrow(self):
        # Unchanged behaviour: an unexpected TrendArrow value must not
        # take the process down, and no arrow beats a wrong one.
        self.assertEqual(TREND.shape_for(reading(trend=9)).angles, ())

    def test_an_unknown_api_trend_is_irrelevant_once_there_is_history(self):
        shape = TREND.shape_for(reading(trend=9, history=self.CLIMB))
        self.assertEqual(shape.source, "bend")

    def test_switching_the_fit_off_returns_to_the_api_arrow(self):
        # For anyone who would rather the face and the phone show the
        # same five arrows. The history is still there and still
        # readable; it is simply not asked.
        off = TrendTuning(local=False)
        shape = off.shape_for(reading(trend=4, history=self.CLIMB))
        self.assertEqual(shape.source, "api")
        self.assertEqual(shape.angles, (TREND_ANGLES[4],))

    def test_the_source_is_decided_in_one_place(self):
        # The face and the fetch log both read this object, so a
        # disagreement between them would show as a log line describing
        # an arrow that was never drawn.
        entry = reading(trend=4, history=self.CLIMB)
        self.assertEqual(
            WatchFaceRenderer().trend.shape_for(entry), TREND.shape_for(entry)
        )


class BendCap(unittest.TestCase):
    """How far the two segments may fold against each other.

    Uncapped, two segments each free to swing +/-90 can meet at a
    hairpin, which at 84 pixels is a blob rather than a signal. What has
    to hold is that the cap flattens the turn without ever moving the
    head, which is the segment describing now.
    """

    def hairpin(self, tuning: TrendTuning = TREND) -> TrendShape:
        # A hard climb followed by an equally hard fall: both segments
        # are past the vertical rate on their own.
        return tuning.shape_for(
            reading(history=((30, 100.0), (15, 190.0), (0, 100.0)))
        )

    def test_a_hairpin_is_folded_back(self):
        angles = self.hairpin().angles
        self.assertLessEqual(abs(angles[0] - angles[-1]), MAX_BEND_DEG + 1e-9)

    def test_the_head_keeps_the_angle_it_earned(self):
        # The tail is what gets pulled in. The recent segment is the one
        # the arrow is for, so it must arrive unaltered.
        shape = self.hairpin()
        self.assertAlmostEqual(shape.angles[-1], TREND.angle_for_slope(shape.rates[-1]))

    def test_an_ordinary_bend_is_left_alone(self):
        # Well inside the cap, so nothing is touched: capping a turn
        # that reads perfectly well would be throwing away the shape.
        shape = TREND.shape_for(reading(history=((30, 100.0), (15, 115.0), (0, 118.0))))
        for rate, angle in zip(shape.rates, shape.angles):
            self.assertAlmostEqual(angle, TREND.angle_for_slope(rate))


class TrendDescription(unittest.TestCase):
    """The line the fetch log and --dry-run print.

    It is the only way to see which arrow a session actually drew, and
    the only instrument for settling fast_mgdl_min against a real day --
    so it carries the rates the sensor gave and the angles that number
    made of them, side by side.
    """

    def test_a_bend_names_both_segments_and_its_source(self):
        line = TREND.shape_for(
            reading(history=((30, 100.0), (15, 115.0), (0, 130.0)))
        ).describe()
        self.assertIn("+1.00/+1.00 mg/dL/min", line)
        self.assertIn("bend", line)
        self.assertIn("deg", line)

    def test_a_fit_says_it_is_a_fit(self):
        # Otherwise a session that never once managed a bend would look
        # exactly like one that always did.
        line = TREND.shape_for(
            reading(history=((70, 90.0), (55, 100.0), (40, 115.0), (0, 145.0)))
        ).describe()
        self.assertIn("fit", line)
        self.assertNotIn("bend", line)

    def test_the_api_fallback_is_the_arrow_and_nothing_else(self):
        line = TREND.shape_for(reading(trend=4)).describe("^")
        self.assertEqual(line, "^ (API)")

    def test_a_fall_keeps_its_sign(self):
        line = TREND.shape_for(
            reading(history=((30, 130.0), (15, 115.0), (0, 100.0)))
        ).describe()
        self.assertIn("-1.00/-1.00", line)


class ArrowDrawing(unittest.TestCase):
    """Where the arrow lands on the card.

    Not whether it is legible -- that is `tools/preview.py --debug` and
    a pair of eyes. What is asserted here is the one thing the bend was
    not allowed to change: a straight arrow is drawn exactly where it
    always was.
    """

    @staticmethod
    def drawn(angles: tuple[float, ...]):
        from PIL import Image, ImageDraw

        image = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
        _draw_arrow(ImageDraw.Draw(image), (256.0, 128.0), angles, 84, (255, 0, 0))
        return image

    def test_a_bend_of_nothing_is_the_straight_arrow(self):
        # The path is anchored by its middle measured along itself, so
        # two collinear segments have to occupy exactly what the one
        # segment they add up to occupied. This is what says the bend
        # moved nothing that was already on the face.
        #
        # The box rather than the pixels: a joint is rounded, and on a
        # diagonal that rounding rasterises a few pixels differently
        # from the straight run it sits inside. It never reaches outside
        # it, which is the part that would move the arrow.
        for angle in (0.0, 45.0, -90.0):
            with self.subTest(angle=angle):
                self.assertEqual(
                    self.drawn((angle,)).getbbox(),
                    self.drawn((angle, angle)).getbbox(),
                )

    def test_a_bent_arrow_stays_inside_the_length_it_was_given(self):
        # It shares the card with the digits, so a fold must not reach
        # further than the straight arrow it replaces.
        straight = self.drawn((0.0,)).getbbox()
        bent = self.drawn((60.0, -60.0)).getbbox()
        self.assertLessEqual(bent[2] - bent[0], straight[2] - straight[0])


class UnitLabel(unittest.TestCase):
    """One spelling of the unit, for the face, the title bar and --dry-run."""

    def test_the_two_units(self):
        self.assertEqual(unit_label("mmol"), "mmol/L")
        self.assertEqual(unit_label("mgdl"), "mg/dL")

    def test_anything_else_reads_as_mgdl(self):
        # `_validate` rejects a third unit at startup, so this is the
        # behaviour for a caller that skipped it rather than a supported
        # setting. mg/dL is the safer guess: the range checks are in it.
        self.assertEqual(unit_label(""), "mg/dL")


class FaceForState(unittest.TestCase):
    """What both frontends draw for whatever the poller is holding."""

    def setUp(self):
        self.renderer = WatchFaceRenderer()

    def face(self, reading_or_none, error):
        return face_image(
            self.renderer, reading_or_none, error, stale_after_min=10.0
        )

    def test_a_reading_is_drawn(self):
        image = self.face(reading(), None)
        self.assertEqual(image.size, (WIDTH, HEIGHT))

    def test_a_reading_survives_a_failed_fetch(self):
        # The last value stays up while the network is down. It keeps
        # ageing and greys out, which is honest; replacing it with an
        # error card would throw away the only number there is.
        with_error = self.face(reading(), "NO CONNECTION")
        without = self.face(reading(), None)
        self.assertEqual(with_error.tobytes(), without.tobytes())

    def test_no_reading_shows_the_error(self):
        expected = self.renderer.render_message(
            "NO CONNECTION", detail="no reading yet"
        )
        self.assertEqual(self.face(None, "NO CONNECTION").tobytes(), expected.tobytes())

    def test_no_reading_and_no_error_is_still_waiting(self):
        # The gap between starting up and the first fetch landing. It is
        # not an error, and must not be drawn as one.
        expected = self.renderer.render_message("WAITING", detail="no reading yet")
        self.assertEqual(self.face(None, None).tobytes(), expected.tobytes())


class FormatAge(unittest.TestCase):
    def test_under_a_minute_reads_now(self):
        self.assertEqual(format_age(0.0), "now")
        self.assertEqual(format_age(0.9), "now")

    def test_minutes(self):
        self.assertEqual(format_age(1.0), "1m")
        self.assertEqual(format_age(7.9), "7m")
        self.assertEqual(format_age(59.9), "59m")

    def test_hours(self):
        self.assertEqual(format_age(60.0), "1h")
        self.assertEqual(format_age(90.0), "1h")
        self.assertEqual(format_age(60 * 23.9), "23h")

    def test_days(self):
        self.assertEqual(format_age(60 * 24), "1d")
        self.assertEqual(format_age(60 * 24 * 3.5), "3d")

    def test_the_age_rounds_down(self):
        # An age that rounded up would read as a minute older than it is,
        # and the stale threshold is judged against the same number.
        self.assertEqual(format_age(1.99), "1m")


if __name__ == "__main__":
    unittest.main()
