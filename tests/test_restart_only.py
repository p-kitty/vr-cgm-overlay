"""Which settings a live reload cannot apply, and how that is noticed.

`config.toml` is re-read while the process runs, but a handful of keys
were consumed once at startup: the overlay picked its controller role
from `hand`, and the API client was built with `[account]`. Editing one
of those changes nothing until a restart, and silently doing nothing is
the worst of the three possible behaviours -- so the running frontend
compares them and says which one moved.

Which keys those are depends on the frontend. `[account]` is everyone's,
because every frontend builds the same client from it. `hand` is the
overlay's alone: a window has no controller to pick, so telling someone
to restart for it would send them off to restart and find nothing
different.

The comparison walks a dotted path per setting, which is a string, which
means a typo in it is invisible until someone saves the file with the
headset on and the draw loop dies on an AttributeError. That is what
these assert against.
"""

from __future__ import annotations

import unittest

from cgm.core.config import Config
from cgm.main import (
    RESTART_ONLY,
    RESTART_ONLY_ACCOUNT,
    RESTART_ONLY_VR,
    _setting,
    warn_restart_only,
)


def changed(before: Config, after: Config, settings=RESTART_ONLY) -> list[str]:
    return warn_restart_only(after, before, settings)


class Paths(unittest.TestCase):
    def test_every_path_resolves(self):
        # A typo here would raise on the first reload after the edit --
        # inside the headset, in the draw loop, rather than at startup.
        cfg = Config()
        for name, path in RESTART_ONLY.items():
            with self.subTest(name):
                _setting(cfg, path)

    def test_the_overlay_checks_the_account_and_the_hand(self):
        self.assertEqual(RESTART_ONLY, RESTART_ONLY_ACCOUNT | RESTART_ONLY_VR)

    def test_the_two_halves_do_not_overlap(self):
        # Reported twice would read as two settings needing a restart.
        self.assertEqual(RESTART_ONLY_ACCOUNT.keys() & RESTART_ONLY_VR.keys(), set())

    def test_the_account_belongs_to_every_frontend(self):
        # The client is built once from it, whichever loop is running.
        for name in RESTART_ONLY_ACCOUNT:
            with self.subTest(name):
                self.assertTrue(name.startswith("account."))

    def test_the_names_are_the_ones_in_the_file(self):
        # The warning tells the user what to go and change back, so it
        # has to name the key as config.toml spells it, not as the
        # sectioned dataclass does.
        for name in RESTART_ONLY:
            with self.subTest(name):
                section, _, key = name.partition(".")
                self.assertIn(section, ("account", "display"))
                self.assertTrue(key)


class Detection(unittest.TestCase):
    def test_an_untouched_config_reports_nothing(self):
        self.assertEqual(changed(Config(), Config()), [])

    def test_the_controller_role_is_restart_only(self):
        after = Config()
        after.vr.hand = "right"
        self.assertEqual(changed(Config(), after), ["display.hand"])

    def test_the_account_is_restart_only(self):
        after = Config()
        after.account.region = "jp"
        self.assertEqual(changed(Config(), after), ["account.region"])

    def test_a_window_is_not_told_to_restart_for_the_hand(self):
        # It has no controller, so `hand` is not a change waiting on a
        # restart there -- it is a key that frontend never reads.
        after = Config()
        after.vr.hand = "right"
        self.assertEqual(changed(Config(), after, RESTART_ONLY_ACCOUNT), [])

    def test_a_window_is_still_told_about_the_account(self):
        after = Config()
        after.account.email = "someone.else@example.com"
        self.assertEqual(
            changed(Config(), after, RESTART_ONLY_ACCOUNT), ["account.email"]
        )

    def test_the_window_settings_are_not_restart_only(self):
        # They are the reason the window watches the file at all.
        after = Config()
        after.window.scale = 2.0
        after.window.always_on_top = False
        self.assertEqual(changed(Config(), after), [])

    def test_a_re_readable_setting_is_not_reported(self):
        # Placement is the whole reason the file is watched. Warning
        # about it would train the user to ignore the warning.
        after = Config()
        after.vr.width_m = 0.2
        after.vr.offset = (0.0, 0.05, 0.12)
        after.thresholds.low_mgdl = 80.0
        after.polling.interval_sec = 90.0
        self.assertEqual(changed(Config(), after), [])


if __name__ == "__main__":
    unittest.main()
