"""Where the window was left, kept between runs.

Nothing in the file is worth failing over, so most of this is what a
bad file does: it is read as no file at all, and the window opens where
Windows puts it.
"""

from __future__ import annotations

import json
import logging
import tempfile
import unittest
from pathlib import Path

from cgm.core.state import load_position, save_position


def setUpModule():
    # The bad-file cases warn, and would otherwise bury the test output.
    logging.getLogger("vrcgm").addHandler(logging.NullHandler())


class StateTestCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = Path(tmp.name) / "state.json"

    def write(self, text: str) -> None:
        self.path.write_text(text, encoding="utf-8")


class RoundTrip(StateTestCase):
    def test_nothing_is_remembered_before_anything_is_written(self):
        self.assertIsNone(load_position(self.path))

    def test_a_position_comes_back(self):
        save_position(self.path, (640, 120))
        self.assertEqual(load_position(self.path), (640, 120))

    def test_a_monitor_left_of_the_primary_one_is_negative(self):
        # A second monitor to the left puts the window at a negative x,
        # and that is a place like any other.
        save_position(self.path, (-1500, 80))
        self.assertEqual(load_position(self.path), (-1500, 80))

    def test_the_last_move_wins(self):
        save_position(self.path, (1, 2))
        save_position(self.path, (3, 4))
        self.assertEqual(load_position(self.path), (3, 4))

    def test_other_things_in_the_file_survive(self):
        self.write(json.dumps({"something": "else"}))
        save_position(self.path, (5, 6))
        self.assertEqual(json.loads(self.path.read_text())["something"], "else")

    def test_no_temporary_file_is_left_behind(self):
        save_position(self.path, (5, 6))
        self.assertEqual([p.name for p in self.path.parent.iterdir()], ["state.json"])


class BadFile(StateTestCase):
    def test_a_half_written_file_is_no_file(self):
        self.write('{"window": {"x": 1')
        self.assertIsNone(load_position(self.path))

    def test_a_file_of_another_shape_is_no_file(self):
        for text in ('[1, 2]', '{"window": [1, 2]}', '{"window": {"x": 1}}'):
            with self.subTest(text=text):
                self.write(text)
                self.assertIsNone(load_position(self.path))

    def test_coordinates_have_to_be_whole_numbers(self):
        # true is an int to Python and would come back as 1.
        for x in ('"10"', "1.5", "true", "null"):
            with self.subTest(x=x):
                self.write(f'{{"window": {{"x": {x}, "y": 2}}}}')
                self.assertIsNone(load_position(self.path))

    def test_a_bad_file_is_replaced_by_the_next_move(self):
        self.write("not json")
        save_position(self.path, (7, 8))
        self.assertEqual(load_position(self.path), (7, 8))

    def test_a_file_that_cannot_be_written_does_not_raise(self):
        # A directory where the file should be: the move cannot land.
        self.path.mkdir()
        with self.assertLogs("vrcgm", level="WARNING"):
            save_position(self.path, (7, 8))


if __name__ == "__main__":
    unittest.main()
