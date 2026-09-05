"""Status banding, the trend arrow's angle, and the age readout.

Whether the face is legible is a question for `tools/preview.py` and a
pair of eyes, and whether the palette survives colour blindness is one
for `tools/check_palette.py`. What can be asserted here is the part with
edges in it: which band a value falls in, and where the boundaries lie.

The face is the alert here -- haptics do not work on Quest 3 -- so a
threshold comparison being out by one boundary is the difference between
a low announcing itself and a low looking ordinary.

The arrow angle is here for the same reason: it is a mapping with
boundaries in it. `tests/test_librelink.py` asserts that the slope
behind it is right; what is asserted here is that the slope reaches the
drawing as the angle it should, and that a reading with no history to
fit still gets the API's arrow rather than none.
"""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from librelink import GlucosePoint, Reading

from renderer import (
    STATUS_MARKERS,
    TREND_ANGLES,
    Theme,
    TrendTuning,
    WatchFaceRenderer,
)

THEME = Theme()
TREND = TrendTuning()

format_age = WatchFaceRenderer._format_age


def reading(trend: int = 3, slope: float | None = None) -> Reading:
    """A reading whose history, if any, fits to `slope` mg/dL per minute."""
    taken_at = datetime.now(timezone.utc)
    history = ()
    if slope is not None:
        history = tuple(
            GlucosePoint(taken_at - timedelta(minutes=ago), 100.0 - slope * ago)
            for ago in range(20, -1, -1)
        )
    return Reading(
        value_mgdl=100.0,
        value_mmol=5.6,
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


class TrendAngle(unittest.TestCase):
    """Slope to arrow angle: 0 is level, +90 straight up.

    The five official buckets become a continuous scale here, so what
    has to hold is that the familiar angles still land where they always
    did -- otherwise a face that used to read at a glance now needs
    measuring.
    """

    def test_flat_is_level(self):
        self.assertEqual(TREND.angle_for_slope(0.0), 0.0)

    def test_the_flat_threshold_is_the_diagonal(self):
        # The boundary the API would have called "rising": same angle
        # here, so the scale is anchored to the arrows people know.
        self.assertAlmostEqual(TREND.angle_for_slope(TREND.flat_mgdl_min), 45.0)

    def test_the_fast_threshold_is_vertical(self):
        self.assertAlmostEqual(TREND.angle_for_slope(TREND.fast_mgdl_min), 90.0)

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

    def test_a_drift_below_flat_is_visible_but_shallow(self):
        # The whole point of fitting the slope: the API rounds this to a
        # level arrow, and a third of the way to the flat threshold
        # should look like a third of the way to the diagonal.
        self.assertAlmostEqual(TREND.angle_for_slope(0.33), 45.0 * 0.33)

    def test_between_the_thresholds_the_angle_slides(self):
        # Halfway from flat to fast is halfway from 45 to 90. Without
        # this the two thresholds would just be buckets again.
        midpoint = (TREND.flat_mgdl_min + TREND.fast_mgdl_min) / 2
        self.assertAlmostEqual(TREND.angle_for_slope(midpoint), 67.5)

    def test_the_angle_never_goes_backwards(self):
        # A faster rise must never draw a shallower arrow, at any of the
        # joins between the three segments of the mapping.
        angles = [TREND.angle_for_slope(i / 20) for i in range(0, 61)]
        self.assertEqual(angles, sorted(angles))

    def test_configured_thresholds_move_the_scale(self):
        # Someone who wants the arrow to react harder to a slow drift
        # lowers these, and the angles have to follow.
        tuning = TrendTuning(flat_mgdl_min=0.5, fast_mgdl_min=1.0)
        self.assertAlmostEqual(tuning.angle_for_slope(0.5), 45.0)
        # The same slope the default scale draws as a gentle 45 now
        # stands the arrow fully upright.
        self.assertAlmostEqual(tuning.angle_for_slope(1.0), 90.0)
        self.assertAlmostEqual(TREND.angle_for_slope(1.0), 45.0)


class TrendSource(unittest.TestCase):
    """Which of the two trends the drawing actually uses."""

    def setUp(self):
        self.renderer = WatchFaceRenderer()

    def test_a_fitted_slope_is_preferred(self):
        angle = self.renderer._trend_angle(reading(trend=3, slope=1.5))
        # trend=3 is the API calling it flat; the history says otherwise
        # and the history is what gets drawn.
        self.assertAlmostEqual(angle, 67.5)

    def test_no_history_falls_back_to_the_api_arrow(self):
        # A fresh sensor, or a gap in scanning. The arrow snaps back to
        # the five official positions rather than disappearing.
        for trend, expected in TREND_ANGLES.items():
            with self.subTest(trend=trend):
                self.assertEqual(self.renderer._trend_angle(reading(trend)), expected)

    def test_an_unknown_api_trend_leaves_no_arrow(self):
        # Unchanged behaviour: an unexpected TrendArrow value must not
        # take the process down, and no arrow beats a wrong one.
        self.assertIsNone(self.renderer._trend_angle(reading(trend=9)))

    def test_an_unknown_api_trend_is_irrelevant_once_it_can_fit(self):
        angle = self.renderer._trend_angle(reading(trend=9, slope=-2.0))
        self.assertEqual(angle, -90.0)


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
