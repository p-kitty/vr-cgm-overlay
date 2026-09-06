"""Picking up edits to config.toml without restarting.

Placement is tuned by trial and error with the headset on, so the file is
watched and edits land on the next draw. That puts a text editor's saves
straight into a resident process, and the two things that must hold are
awkward to check by hand: that a save is noticed at all, and that a
half-written or plainly wrong file is ignored rather than fatal.
"""

from __future__ import annotations

import logging
import os
import tempfile
import unittest
from pathlib import Path

from cgm.core.watcher import ConfigWatcher

ACCOUNT = '[account]\nemail = "someone@example.com"\npassword = "secret"\n'


def setUpModule():
    # The watcher logs a warning for every edit it rejects, which
    # several of these tests provoke on purpose.
    logging.getLogger("vrcgm").addHandler(logging.NullHandler())


def body(width: float = 0.14, interval: int = 60) -> str:
    return (
        f"{ACCOUNT}\n[display]\nwidth_m = {width}\n"
        f"\n[polling]\ninterval_sec = {interval}\n"
    )


class WatcherTestCase(unittest.TestCase):
    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.path = Path(self._dir.name) / "config.toml"
        self.write(body())
        self.watcher = ConfigWatcher(self.path)

    def write(self, text: str) -> None:
        self.path.write_text(text, encoding="utf-8")


class NoEdit(WatcherTestCase):
    def test_an_untouched_file_reloads_nothing(self):
        # Called about twenty times a second, so the quiet path has to
        # stay quiet.
        self.assertIsNone(self.watcher.poll())
        self.assertIsNone(self.watcher.poll())


class GoodEdit(WatcherTestCase):
    """Edits here change the file's length as well as its timestamp.

    The stamp is (mtime, size) and a real edit almost always moves both.
    An edit that moved neither -- the same number of characters, saved
    within one tick of the system clock -- would slip past, which no
    hand-driven edit can do but two writes in a test can. The two cases
    below force one half of the stamp at a time to show each is load
    bearing.
    """

    def test_a_changed_value_comes_back(self):
        self.write(body(width=0.255))
        cfg = self.watcher.poll()
        self.assertIsNotNone(cfg)
        self.assertEqual(cfg.vr.width_m, 0.255)

    def test_the_same_edit_is_reported_once(self):
        self.write(body(width=0.255))
        self.assertIsNotNone(self.watcher.poll())
        self.assertIsNone(self.watcher.poll())

    def test_a_same_length_edit_is_caught_by_the_timestamp(self):
        # 0.14 and 0.99 are the same number of characters, so the size
        # does not move and only the timestamp can catch this one.
        self.write(body(width=0.99))
        os.utime(self.path, (0, 0))
        cfg = self.watcher.poll()
        self.assertIsNotNone(cfg)
        self.assertEqual(cfg.vr.width_m, 0.99)

    def test_a_same_timestamp_edit_is_caught_by_the_size(self):
        # The other half of the pair: editors can save twice inside the
        # filesystem's timestamp resolution, and on a coarse filesystem
        # both saves carry one mtime.
        stamp = self.path.stat().st_mtime
        self.write(body(width=0.123456))
        os.utime(self.path, (stamp, stamp))
        cfg = self.watcher.poll()
        self.assertIsNotNone(cfg)
        self.assertEqual(cfg.vr.width_m, 0.123456)


class BadEdit(WatcherTestCase):
    def test_a_half_written_file_is_ignored(self):
        # An editor writing in place can be caught mid-save. Taking the
        # process down for it would mean pulling the headset off.
        self.write("[display]\nwidth_m = ")
        self.assertIsNone(self.watcher.poll())

    def test_a_rejected_setting_is_ignored(self):
        self.write(body(interval=5))
        self.assertIsNone(self.watcher.poll())

    def test_a_key_nothing_reads_is_ignored(self):
        # A typo saved with the headset on is the likeliest way to reach
        # this: the edit is refused, the running config stays, and the
        # log says which key it was.
        self.write(body() + "\n[trend]\nwindowmin = 30\n")
        self.assertIsNone(self.watcher.poll())

    def test_a_deleted_file_is_ignored(self):
        self.path.unlink()
        self.assertIsNone(self.watcher.poll())

    def test_the_fix_after_a_bad_edit_is_still_noticed(self):
        # The failure that would matter: refusing an edit and then never
        # looking at the file again, so the correction never lands.
        self.write(body(interval=5))
        self.assertIsNone(self.watcher.poll())

        self.write(body(width=0.255, interval=120))
        cfg = self.watcher.poll()
        self.assertIsNotNone(cfg)
        self.assertEqual(cfg.vr.width_m, 0.255)

    def test_a_file_restored_after_deletion_is_noticed(self):
        self.path.unlink()
        self.assertIsNone(self.watcher.poll())

        self.write(body(width=0.255))
        cfg = self.watcher.poll()
        self.assertIsNotNone(cfg)
        self.assertEqual(cfg.vr.width_m, 0.255)


if __name__ == "__main__":
    unittest.main()
