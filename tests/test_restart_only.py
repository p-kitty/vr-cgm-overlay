"""Which settings a live reload cannot apply, and how that is noticed.

`config.toml` is re-read while the process runs, but `[account]` is
consumed once: the API client is built from it at startup and nothing
rebuilds it. Editing one of those keys changes nothing until a restart,
and silently doing nothing is the worst of the three possible
behaviours -- so the running process compares them and says which one
moved.

`hand` used to be here as well, because the overlay picked its
controller role when the process started. It picks it when the *VR
session* starts now, and a session can be reopened without the process
going anywhere, so editing `hand` reopens one instead of asking for a
restart. What is asserted below is that it is no longer reported: a
warning telling somebody to restart for a setting that already applied
itself is worse than no warning at all.

The list is read off `[account]` rather than written out, so a name in
it cannot be misspelled -- which used to mean an AttributeError in the
draw loop on the first reload after the edit, with the headset on. What
is asserted is that it stays the whole of that section and nothing else.
"""

from __future__ import annotations

import unittest

from cgm.core.config import FIELD_TYPES, Config
from cgm.main import RESTART_ONLY, _setting, warn_restart_only


def changed(before: Config, after: Config) -> list[str]:
    return warn_restart_only(after, before)


class Names(unittest.TestCase):
    def test_every_name_resolves(self):
        cfg = Config()
        for name in RESTART_ONLY:
            with self.subTest(name):
                _setting(cfg, name)

    def test_the_account_is_the_whole_of_it(self):
        # The client is built once from all of it, whichever frontends
        # are up. Everything else in the file is either re-readable or
        # reopens the session that read it.
        self.assertEqual(
            set(RESTART_ONLY), {f"account.{key}" for key in FIELD_TYPES["account"]}
        )

    def test_the_names_are_the_ones_in_the_file(self):
        # The warning tells the user what to go and change back, so it
        # has to name the key as config.toml spells it.
        self.assertIn("account.api_version", RESTART_ONLY)


class Detection(unittest.TestCase):
    def test_an_untouched_config_reports_nothing(self):
        self.assertEqual(changed(Config(), Config()), [])

    def test_the_account_is_restart_only(self):
        after = Config()
        after.account.region = "jp"
        self.assertEqual(changed(Config(), after), ["account.region"])

    def test_the_controller_role_is_not_reported_any_more(self):
        # It reopens the VR session, which happens by itself and within
        # a second. Being told to restart for it would send someone off
        # to restart and find it had already taken.
        after = Config()
        after.vr.hand = "right"
        self.assertEqual(changed(Config(), after), [])

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
