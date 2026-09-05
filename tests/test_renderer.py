"""Colour and the age readout.

Whether the face is legible is a question for `tools/preview.py` and a
pair of eyes. What can be asserted is the part with edges in it: which
colour a value maps to, and where the boundaries fall.

Colour is the alert here -- haptics do not work on Quest 3 -- so a
threshold comparison being out by one boundary is the difference between
a low announcing itself and a low looking ordinary.
"""

from __future__ import annotations

import unittest

from renderer import Theme, WatchFaceRenderer

THEME = Theme()

format_age = WatchFaceRenderer._format_age


class StatusColor(unittest.TestCase):
    def test_a_value_in_range_is_green(self):
        self.assertEqual(THEME.status_color(100.0), THEME.color_in_range)

    def test_below_low_is_red(self):
        self.assertEqual(THEME.status_color(69.0), THEME.color_low)

    def test_above_high_is_yellow(self):
        self.assertEqual(THEME.status_color(200.0), THEME.color_high)

    def test_above_very_high_is_orange(self):
        # Split from yellow so drifting over range and being far over it
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
