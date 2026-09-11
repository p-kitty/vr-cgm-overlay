"""The run log reaches a file, arrows and tracebacks included.

What `NOTES.md` still has open is mostly answered by reading a log after
a long session, so the file has to hold the lines that matter: the
fetch lines, which carry the diagonal arrows a cp932 file would drop,
and the traceback of whatever went wrong, which Python prints past
every handler unless it is asked not to.
"""

from __future__ import annotations

import logging
import sys
import tempfile
import threading
import unittest
from pathlib import Path

from cgm.core.librelink import TREND_ARROWS
from cgm.core.logfile import LOG_DAYS, LOG_NAME, log_to_file, log_uncaught

log = logging.getLogger("vrcgm")


class LogFileTestCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name) / "logs"
        # Registered after the directory, so it runs first: Windows will
        # not delete a file that a handler still holds open.
        self.addCleanup(self.detach)
        self.handler = None

        root = logging.getLogger()
        self.level = root.level
        root.setLevel(logging.INFO)

    def detach(self):
        root = logging.getLogger()
        root.setLevel(self.level)
        if self.handler is not None:
            root.removeHandler(self.handler)
            self.handler.close()

    def attach(self) -> Path:
        self.handler = log_to_file(self.dir)
        self.assertIsNotNone(self.handler)
        return self.dir / LOG_NAME

    def written(self) -> str:
        self.handler.flush()
        return (self.dir / LOG_NAME).read_text(encoding="utf-8")


class Writing(LogFileTestCase):
    def test_the_directory_is_made_on_the_way(self):
        self.assertTrue(self.attach().exists())

    def test_every_trend_arrow_survives(self):
        # The diagonals are the two cp932 has no room for, and logging
        # drops a line it cannot encode rather than raising.
        self.attach()
        arrows = "".join(TREND_ARROWS.values())
        log.info("fetched: %s", arrows)
        self.assertIn(arrows, self.written())

    def test_each_line_carries_its_date(self):
        # A file is read days later, so the time alone is not enough.
        self.attach()
        log.info("dated")
        self.assertRegex(self.written(), r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} INFO")

    def test_a_day_a_file_for_two_weeks(self):
        self.attach()
        self.assertEqual(self.handler.when, "MIDNIGHT")
        self.assertEqual(self.handler.backupCount, LOG_DAYS)

    def test_a_directory_it_cannot_make_does_not_stop_the_run(self):
        # A file where the directory should be: mkdir cannot get past it.
        self.dir.parent.mkdir(exist_ok=True)
        blocker = self.dir.parent / "blocker"
        blocker.write_text("")
        with self.assertLogs("vrcgm", level="WARNING"):
            self.assertIsNone(log_to_file(blocker / "logs"))


class Uncaught(LogFileTestCase):
    def setUp(self):
        super().setUp()
        hooks = sys.excepthook, threading.excepthook
        self.addCleanup(self.restore, hooks)
        log_uncaught()

    @staticmethod
    def restore(hooks):
        sys.excepthook, threading.excepthook = hooks

    def test_a_traceback_that_ends_the_process_reaches_the_file(self):
        self.attach()
        try:
            raise RuntimeError("the reason it died")
        except RuntimeError:
            sys.excepthook(*sys.exc_info())
        written = self.written()
        self.assertIn("the reason it died", written)
        self.assertIn("Traceback", written)

    def test_so_does_one_that_ends_a_thread(self):
        # A thread dying leaves the rest running as if nothing happened,
        # which is the failure most worth a line in the file.
        self.attach()

        def die():
            raise RuntimeError("the thread's reason")

        thread = threading.Thread(target=die, name="cgm-test")
        thread.start()
        thread.join()
        written = self.written()
        self.assertIn("cgm-test", written)
        self.assertIn("the thread's reason", written)


if __name__ == "__main__":
    unittest.main()
