"""The reading itself: its timestamp, its value, and its trend.

`_parse_factory_timestamp` is the one place this process reads a format
it does not control. It fails quietly when it fails at all -- a wrong
timezone or a swapped month still parses, and the result is a reading
whose age is hours out, which greys the face or fails to grey it. So the
cases are pinned here rather than left to be noticed in a headset.

The trend fit is the other half. It replaces a number Abbott computed
with one computed here, so being wrong is now this project's problem:
too eager and a flat reading grows an arrow, too slow and a real fall
looks level. Both are decided by `fit_slope`, and both are asserted
below against series with a known answer.

What none of this can check is the shape `graphData` actually arrives
in -- only `python src/main.py --dry-run` sees that.
"""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from librelink import (
    GRAPH_RESOLUTION_MIN,
    MIN_FIT_POINTS,
    MIN_FIT_SPAN_MIN,
    GlucosePoint,
    LibreLinkError,
    Reading,
    _parse_factory_timestamp,
    _parse_graph_data,
    fit_slope,
)

# A fixed instant, so a series can be written down and its slope known.
BASE = datetime(2026, 9, 4, 12, 0, 0, tzinfo=timezone.utc)


def reading(mgdl=100.0, trend=3, age_min=0.0, slope=None) -> Reading:
    """A reading; with `slope` set it carries history that fits to it.

    Without one the history is empty, which is too little to fit -- so
    `trend` is what the arrow falls back to, as on a fresh sensor.
    """
    taken_at = datetime.now(timezone.utc) - timedelta(minutes=age_min)
    return Reading(
        value_mgdl=mgdl,
        trend=trend,
        timestamp_utc=taken_at,
        is_high=False,
        is_low=False,
        history=() if slope is None else series(slope, ends_at=taken_at),
    )


def series(
    slope: float,
    minutes: float = 20.0,
    *,
    ends_at: datetime = BASE,
    end_mgdl: float = 100.0,
    step: float = 1.0,
) -> tuple[GlucosePoint, ...]:
    """A straight run of samples `step` minutes apart, ending at `ends_at`.

    `slope` is in mg/dL per minute, which is what the fit returns.

    `step` defaults to a minute because that is the convenient shape for
    asserting the arithmetic, NOT because it is what arrives: the API
    downsamples to a point every GRAPH_RESOLUTION_MIN. RealisticSpacing
    below is the case that uses the spacing the API actually sends.
    """
    return tuple(
        GlucosePoint(
            ends_at - timedelta(minutes=ago * step),
            end_mgdl - slope * ago * step,
        )
        for ago in range(int(minutes / step), -1, -1)
    )


def graph_entry(at: datetime, mgdl: float) -> dict:
    """One graphData element, in the shape the API sends."""
    return {
        "FactoryTimestamp": at.strftime("%m/%d/%Y %I:%M:%S %p"),
        "ValueInMgPerDl": mgdl,
        "Value": mgdl / 18.0,
    }


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

    def test_mmol_is_derived_from_mgdl(self):
        # The API's own Value field is in the account's display unit,
        # which follows its country, so a mg/dL account sends the mg/dL
        # number there. Reading it back drew 112 where 6.2 belonged --
        # a number no arm has ever produced. Only ValueInMgPerDl is
        # guaranteed, so mmol/L has to be computed from it.
        self.assertAlmostEqual(reading(mgdl=112.0).value_mmol, 112.0 / 18.0)

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


class FitSlope(unittest.TestCase):
    """The number the arrow is now drawn from."""

    def test_a_straight_run_recovers_its_slope(self):
        self.assertAlmostEqual(fit_slope(series(1.5), 15.0), 1.5, places=6)

    def test_a_flat_run_is_zero(self):
        self.assertAlmostEqual(fit_slope(series(0.0), 15.0), 0.0, places=6)

    def test_a_fall_is_negative(self):
        # Sign is the whole message. A dropping arm drawn pointing up
        # would be worse than no arrow at all.
        self.assertAlmostEqual(fit_slope(series(-2.0), 15.0), -2.0, places=6)

    def test_noise_does_not_swing_the_fit(self):
        # Why a line and not the difference of the last two samples: the
        # jitter here is +/-3 mg/dL on a flat arm, so the last two points
        # sit 6 mg/dL apart and subtracting them claims 6 mg/dL/min --
        # three times what stands the arrow fully upright.
        points = tuple(
            GlucosePoint(p.at, p.mgdl + (3.0 if i % 2 else -3.0))
            for i, p in enumerate(series(0.0))
        )
        self.assertLess(abs(fit_slope(points, 15.0)), 0.2)

    def test_only_the_window_counts(self):
        # A climb that finished half an hour ago is not the trend now.
        old_climb = series(4.0, 20.0, ends_at=BASE - timedelta(minutes=20))
        recent = series(0.0, 15.0, ends_at=BASE, end_mgdl=180.0)
        self.assertAlmostEqual(fit_slope(old_climb + recent, 15.0), 0.0, places=6)

    def test_a_longer_window_reaches_further_back(self):
        # The same points, a wider window: the earlier climb is in scope
        # now, so the answer moves. This is what window_min tunes.
        old_climb = series(4.0, 20.0, ends_at=BASE - timedelta(minutes=20))
        recent = series(0.0, 15.0, ends_at=BASE, end_mgdl=180.0)
        self.assertGreater(fit_slope(old_climb + recent, 60.0), 1.0)

    def test_too_few_points_will_not_fit(self):
        # A fresh sensor. Better to hand the arrow back to the API than
        # to draw a line through two samples and call it a trend.
        thin = series(2.0, 10.0, step=10.0)
        self.assertEqual(len(thin), MIN_FIT_POINTS - 1)
        self.assertIsNone(fit_slope(thin, 15.0))

    def test_too_short_a_span_will_not_fit(self):
        # Plenty of points, but all inside a couple of minutes: the
        # sensor's own noise dominates anything measured over that.
        crowded = series(2.0, MIN_FIT_SPAN_MIN - 1.0, step=0.5)
        self.assertGreaterEqual(len(crowded), MIN_FIT_POINTS)
        self.assertIsNone(fit_slope(crowded, 15.0))

    def test_an_empty_history_will_not_fit(self):
        self.assertIsNone(fit_slope((), 15.0))

    def test_points_sharing_one_instant_will_not_fit(self):
        # No spread in time at all, which is a division by zero rather
        # than a wrong answer. It must not take the draw loop down.
        stacked = tuple(GlucosePoint(BASE, 100.0 + i) for i in range(5))
        self.assertIsNone(fit_slope(stacked, 15.0))

    def test_the_window_ends_at_the_newest_point_by_default(self):
        # Not at the wall clock: these samples are dated 2026 and would
        # otherwise all fall outside any window.
        self.assertAlmostEqual(fit_slope(series(1.0), 15.0), 1.0, places=6)


class RealisticSpacing(unittest.TestCase):
    """The fit against the spacing the API actually sends.

    A dry run against the live service returned 48 points over 11.9
    hours -- a point every 15 minutes, not the once a minute the sensor
    records. The first default shipped here was a 15 minute window,
    which put exactly one point inside it and could never fit; the arrow
    silently used TrendArrow for every reading. These pin the spacing so
    that cannot come back unnoticed.
    """

    # The measured median gap, not the nominal 15. It matters: at an
    # exact 15.0 the point on a 30 minute boundary lands just inside the
    # window and a 30 minute window looks like it fits. Real gaps are
    # never exact, so that apparent fit is an artefact -- which is the
    # margin the floor at three gaps exists to keep.
    MEASURED_GAP_MIN = 15.05

    def coarse(self, slope: float, step: float | None = None):
        return series(
            slope,
            12 * 60.0,
            step=step or self.MEASURED_GAP_MIN,
            end_mgdl=140.0,
        )

    def test_a_window_of_one_gap_cannot_fit(self):
        # The bug that shipped: one point lands inside, so there is
        # nothing to fit and every reading fell back to TrendArrow.
        self.assertIsNone(fit_slope(self.coarse(1.0), GRAPH_RESOLUTION_MIN))

    def test_a_window_of_two_gaps_cannot_fit_either(self):
        self.assertIsNone(fit_slope(self.coarse(1.0), 2 * GRAPH_RESOLUTION_MIN))

    def test_the_configured_floor_is_the_shortest_window_that_fits(self):
        floor = MIN_FIT_POINTS * GRAPH_RESOLUTION_MIN
        self.assertAlmostEqual(fit_slope(self.coarse(1.0), floor), 1.0, places=6)

    def test_the_default_window_fits_with_room_to_spare(self):
        # An hour holds four points, so one missing sample still leaves
        # enough to fit rather than dropping to the API arrow.
        self.assertAlmostEqual(fit_slope(self.coarse(-0.5), 60.0), -0.5, places=6)

    def test_the_longest_observed_gap_still_fits_the_default_window(self):
        # Gaps ran 15.02 to 21 minutes. Three of the longest still land
        # inside an hour, which is what makes 60 a safe default and 45 a
        # floor rather than a recommendation.
        self.assertAlmostEqual(
            fit_slope(self.coarse(1.0, step=21.0), 60.0), 1.0, places=6
        )


class ReadingTrend(unittest.TestCase):
    def test_the_slope_comes_from_the_history(self):
        entry = reading(slope=1.2)
        self.assertAlmostEqual(entry.slope_mgdl_per_min(15.0), 1.2, places=6)

    def test_no_history_means_no_slope(self):
        # Which is what sends the renderer back to the API's TrendArrow.
        self.assertIsNone(reading().slope_mgdl_per_min(15.0))

    def test_the_window_is_anchored_on_the_reading_not_now(self):
        # A stale reading's trend describes the moment it was taken. Were
        # the window to end at the wall clock instead, an ageing reading
        # would watch its own arrow fall to flat as its points dropped
        # out the back of the window -- a second, quieter staleness
        # signal competing with the grey one.
        taken_at = datetime.now(timezone.utc) - timedelta(hours=3)
        entry = Reading(
            value_mgdl=100.0,
            trend=3,
            timestamp_utc=taken_at,
            is_high=False,
            is_low=False,
            history=series(1.7, ends_at=taken_at),
        )
        self.assertAlmostEqual(entry.slope_mgdl_per_min(15.0), 1.7, places=6)


class ParseGraphData(unittest.TestCase):
    """Turning the discarded half of the response into a series."""

    def test_the_current_measurement_is_folded_in(self):
        # graphData stops short of it, and the trend has to be anchored
        # on the newest value there is.
        latest = GlucosePoint(BASE, 140.0)
        got = _parse_graph_data(
            [graph_entry(BASE - timedelta(minutes=5), 120.0)], latest
        )
        self.assertEqual(got[-1], latest)

    def test_points_come_out_oldest_first(self):
        latest = GlucosePoint(BASE, 100.0)
        entries = [
            graph_entry(BASE - timedelta(minutes=5), 120.0),
            graph_entry(BASE - timedelta(minutes=15), 110.0),
            graph_entry(BASE - timedelta(minutes=10), 115.0),
        ]
        got = _parse_graph_data(entries, latest)
        self.assertEqual(list(got), sorted(got))

    def test_unparseable_entries_are_dropped_not_fatal(self):
        # The number is what the request was made for; the series is
        # supplementary. If Abbott changes the shape of graphData this
        # has to degrade to the API arrow, not to no reading at all.
        latest = GlucosePoint(BASE, 100.0)
        entries = [
            graph_entry(BASE - timedelta(minutes=5), 120.0),
            {"FactoryTimestamp": "yesterday", "ValueInMgPerDl": 90.0},
            {"ValueInMgPerDl": 90.0},
            {"FactoryTimestamp": "9/4/2026 11:50:00 AM"},
            {"FactoryTimestamp": "9/4/2026 11:51:00 AM", "ValueInMgPerDl": "n/a"},
            None,
        ]
        got = _parse_graph_data(entries, latest)
        self.assertEqual(len(got), 2)

    def test_an_absent_series_still_yields_the_reading(self):
        latest = GlucosePoint(BASE, 100.0)
        self.assertEqual(_parse_graph_data(None, latest), (latest,))
        self.assertEqual(_parse_graph_data([], latest), (latest,))

    def test_the_current_measurement_wins_a_shared_timestamp(self):
        # The same sample can appear in both halves of the response. It
        # is one measurement, so it has to become one point.
        latest = GlucosePoint(BASE, 140.0)
        got = _parse_graph_data([graph_entry(BASE, 120.0)], latest)
        self.assertEqual(got, (latest,))

    def test_a_parsed_series_fits(self):
        # End to end: what the API sends comes back as something the
        # trend can actually be computed from.
        latest = GlucosePoint(BASE, 100.0)
        entries = [
            graph_entry(BASE - timedelta(minutes=m), 100.0 - m) for m in range(1, 16)
        ]
        got = _parse_graph_data(entries, latest)
        self.assertAlmostEqual(fit_slope(got, 15.0), 1.0, places=6)


class ReadingAge(unittest.TestCase):
    def test_age_against_a_given_now(self):
        stamp = datetime(2026, 9, 4, 12, 0, 0, tzinfo=timezone.utc)
        entry = Reading(100.0, 3, stamp, False, False)
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
