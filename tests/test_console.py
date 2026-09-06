"""Trend arrows survive a redirected stdout.

Python gives a real Windows console UTF-8 whatever the code page is, and
falls back to the locale encoding for a pipe or a redirect. Here that
fallback is cp932, which carries three of the five trend arrows and not
the two diagonals -- so `--dry-run > check.txt` raised
UnicodeEncodeError, or did not, according to which way the glucose was
moving, and a redirected log quietly dropped the fetch lines instead.

Nothing about that is reachable through the console the suite runs in,
so these drive the streams directly.
"""

from __future__ import annotations

import io
import logging
import sys
import tempfile
import unittest
from pathlib import Path

from cgm.core.console import _as_utf8, force_utf8_output
from cgm.core.librelink import TREND_ARROWS
from cgm.main import main


def cp932_stream() -> io.TextIOWrapper:
    """A text stream encoding the way a redirect does on this machine."""
    return io.TextIOWrapper(io.BytesIO(), encoding="cp932", newline="")


class Reconfigure(unittest.TestCase):
    def test_a_diagonal_arrow_raises_before_the_fix(self):
        # The bug itself, so the test below is known to be testing
        # something. If this ever stops raising, cp932 grew the glyph
        # and the rest of this file is moot.
        stream = cp932_stream()
        with self.assertRaises(UnicodeEncodeError):
            stream.write("↗")
            stream.flush()

    def test_every_trend_arrow_survives_afterwards(self):
        stream = cp932_stream()
        self.assertTrue(_as_utf8(stream))
        for value, arrow in sorted(TREND_ARROWS.items()):
            with self.subTest(trend=value, arrow=arrow):
                stream.write(arrow)
        stream.flush()
        written = stream.buffer.getvalue()
        self.assertEqual(
            written.decode("utf-8"), "".join(v for _, v in sorted(TREND_ARROWS.items()))
        )

    def test_a_stream_with_no_encoding_is_left_alone(self):
        # An IDE or a test harness hands out StringIO, which holds str
        # and has nothing to reconfigure. Say so rather than raising.
        stream = io.StringIO()
        self.assertFalse(_as_utf8(stream))
        stream.write("↗")
        self.assertEqual(stream.getvalue(), "↗")

    def test_a_closed_stream_does_not_take_the_process_down(self):
        stream = cp932_stream()
        stream.close()
        self.assertFalse(_as_utf8(stream))

    def test_none_does_not_take_the_process_down(self):
        # pythonw.exe has no console and hands out None for both.
        self.assertFalse(_as_utf8(None))

    def test_it_covers_both_streams(self):
        out, err = cp932_stream(), cp932_stream()
        original = sys.stdout, sys.stderr
        sys.stdout, sys.stderr = out, err
        try:
            force_utf8_output()
        finally:
            sys.stdout, sys.stderr = original
        self.assertEqual(out.encoding.lower(), "utf-8")
        self.assertEqual(err.encoding.lower(), "utf-8")


class EntryPoint(unittest.TestCase):
    """It has to happen before anything writes, not merely somewhere."""

    def test_main_reconfigures_before_its_first_print(self):
        # The config is deliberately missing, so main() reaches its
        # earliest print -- the config error on stderr -- and stops.
        out, err = cp932_stream(), cp932_stream()
        streams = sys.stdout, sys.stderr
        handlers = logging.root.handlers[:]
        sys.stdout, sys.stderr = out, err
        try:
            with tempfile.TemporaryDirectory() as tmp:
                code = main(["--config", str(Path(tmp) / "nowhere.toml")])
        finally:
            sys.stdout, sys.stderr = streams
            # main() calls basicConfig, which would otherwise leave the
            # root logger writing to these streams after they are gone.
            logging.root.handlers[:] = handlers
        # These wrap a BytesIO, which is neither a tty nor line
        # buffered, so nothing has reached the bytes yet.
        err.flush()

        self.assertEqual(code, 2)
        self.assertEqual(err.encoding.lower(), "utf-8")
        self.assertIn("config error", err.buffer.getvalue().decode("utf-8"))


if __name__ == "__main__":
    unittest.main()
