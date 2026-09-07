"""The history sparkline: what it shows, what it refuses to join up.

Whether the graph is legible is a question for `tools/preview.py` and a
pair of eyes. What can be asserted here is the part with edges in it:
which points fall inside the window, where the line is allowed to break,
and that a value past the end of the axis is pulled back onto it rather
than drawn off the card.

The break across gaps is the one that matters most. A line joined over a
stretch the sensor was not scanning is not a cosmetic problem: it draws
measurements that were never taken, on a face whose whole claim is that
what it shows is real.
"""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from PIL import Image, ImageDraw

from cgm.core.librelink import GRAPH_RESOLUTION_MIN, GlucosePoint, Reading

from cgm.face.graph import (
    AXIS_FLOOR_MGDL,
    AXIS_STEP_MGDL,
    GRID_COLOR,
    HEAD_RADIUS,
    LAST_GAP_MIN,
    MAX_GAP_MIN,
    MAX_TIME_TICKS,
    TRACE_COLOR,
    GraphTuning,
    axis_top,
    draw_sparkline,
    edge_point,
    format_value,
    recent,
    segments,
    time_ticks,
)
from cgm.face.renderer import GRAPH_HEIGHT, HEIGHT, WIDTH, Theme, WatchFaceRenderer

NOW = datetime(2026, 9, 7, 12, 0, tzinfo=timezone.utc)
THEME = Theme()
TUNING = GraphTuning()


def series(*offsets_and_values) -> tuple[GlucosePoint, ...]:
    """Points given as (minutes before NOW, mg/dL), oldest first."""
    return tuple(
        GlucosePoint(NOW - timedelta(minutes=ago), mgdl)
        for ago, mgdl in sorted(offsets_and_values, reverse=True)
    )


class GapThreshold(unittest.TestCase):
    def test_the_gap_matches_the_resolution_it_is_derived_from(self):
        # cgm.face imports nothing from cgm.core, so the API's sample
        # resolution is written out a second time in cgm.face.graph.
        # This is the seam that would otherwise drift silently: if the
        # API's spacing ever changes, a threshold left behind would
        # start breaking the line between every ordinary pair of points.
        self.assertEqual(MAX_GAP_MIN, 2 * GRAPH_RESOLUTION_MIN)


class Windowing(unittest.TestCase):
    def test_points_inside_the_window_are_kept(self):
        points = series((30, 100), (15, 105), (0, 110))
        self.assertEqual(len(recent(points, 60, NOW)), 3)

    def test_points_older_than_the_window_are_dropped(self):
        points = series((200, 90), (30, 100), (0, 110))
        kept = recent(points, 60, NOW)
        self.assertEqual([p.mgdl for p in kept], [100, 110])

    def test_the_window_edge_itself_is_inside(self):
        points = series((60, 90), (61, 88))
        kept = recent(points, 60, NOW)
        self.assertEqual([p.mgdl for p in kept], [90])

    def test_points_after_the_anchor_are_dropped(self):
        # The anchor is the reading's own timestamp, so anything newer
        # than it is not part of the stretch it describes.
        points = series((10, 100), (-10, 130))
        self.assertEqual([p.mgdl for p in recent(points, 60, NOW)], [100])

    def test_an_empty_series_windows_to_nothing(self):
        self.assertEqual(recent((), 180, NOW), [])

    def test_a_window_of_zero_keeps_everything(self):
        # "All of it" -- the twelve hours the response carried, rather
        # than a length. This is what `graph.window_min = 0` asks for.
        points = series((700, 88), (400, 92), (200, 100), (0, 110))
        self.assertEqual(len(recent(points, 0, NOW)), 4)

    def test_all_of_it_still_stops_at_the_anchor(self):
        # However far back it reaches, nothing newer than the reading is
        # part of the stretch that reading describes.
        points = series((400, 92), (0, 110), (-30, 140))
        self.assertEqual([p.mgdl for p in recent(points, 0, NOW)], [92, 110])


class Axis(unittest.TestCase):
    """The bottom never moves; the top grows rather than clip."""

    def test_an_ordinary_day_leaves_the_axis_where_it_was_configured(self):
        # The whole reason the axis is not fitted to the data: a quiet
        # stretch has to stay a flat line rather than fill the plot.
        points = series((60, 88), (30, 104), (0, 121))
        self.assertEqual(axis_top(points, TUNING), TUNING.axis_high_mgdl)

    def test_a_reading_at_the_top_does_not_move_it(self):
        points = series((30, 100), (0, TUNING.axis_high_mgdl))
        self.assertEqual(axis_top(points, TUNING), TUNING.axis_high_mgdl)

    def test_a_hyper_grows_the_axis_to_hold_it(self):
        points = series((30, 288), (0, 372))
        grown = axis_top(points, TUNING)
        self.assertGreaterEqual(grown, 372)
        self.assertLess(grown, 372 + AXIS_STEP_MGDL)

    def test_the_grown_axis_lands_on_a_round_number(self):
        # An axis whose top is whatever the highest sample happened to
        # be is a number, not a scale.
        for peak in (301, 349, 350, 351, 480):
            with self.subTest(peak=peak):
                grown = axis_top(series((0, peak)), TUNING)
                self.assertEqual(grown % AXIS_STEP_MGDL, 0)

    def test_nothing_to_draw_leaves_the_configured_axis(self):
        self.assertEqual(axis_top([], TUNING), TUNING.axis_high_mgdl)

    def test_the_floor_is_not_a_minimum_but_a_floor(self):
        # Readings under it are clamped onto it rather than lowering it.
        # The digits above say how low a low actually went.
        self.assertEqual(axis_top(series((0, 42)), TUNING), TUNING.axis_high_mgdl)

    def test_the_floor_is_not_settable(self):
        # It is a constant on purpose: every graph of this starts in the
        # same place, so two of them are comparable and the eye learns
        # where the bottom is once.
        self.assertEqual(AXIS_FLOOR_MGDL, 50.0)
        self.assertFalse(hasattr(TUNING, "axis_low_mgdl"))


class Formatting(unittest.TestCase):
    def test_a_level_label_matches_what_the_digits_would_say(self):
        # format_value is a copy of Reading.display_value, because
        # cgm.face may not import cgm.core. A graph labelled 240 beside
        # digits reading 13.3 would be two units on one card.
        for unit in ("mgdl", "mmol"):
            for mgdl in (40.0, 70.0, 103.0, 180.0, 240.0, 300.0):
                with self.subTest(unit=unit, mgdl=mgdl):
                    same = Reading(
                        value_mgdl=mgdl,
                        trend=3,
                        timestamp_utc=NOW,
                        is_high=False,
                        is_low=False,
                    )
                    self.assertEqual(
                        format_value(mgdl, unit), same.display_value(unit)
                    )


class TimeAxis(unittest.TestCase):
    def spans(self, hours: float):
        return NOW - timedelta(hours=hours), NOW

    def test_the_labels_stay_few_enough_to_read(self):
        for hours in (0.5, 1, 3, 6, 12, 24):
            with self.subTest(hours=hours):
                ticks = time_ticks(*self.spans(hours))
                self.assertLessEqual(len(ticks), MAX_TIME_TICKS + 1)

    def test_every_tick_is_inside_the_span(self):
        start, end = self.spans(12)
        for tick in time_ticks(start, end):
            with self.subTest(tick=tick):
                moment = tick.astimezone(timezone.utc)
                self.assertGreaterEqual(moment, start)
                self.assertLessEqual(moment, end)

    def test_a_longer_span_steps_more_coarsely(self):
        def step(hours):
            ticks = time_ticks(*self.spans(hours))
            return (ticks[1] - ticks[0]) if len(ticks) > 1 else None

        self.assertLess(step(3), step(12))

    def test_the_ticks_land_on_round_times(self):
        # They are there to be compared against a clock, so they have to
        # be times someone would read off one.
        for tick in time_ticks(*self.spans(12)):
            with self.subTest(tick=tick):
                self.assertEqual((tick.minute, tick.second), (0, 0))

    def test_no_span_has_no_ticks(self):
        self.assertEqual(time_ticks(NOW, NOW), [])


class WindowEdge(unittest.TestCase):
    """The trace is clipped to the left of the plot, not started late.

    A fixed window begins a round number of minutes ago; the samples
    arrive on a fifteen minute grid with no reason to line up with it.
    The segment crossing that boundary was measured, so the point where
    it crosses is worked out rather than the line simply beginning at
    the first sample inside.
    """

    def start(self, minutes: float):
        return NOW - timedelta(minutes=minutes)

    def test_the_crossing_is_interpolated(self):
        # 60 at four hours back, 100 at two: at three, halfway, 80.
        points = series((240, 60.0), (120, 100.0))
        crossing = edge_point(points, self.start(180), max_gap_min=1e9)
        self.assertEqual(crossing.at, self.start(180))
        self.assertAlmostEqual(crossing.mgdl, 80.0)

    def test_it_lands_exactly_on_the_edge(self):
        points = series((20, 90.0), (5, 120.0))
        crossing = edge_point(points, self.start(10))
        self.assertEqual(crossing.at, self.start(10))

    def test_history_that_does_not_reach_back_is_left_alone(self):
        # A fresh sensor really has nothing there. Inventing a point
        # would draw a reading that was never taken.
        points = series((30, 100.0), (0, 110.0))
        self.assertIsNone(edge_point(points, self.start(480)))

    def test_a_gap_on_the_boundary_is_still_a_gap(self):
        # Two samples an hour apart, with the window edge between them.
        # Crossing them would join what the break rule exists to split.
        points = series((90, 100.0), (20, 110.0))
        self.assertIsNone(edge_point(points, self.start(60)))

    def test_nothing_inside_the_window_crosses_nothing(self):
        points = series((600, 100.0), (500, 110.0))
        self.assertIsNone(edge_point(points, self.start(480)))

    def test_the_drawn_trace_reaches_the_left_edge(self):
        # The whole point, at the level it is visible: with history
        # older than the window, the first plotted x is the plot's left.
        image = Image.new("RGBA", (220, 120), (0, 0, 0, 255))
        box = (10.0, 10.0, 210.0, 110.0)
        points = series(*[(m, 100.0 + m % 7) for m in range(0, 700, 15)])
        draw_sparkline(
            ImageDraw.Draw(image),
            box,
            points,
            tuning=TUNING,
            theme=THEME,
            now=NOW,
            accent=(255, 0, 255),
        )
        drawn = [
            x
            for x in range(image.width)
            for y in range(image.height)
            if image.getpixel((x, y))[:3] == TRACE_COLOR
        ]
        self.assertTrue(drawn, "nothing was drawn")
        self.assertLessEqual(min(drawn), box[0] + 1)


class Segments(unittest.TestCase):
    def test_an_unbroken_series_is_one_run(self):
        points = series((30, 100), (15, 105), (0, 110))
        self.assertEqual(len(segments(points)), 1)

    def test_a_long_gap_breaks_the_line(self):
        points = series((120, 100), (15, 105), (0, 110))
        runs = segments(points)
        self.assertEqual([[p.mgdl for p in run] for run in runs], [[100], [105, 110]])

    def test_the_threshold_itself_still_joins(self):
        # Exactly two samples apart is a sample that did not arrive, not
        # a stretch of missing data. Breaking here would put a gap in
        # every series that dropped one point.
        points = series((MAX_GAP_MIN, 100), (0, 110))
        self.assertEqual(len(segments(points)), 1)

    def test_just_past_the_threshold_breaks(self):
        # Three points, so the break is an interior one. A break in
        # front of the newest point is the publication lag and is
        # treated separately -- see PublicationLag below.
        points = series(
            (MAX_GAP_MIN + 71, 100), (MAX_GAP_MIN + 1, 104), (15, 108), (0, 110)
        )
        self.assertEqual(len(segments(points)), 2)

    def test_every_point_survives_the_split(self):
        points = series((300, 90), (285, 95), (120, 100), (15, 105), (0, 110))
        flattened = [p for run in segments(points) for p in run]
        self.assertEqual(flattened, list(points))

    def test_nothing_splits_into_nothing(self):
        self.assertEqual(segments([]), [])


class PublicationLag(unittest.TestCase):
    """The gap in front of the newest point is not a scanning gap.

    One response carries both the current measurement and the history,
    and they are not equally fresh: graphData's newest entry trails
    glucoseMeasurement by a quarter of an hour or more. The reading at
    the end of that gap exists, which is the proof the sensor was
    reading throughout it, so the line is joined across -- up to a
    bound, because a phone that really stopped scanning looks the same
    from here and joining across an afternoon would draw one.
    """

    def test_the_newest_point_is_joined_across_the_lag(self):
        # The last gap is 40 minutes, which breaks the line anywhere else.
        points = series((55, 100), (40, 104), (0, 112))
        self.assertEqual(len(segments(points)), 1)

    def test_a_gap_past_the_bound_leaves_it_stranded(self):
        points = series((LAST_GAP_MIN + 16, 100), (LAST_GAP_MIN + 1, 104), (0, 112))
        runs = segments(points)
        self.assertEqual([[p.mgdl for p in run] for run in runs], [[100, 104], [112]])

    def test_the_bound_itself_still_joins(self):
        points = series((LAST_GAP_MIN + 15, 100), (LAST_GAP_MIN, 104), (0, 112))
        self.assertEqual(len(segments(points)), 1)

    def test_an_interior_gap_is_still_a_gap(self):
        # Only the newest point gets the exception. A hole in the middle
        # of the night is missing data however fresh the reading is.
        points = series((240, 96), (225, 99), (30, 104), (0, 112))
        runs = segments(points)
        self.assertEqual([[p.mgdl for p in run] for run in runs], [[96, 99], [104, 112]])

    def test_the_exception_can_be_switched_off(self):
        points = series((55, 100), (40, 104), (0, 112))
        self.assertEqual(len(segments(points, last_gap_min=0)), 2)


class CanvasSize(unittest.TestCase):
    def test_no_graph_is_the_face_that_always_shipped(self):
        # The overlay's default. This is a regression guard as much as a
        # test: the graph was added by growing the card, and the frontend
        # that did not ask for one must be untouched by that.
        renderer = WatchFaceRenderer()
        self.assertEqual((renderer.width, renderer.height), (WIDTH, HEIGHT))
        self.assertEqual(renderer.render(_reading()).size, (WIDTH, HEIGHT))

    def test_a_graph_grows_the_card_downwards(self):
        renderer = WatchFaceRenderer(graph=TUNING)
        self.assertEqual(renderer.height, HEIGHT + GRAPH_HEIGHT)
        self.assertEqual(
            renderer.render(_reading()).size, (renderer.width, renderer.height)
        )

    def test_a_face_drawn_at_a_given_instant_is_reproducible(self):
        # tools/preview.py commits a PNG, so the card has to be able to
        # come out the same twice. Everything on it that moves -- the
        # age, whether it has gone stale, the times under the graph --
        # comes off the `now` handed in.
        renderer = WatchFaceRenderer(graph=TUNING)
        first = renderer.render(_reading(), now=NOW).tobytes()
        second = renderer.render(_reading(), now=NOW).tobytes()
        self.assertEqual(first, second)

    def test_the_instant_is_what_decides_the_age(self):
        renderer = WatchFaceRenderer()
        fresh = renderer.render(_reading(), now=NOW)
        later = renderer.render(_reading(), now=NOW + timedelta(hours=3))
        self.assertNotEqual(fresh.tobytes(), later.tobytes())

    def test_a_message_card_is_the_same_size_as_a_reading(self):
        # Both frontends size themselves from the image. A message card
        # of a different height would resize the window, or reallocate
        # the overlay texture, every time a fetch failed.
        for renderer in (WatchFaceRenderer(), WatchFaceRenderer(graph=TUNING)):
            with self.subTest(graph=renderer.graph is not None):
                self.assertEqual(
                    renderer.render_message("NO CONNECTION").size,
                    renderer.render(_reading()).size,
                )


class Drawing(unittest.TestCase):
    """Pixels, on a bare canvas rather than through the whole face."""

    def setUp(self):
        self.box = (10.0, 10.0, 210.0, 110.0)
        self.image = Image.new("RGBA", (220, 120), (0, 0, 0, 255))
        self.draw = ImageDraw.Draw(self.image)

    def sparkline(self, points, accent=(255, 0, 255)) -> None:
        """Draw onto a fresh canvas, so a test may call this twice."""
        self.setUp()
        draw_sparkline(
            self.draw,
            self.box,
            points,
            tuning=TUNING,
            theme=THEME,
            now=NOW,
            accent=accent,
        )

    def coloured(self, color) -> list[tuple[int, int]]:
        """Every pixel of one colour, so a shape can be located."""
        return [
            (x, y)
            for x in range(self.image.width)
            for y in range(self.image.height)
            if self.image.getpixel((x, y))[:3] == color
        ]

    def test_the_band_is_drawn_with_no_history_at_all(self):
        # An empty strip with the range still marked reads as a graph
        # with no data in it. An entirely blank one reads as broken.
        self.sparkline(())
        self.assertTrue(any(p != (0, 0, 0) for p in self._column(110)))

    def test_the_newest_point_is_drawn_in_the_status_colour(self):
        self.sparkline(series((30, 100), (15, 105), (0, 110)))
        head = self.coloured((255, 0, 255))
        self.assertTrue(head, "the newest point was not marked")
        # At the right-hand edge, because the window ends on it.
        self.assertGreaterEqual(max(x for x, _ in head), self.box[2] - HEAD_RADIUS - 1)

    def test_nothing_is_drawn_outside_the_box(self):
        # A reading past the top of the axis is clamped onto it, so the
        # trace rides the edge instead of being painted over the age
        # readout above or the marker below.
        self.sparkline(series((30, 400), (15, 500), (0, 450)))
        for x, y in self.coloured(TRACE_COLOR) + self.coloured((255, 0, 255)):
            with self.subTest(pixel=(x, y)):
                self.assertGreaterEqual(y, self.box[1] - 1)
                self.assertLessEqual(y, self.box[3] + 1)

    def test_a_high_reading_sits_above_a_low_one(self):
        self.sparkline(series((30, 90), (15, 95), (0, 100)))
        low = min(y for _, y in self.coloured((255, 0, 255)))
        self.sparkline(series((30, 190), (15, 195), (0, 200)))
        high = min(y for _, y in self.coloured((255, 0, 255)))
        # Screen Y grows downwards, so higher glucose is a smaller y.
        self.assertLess(high, low)

    def test_each_threshold_is_drawn_in_the_colour_it_turns_the_face(self):
        # One line each, in its own status colour, so the line and the
        # card cannot disagree about which end of the scale it marks.
        self.sparkline(())
        low = self.coloured(THEME.color_low)
        very_high = self.coloured(THEME.color_very_high)
        self.assertTrue(low, "the low threshold has no line")
        self.assertTrue(very_high, "the very high threshold has no line")
        # Screen Y grows downwards, so the low line is the lower one.
        self.assertGreater(
            min(y for _, y in low), max(y for _, y in very_high)
        )

    def test_the_floor_gets_a_line_of_its_own(self):
        # It says where the scale starts. Quiet, because that is worth
        # being able to see and not worth noticing.
        self.sparkline(())
        floor = self.coloured(GRID_COLOR)
        self.assertTrue(floor, "the floor has no line")
        # Below both thresholds, because it is the bottom of the axis.
        self.assertGreater(
            min(y for _, y in floor),
            max(y for _, y in self.coloured(THEME.color_low)),
        )

    def test_the_floor_is_solid_and_a_hairline(self):
        # The weight is the difference between a ruled line and a level
        # that means something: one row of pixels, unbroken, where the
        # thresholds are two and dashed.
        self.sparkline(())
        floor = self.coloured(GRID_COLOR)
        rows = {y for _, y in floor}
        self.assertEqual(len(rows), 1, f"the gridline is {len(rows)} rows deep")
        xs = sorted(x for x, _ in floor)
        self.assertTrue(
            all(b - a == 1 for a, b in zip(xs, xs[1:])),
            "the gridline came out dashed",
        )

    def test_the_threshold_lines_are_dashed_and_not_solid(self):
        self.sparkline(())
        row = min(y for _, y in self.coloured(THEME.color_low))
        xs = [x for x, y in self.coloured(THEME.color_low) if y == row]
        # A solid rule would run without a break from one end to the
        # other; a dashed one leaves gaps in the middle of its own row.
        self.assertTrue(
            any(b - a > 1 for a, b in zip(xs, xs[1:])),
            "the threshold line came out solid",
        )

    def test_a_single_point_still_leaves_a_mark(self):
        # A run of one cannot be a line. It has to be drawn as a dot or
        # a fresh sensor's first measurement would show as nothing.
        self.sparkline(series((0, 110)))
        self.assertTrue(self.coloured((255, 0, 255)))

    def _column(self, x: int):
        return [self.image.getpixel((x, y))[:3] for y in range(self.image.height)]


def _reading(mgdl: float = 110.0) -> Reading:
    return Reading(
        value_mgdl=mgdl,
        trend=3,
        timestamp_utc=NOW,
        is_high=False,
        is_low=False,
        history=series((30, 100), (15, 105), (0, mgdl)),
    )


if __name__ == "__main__":
    unittest.main()
