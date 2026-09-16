"""The history's average, and the row it is drawn in.

The arithmetic is short, and the edges are in what it refuses: a
history too short to average must come out as no average rather than
as the current reading relabelled. The row itself is asserted only as
far as geometry goes -- that it grows the card, and that the graph under
it moves down with it rather than being drawn over.
"""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from cgm.core.config import Config
from cgm.core.librelink import GlucosePoint, Reading
from cgm.face.average import MIN_SPAN_MIN, history_average
from cgm.face.graph import GraphTuning
from cgm.face.renderer import (
    AVERAGE_HEIGHT,
    GRAPH_HEIGHT,
    GRAPH_TOP,
    HEIGHT,
    WatchFaceRenderer,
)
from cgm.main import build_renderer

NOW = datetime(2026, 9, 7, 12, 0, tzinfo=timezone.utc)


def series(values, step_min: float = 15.0) -> tuple[GlucosePoint, ...]:
    """`values` oldest first, one every `step_min`, ending at NOW."""
    last = len(values) - 1
    return tuple(
        GlucosePoint(NOW - timedelta(minutes=(last - i) * step_min), v)
        for i, v in enumerate(values)
    )


class HistoryAverage(unittest.TestCase):
    def test_the_mean_of_the_samples(self):
        stats = history_average(series([100, 120, 140, 160, 180]))
        self.assertEqual(stats.mgdl, 140.0)
        self.assertEqual(stats.span_min, 60.0)
        self.assertEqual(stats.count, 5)

    def test_twelve_hours_is_reported_as_twelve_hours(self):
        stats = history_average(series([120] * 49))
        self.assertEqual(stats.span_min, 720.0)

    def test_nothing_to_average(self):
        self.assertIsNone(history_average(()))
        self.assertIsNone(history_average(series([120])))

    def test_under_the_minimum_span_is_no_average(self):
        # A fresh sensor's first few points. The mean of those is the
        # current reading with a label claiming more than it is.
        points = series([100, 110, 120], step_min=(MIN_SPAN_MIN - 1) / 2)
        self.assertIsNone(history_average(points))

    def test_the_minimum_span_itself_is_enough(self):
        points = series([100, 110, 120], step_min=MIN_SPAN_MIN / 2)
        self.assertIsNotNone(history_average(points))

    def test_a_scanning_gap_is_not_filled_in(self):
        # Two hours at 100, a gap, then an hour at 200. Weighted by time
        # across the gap the mean would lean towards whichever side the
        # interpolation favoured; over the samples it is what was measured.
        before = series([100] * 9)
        after = tuple(
            GlucosePoint(p.at + timedelta(hours=5), 200) for p in series([200] * 5)
        )
        stats = history_average(before + after)
        self.assertAlmostEqual(stats.mgdl, (9 * 100 + 5 * 200) / 14)


class Card(unittest.TestCase):
    def reading(self, values=(120,) * 49) -> Reading:
        return Reading(values[-1], 3, NOW, history=series(list(values)))

    def test_off_is_the_face_that_always_shipped(self):
        renderer = WatchFaceRenderer()
        self.assertEqual(renderer.height, HEIGHT)

    def test_the_row_grows_the_card(self):
        renderer = WatchFaceRenderer(average=True)
        self.assertEqual(renderer.height, HEIGHT + AVERAGE_HEIGHT)
        self.assertEqual(
            renderer.render(self.reading(), now=NOW).size,
            (renderer.width, renderer.height),
        )

    def test_the_graph_moves_down_under_the_row(self):
        plain = WatchFaceRenderer(graph=GraphTuning())
        averaged = WatchFaceRenderer(graph=GraphTuning(), average=True)
        self.assertEqual(averaged.height, HEIGHT + AVERAGE_HEIGHT + GRAPH_HEIGHT)
        self.assertEqual(plain._graph_box()[1], GRAPH_TOP)
        self.assertEqual(averaged._graph_box()[1], GRAPH_TOP + AVERAGE_HEIGHT)
        # The plot keeps its size; only where it sits changes.
        self.assertEqual(
            plain._graph_box()[3] - plain._graph_box()[1],
            averaged._graph_box()[3] - averaged._graph_box()[1],
        )

    def test_too_little_history_still_draws(self):
        renderer = WatchFaceRenderer(average=True, unit="mmol")
        renderer.render(self.reading((120,)), now=NOW)

    def test_a_message_card_is_as_tall_as_the_face(self):
        # The frontends take their size from the image, so a card before
        # the first reading must not be a different height.
        renderer = WatchFaceRenderer(average=True)
        self.assertEqual(renderer.render_message("CONNECTING").height, renderer.height)


class Settings(unittest.TestCase):
    def test_on_in_the_window_and_off_in_vr_by_default(self):
        # The same split as the graph, for the same reason.
        cfg = Config()
        self.assertTrue(cfg.average.in_window)
        self.assertFalse(cfg.average.in_vr)

    def test_each_frontend_is_built_from_its_own_switch(self):
        cfg = Config()
        cfg.average.in_window = False
        cfg.average.in_vr = True
        self.assertFalse(
            build_renderer(cfg, with_average=cfg.average.in_window).average
        )
        self.assertTrue(build_renderer(cfg, with_average=cfg.average.in_vr).average)


if __name__ == "__main__":
    unittest.main()
