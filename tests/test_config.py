"""Loading config.toml, and the rules it has to satisfy.

`_validate` exists so a contradictory setting is caught at startup rather
than after the headset is on. Two of its rules are project decisions
rather than mechanical checks -- the thirty second polling floor and the
low < high < very_high ordering -- and this file is where they stop being
prose and become something that runs.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import config as config_mod
from librelink import MIN_FIT_SPAN_MIN

ACCOUNT = '[account]\nemail = "someone@example.com"\npassword = "secret"\n'


class ConfigTestCase(unittest.TestCase):
    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.path = Path(self._dir.name) / "config.toml"

    def load(self, body: str = "", *, account: str = ACCOUNT):
        self.path.write_text(account + body, encoding="utf-8")
        return config_mod.load(self.path)


class Loading(ConfigTestCase):
    def test_absent_keys_fall_back_to_defaults(self):
        # config.example.toml is not exhaustive and a user's file need not
        # be either; every section below [account] is optional.
        cfg = self.load()
        self.assertEqual(cfg.unit, "mgdl")
        self.assertEqual(cfg.hand, "left")
        self.assertEqual(cfg.poll_interval_sec, 60.0)
        self.assertEqual(cfg.low_mgdl, 70.0)

    def test_values_are_read_from_the_file(self):
        cfg = self.load(
            "\n[display]\nunit = 'mmol'\nhand = 'right'\nwidth_m = 0.2\n"
            "\n[thresholds]\nlow_mgdl = 80\nhigh_mgdl = 170\nvery_high_mgdl = 250\n"
            "\n[polling]\ninterval_sec = 90\nalert_on_low = false\n"
        )
        self.assertEqual(cfg.unit, "mmol")
        self.assertEqual(cfg.hand, "right")
        self.assertEqual(cfg.width_m, 0.2)
        self.assertEqual(cfg.low_mgdl, 80.0)
        self.assertEqual(cfg.poll_interval_sec, 90.0)
        self.assertFalse(cfg.alert_on_low)

    def test_integers_in_the_file_arrive_as_floats(self):
        # TOML distinguishes 70 from 70.0; the comparisons downstream
        # should not have to.
        cfg = self.load("\n[thresholds]\nlow_mgdl = 70\n")
        self.assertIsInstance(cfg.low_mgdl, float)

    def test_placement_arrives_as_a_tuple(self):
        cfg = self.load(
            "\n[display]\noffset = [0.0, -0.02, 0.1]\nrotation_deg = [-40, 0, 90]\n"
        )
        self.assertEqual(cfg.offset, (0.0, -0.02, 0.1))
        self.assertEqual(cfg.rotation_deg, (-40, 0, 90))

    def test_orbit_defaults_to_off(self):
        # Fixed placement is what has been tuned on a real arm, so orbit
        # is opt in and its own settings still have to have values.
        cfg = self.load()
        self.assertFalse(cfg.orbit)
        self.assertFalse(cfg.arm_guide)
        self.assertEqual(cfg.orbit_radius_m, 0.06)
        self.assertEqual(cfg.orbit_limit_deg, 120.0)

    def test_orbit_settings_are_read(self):
        cfg = self.load(
            "\n[display]\norbit = true\norbit_radius_m = 0.05\n"
            "orbit_limit_deg = 100\narm_guide = true\n"
        )
        self.assertTrue(cfg.orbit)
        self.assertTrue(cfg.arm_guide)
        self.assertEqual(cfg.orbit_radius_m, 0.05)
        self.assertEqual(cfg.orbit_limit_deg, 100.0)

    def test_trend_defaults_to_a_quarter_hour_window(self):
        cfg = self.load()
        self.assertTrue(cfg.trend_local)
        self.assertEqual(cfg.trend_window_min, 15.0)
        self.assertEqual(cfg.trend_fast_mgdl_min, 2.0)

    def test_trend_settings_are_read(self):
        cfg = self.load(
            "\n[trend]\nlocal = false\nwindow_min = 30\nfast_mgdl_min = 1.5\n"
        )
        self.assertFalse(cfg.trend_local)
        self.assertEqual(cfg.trend_window_min, 30.0)
        self.assertEqual(cfg.trend_fast_mgdl_min, 1.5)

    def test_a_blank_patient_id_means_unset(self):
        # An empty string would be sent as a patient id and 404; absent
        # means "work it out from the connections list".
        cfg = self.load(account=ACCOUNT + 'patient_id = ""\nregion = ""\n')
        self.assertIsNone(cfg.patient_id)
        self.assertIsNone(cfg.region)

    def test_a_missing_file_says_what_to_do(self):
        missing = Path(self._dir.name) / "nope.toml"
        with self.assertRaises(FileNotFoundError) as caught:
            config_mod.load(missing)
        self.assertIn("config.example.toml", str(caught.exception))

    def test_broken_toml_raises(self):
        self.path.write_text("[account\nemail = ", encoding="utf-8")
        # TOMLDecodeError is a ValueError, which is what the config
        # watcher catches to keep a half-written file from ending the run.
        with self.assertRaises(ValueError):
            config_mod.load(self.path)


class Validation(ConfigTestCase):
    def assertRejected(self, body: str, *, account: str = ACCOUNT) -> str:
        with self.assertRaises(ValueError) as caught:
            self.load(body, account=account)
        return str(caught.exception)

    def test_credentials_are_required(self):
        # Without them the first fetch fails with an auth error and backs
        # off to ten minutes, which is a confusing way to learn this.
        self.assertRejected("", account='[account]\npassword = "secret"\n')
        self.assertRejected("", account='[account]\nemail = "a@b.c"\n')

    def test_unit_must_be_one_of_the_two(self):
        self.assertRejected("\n[display]\nunit = 'mmoll'\n")

    def test_hand_must_be_one_of_the_two(self):
        self.assertRejected("\n[display]\nhand = 'both'\n")

    def test_placement_needs_three_numbers(self):
        self.assertRejected("\n[display]\noffset = [0.0, 0.1]\n")
        self.assertRejected("\n[display]\nrotation_deg = [0, 0, 0, 0]\n")

    def test_orbit_radius_must_be_positive(self):
        # A zero radius puts the face on the arm's own centreline, where
        # there is no outward direction to turn it towards.
        self.assertRejected("\n[display]\norbit_radius_m = 0\n")
        self.assertRejected("\n[display]\norbit_radius_m = -0.06\n")

    def test_orbit_limit_must_be_within_half_a_turn(self):
        message = self.assertRejected("\n[display]\norbit_limit_deg = 181\n")
        self.assertIn("(0, 180]", message)
        self.assertRejected("\n[display]\norbit_limit_deg = 0\n")
        self.assertRejected("\n[display]\norbit_limit_deg = -20\n")

    def test_half_a_turn_either_way_is_allowed(self):
        # 180 each way is the whole circle: the most travel that can be
        # asked for, rather than one degree too much.
        cfg = self.load("\n[display]\norbit_limit_deg = 180\n")
        self.assertEqual(cfg.orbit_limit_deg, 180.0)

    def test_orbit_is_checked_even_when_it_is_switched_off(self):
        # Orbit is turned on from inside the headset. A radius that is
        # only rejected at that point is rejected at the worst moment.
        self.assertRejected("\n[display]\norbit = false\norbit_radius_m = 0\n")

    def test_thresholds_must_be_ordered(self):
        message = self.assertRejected(
            "\n[thresholds]\nlow_mgdl = 200\nhigh_mgdl = 180\n"
        )
        self.assertIn("low < high < very_high", message)
        self.assertRejected("\n[thresholds]\nhigh_mgdl = 250\nvery_high_mgdl = 240\n")

    def test_equal_thresholds_are_rejected(self):
        # Equal bounds leave a colour with no range to occupy, so the face
        # would simply never show it.
        self.assertRejected("\n[thresholds]\nlow_mgdl = 180\nhigh_mgdl = 180\n")

    def test_the_trend_window_must_be_long_enough_to_fit(self):
        # Under the fit's own span floor there is never enough spread in
        # the samples to fit a line, so the arrow would quietly sit on
        # the API's five buckets rather than failing where it was set.
        message = self.assertRejected("\n[trend]\nwindow_min = 4\n")
        self.assertIn("window_min", message)
        self.assertRejected("\n[trend]\nwindow_min = 0\n")

    def test_the_span_floor_itself_is_allowed(self):
        cfg = self.load(f"\n[trend]\nwindow_min = {MIN_FIT_SPAN_MIN}\n")
        self.assertEqual(cfg.trend_window_min, MIN_FIT_SPAN_MIN)

    def test_trend_settings_are_checked_even_when_the_fit_is_off(self):
        # local is flipped from inside the headset like everything else
        # here. A value only rejected once the fit is switched on is
        # rejected at the worst possible moment.
        self.assertRejected("\n[trend]\nlocal = false\nwindow_min = 1\n")
        self.assertRejected("\n[trend]\nlocal = false\nfast_mgdl_min = 0\n")

    def test_the_fast_rate_must_be_positive(self):
        # It divides the slope, so zero would take the draw loop down on
        # the first reading rather than at startup. Negative would draw
        # every arrow backwards, which is worse than crashing.
        message = self.assertRejected("\n[trend]\nfast_mgdl_min = 0\n")
        self.assertIn("fast_mgdl_min", message)
        self.assertRejected("\n[trend]\nfast_mgdl_min = -2\n")

    def test_polling_floor_is_thirty_seconds(self):
        # The sensor updates about once a minute. Anything faster returns
        # the same value and only risks the account being blocked.
        message = self.assertRejected("\n[polling]\ninterval_sec = 29\n")
        self.assertIn("30", message)

    def test_exactly_thirty_seconds_is_allowed(self):
        cfg = self.load("\n[polling]\ninterval_sec = 30\n")
        self.assertEqual(cfg.poll_interval_sec, 30.0)


if __name__ == "__main__":
    unittest.main()
