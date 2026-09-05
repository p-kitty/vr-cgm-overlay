"""The reading itself: parsing its timestamp and presenting its value.

`_parse_factory_timestamp` is the one place this process reads a format
it does not control. It fails quietly when it fails at all -- a wrong
timezone or a swapped month still parses, and the result is a reading
whose age is hours out, which greys the face or fails to grey it. So the
cases are pinned here rather than left to be noticed in a headset.
"""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from librelink import LibreLinkError, Reading, _parse_factory_timestamp


def reading(mgdl=100.0, trend=3, age_min=0.0) -> Reading:
    return Reading(
        value_mgdl=mgdl,
        value_mmol=mgdl / 18.0,
        trend=trend,
        timestamp_utc=datetime.now(timezone.utc) - timedelta(minutes=age_min),
        is_high=False,
        is_low=False,
    )


class ParseFactoryTimestamp(unittest.TestCase):
    def test_twelve_hour_afternoon(self):
        got = _parse_factory_timestamp("9/4/2026 3:04:05 PM")
        self.assertEqual(got, datetime(2026, 9, 4, 15, 4, 5, tzinfo=timezone.utc))

    def test_midnight_is_hour_zero(self):
        # 12 AM is the hour that a naive %I mapping gets wrong, and being
        # twelve hours out is exactly enough to look like a stale sensor.
        got = _parse_factory_timestamp("9/4/2026 12:30:00 AM")
        self.assertEqual(got, datetime(2026, 9, 4, 0, 30, 0, tzinfo=timezone.utc))

    def test_noon_is_hour_twelve(self):
        got = _parse_factory_timestamp("9/4/2026 12:30:00 PM")
        self.assertEqual(got, datetime(2026, 9, 4, 12, 30, 0, tzinfo=timezone.utc))

    def test_month_comes_first(self):
        # 3/4 is March 4th, not the 3rd of April. Both parse, so only a
        # date where the two readings differ can tell them apart.
        got = _parse_factory_timestamp("3/4/2026 1:00:00 PM")
        self.assertEqual(got.month, 3)
        self.assertEqual(got.day, 4)

    def test_twenty_four_hour_fallback(self):
        got = _parse_factory_timestamp("9/4/2026 15:04:05")
        self.assertEqual(got, datetime(2026, 9, 4, 15, 4, 5, tzinfo=timezone.utc))

    def test_result_is_utc_aware(self):
        # The API sends no zone and means UTC. Attaching it is what lets
        # age_minutes subtract an aware now() without raising.
        got = _parse_factory_timestamp("9/4/2026 3:04:05 PM")
        self.assertEqual(got.tzinfo, timezone.utc)
        self.assertIsInstance(got - datetime.now(timezone.utc), timedelta)

    def test_an_unknown_shape_raises(self):
        # Better to fail loudly than to invent a time. A new format would
        # otherwise surface as a reading that is permanently stale.
        for raw in ("2026-09-04T15:04:05Z", "", "yesterday", "9/4/2026"):
            with self.subTest(raw=raw):
                with self.assertRaises(LibreLinkError):
                    _parse_factory_timestamp(raw)


class ReadingDisplay(unittest.TestCase):
    def test_mgdl_is_whole_numbers(self):
        self.assertEqual(reading(mgdl=112.4).display_value("mgdl"), "112")

    def test_mmol_keeps_one_decimal(self):
        # 6.2 rather than 6, which is the whole reason for the unit split.
        self.assertEqual(reading(mgdl=112.0).display_value("mmol"), "6.2")

    def test_unknown_unit_falls_back_to_mgdl(self):
        self.assertEqual(reading(mgdl=112.0).display_value("stones"), "112")

    def test_arrow_for_each_trend(self):
        self.assertEqual(reading(trend=1).arrow, "↓")
        self.assertEqual(reading(trend=3).arrow, "→")
        self.assertEqual(reading(trend=5).arrow, "↑")

    def test_unknown_trend_has_no_arrow(self):
        # An unexpected TrendArrow value must not take the process down;
        # the number is what matters and the arrow is supplementary.
        self.assertEqual(reading(trend=9).arrow, "")


class ReadingAge(unittest.TestCase):
    def test_age_against_a_given_now(self):
        stamp = datetime(2026, 9, 4, 12, 0, 0, tzinfo=timezone.utc)
        entry = Reading(100.0, 5.6, 3, stamp, False, False)
        now = stamp + timedelta(minutes=7, seconds=30)
        self.assertAlmostEqual(entry.age_minutes(now), 7.5)

    def test_age_defaults_to_the_current_time(self):
        self.assertAlmostEqual(reading(age_min=3.0).age_minutes(), 3.0, places=1)

    def test_a_future_timestamp_reads_negative(self):
        # Clock skew between the phone and here can put a reading slightly
        # ahead. It should not read as ancient, which a wrapped or absolute
        # value would.
        self.assertLess(reading(age_min=-2.0).age_minutes(), 0)


if __name__ == "__main__":
    unittest.main()
