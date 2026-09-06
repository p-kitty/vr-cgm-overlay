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
    HEAD_RADIUS,
    MAX_GAP_MIN,
    TRACE_COLOR,
    GraphTuning,
    draw_sparkline,
    recent,
    segments,
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
        points = series((MAX_GAP_MIN + 1, 100), (0, 110))
        self.assertEqual(len(segments(points)), 2)

    def test_every_point_survives_the_split(self):
        points = series((300, 90), (285, 95), (120, 100), (15, 105), (0, 110))
        flattened = [p for run in segments(points) for p in run]
        self.assertEqual(flattened, list(points))

    def test_nothing_splits_into_nothing(self):
        self.assertEqual(segments([]), [])


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
