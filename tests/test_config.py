"""Loading config.toml, writing it back, and the rules it has to satisfy.

`_validate` exists so a contradictory setting is caught at startup rather
than after the headset is on. Two of its rules are project decisions
rather than mechanical checks -- the thirty second polling floor and the
low < high < very_high ordering -- and this file is where they stop being
prose and become something that runs.

Both directions are one walk over the dataclasses now, so most of what
used to be asserted here about individual keys is really asserting that
the walk reaches them. The rest is about `save` not damaging the file it
writes into: the comments in config.toml are the only documentation of
most of these settings that anyone editing by hand will see, and a save
that quietly threw them away would be discovered long after the fact.
"""

from __future__ import annotations

import tempfile
import tomllib
import unittest
from dataclasses import asdict, fields
from pathlib import Path

from cgm.core import config as config_mod
from cgm.core.config import WINDOW_SCALE_MAX, WINDOW_SCALE_MIN
from cgm.face.graph import TICK_MAJOR_MIN
from cgm.face.graph import AXIS_FLOOR_MGDL

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
        self.assertEqual(cfg.display.unit, "mgdl")
        self.assertEqual(cfg.vr.hand, "left")
        self.assertEqual(cfg.polling.interval_sec, 60.0)
        self.assertEqual(cfg.thresholds.low_mgdl, 70.0)

    def test_values_are_read_from_the_file(self):
        cfg = self.load(
            "\n[display]\nunit = 'mmol'\n"
            "\n[vr]\nhand = 'right'\nwidth_m = 0.2\n"
            "\n[thresholds]\nlow_mgdl = 80\nhigh_mgdl = 170\nvery_high_mgdl = 250\n"
            "\n[polling]\ninterval_sec = 90\nalert_on_low = false\n"
        )
        self.assertEqual(cfg.display.unit, "mmol")
        self.assertEqual(cfg.vr.hand, "right")
        self.assertEqual(cfg.vr.width_m, 0.2)
        self.assertEqual(cfg.thresholds.low_mgdl, 80.0)
        self.assertEqual(cfg.polling.interval_sec, 90.0)
        self.assertFalse(cfg.polling.alert_on_low)

    def test_integers_in_the_file_arrive_as_floats(self):
        # TOML distinguishes 70 from 70.0; the comparisons downstream
        # should not have to.
        cfg = self.load("\n[thresholds]\nlow_mgdl = 70\n")
        self.assertIsInstance(cfg.thresholds.low_mgdl, float)

    def test_placement_arrives_as_a_tuple(self):
        cfg = self.load(
            "\n[vr]\noffset = [0.0, -0.02, 0.1]\nrotation_deg = [-40, 0, 90]\n"
        )
        self.assertEqual(cfg.vr.offset, (0.0, -0.02, 0.1))
        self.assertEqual(cfg.vr.rotation_deg, (-40, 0, 90))

    def test_orbit_defaults_to_off(self):
        # Fixed placement is what has been tuned on a real arm, so orbit
        # is opt in and its own settings still have to have values.
        cfg = self.load()
        self.assertFalse(cfg.vr.orbit)
        self.assertFalse(cfg.vr.arm_guide)
        self.assertEqual(cfg.vr.orbit_radius_m, 0.06)
        self.assertEqual(cfg.vr.orbit_limit_deg, 120.0)

    def test_orbit_settings_are_read(self):
        cfg = self.load(
            "\n[vr]\norbit = true\norbit_radius_m = 0.05\n"
            "orbit_limit_deg = 100\narm_guide = true\n"
        )
        self.assertTrue(cfg.vr.orbit)
        self.assertTrue(cfg.vr.arm_guide)
        self.assertEqual(cfg.vr.orbit_radius_m, 0.05)
        self.assertEqual(cfg.vr.orbit_limit_deg, 100.0)

    def test_trend_defaults_to_an_hour_window(self):
        cfg = self.load()
        self.assertTrue(cfg.trend.local)
        self.assertEqual(cfg.trend.fast_mgdl_min, 2.0)

    def test_trend_settings_are_read(self):
        cfg = self.load(
            "\n[trend]\nlocal = false\nfast_mgdl_min = 1.5\n"
        )
        self.assertFalse(cfg.trend.local)
        self.assertEqual(cfg.trend.fast_mgdl_min, 1.5)

    def test_gaze_fade_defaults_to_off(self):
        # A glucose readout is not a desktop window, so the fade is opt
        # in; its settings still have to have values, since they are read
        # whether or not it is on.
        cfg = self.load()
        self.assertFalse(cfg.vr.gaze_fade)
        self.assertEqual(cfg.vr.gaze_full_deg, 20.0)
        self.assertEqual(cfg.vr.gaze_fade_deg, 45.0)
        self.assertEqual(cfg.vr.gaze_min_alpha, 0.25)

    def test_gaze_settings_are_read(self):
        cfg = self.load(
            "\n[vr]\ngaze_fade = true\ngaze_full_deg = 15\n"
            "gaze_fade_deg = 60\ngaze_min_alpha = 0.4\n"
        )
        self.assertTrue(cfg.vr.gaze_fade)
        self.assertEqual(cfg.vr.gaze_full_deg, 15.0)
        self.assertEqual(cfg.vr.gaze_fade_deg, 60.0)
        self.assertEqual(cfg.vr.gaze_min_alpha, 0.4)

    def test_the_window_defaults_to_native_size_and_on_top(self):
        # Native size because the face was laid out at 512x256 and
        # anything else is resampling it; on top because a readout you
        # have to go and find is not a readout.
        cfg = self.load()
        self.assertEqual(cfg.window.scale, 1.0)
        self.assertTrue(cfg.window.always_on_top)

    def test_window_settings_are_read(self):
        cfg = self.load(
            "\n[window]\nscale = 0.75\nalways_on_top = false\n"
        )
        self.assertEqual(cfg.window.scale, 0.75)
        self.assertFalse(cfg.window.always_on_top)

    def test_the_graph_defaults_to_the_window_and_not_to_vr(self):
        # The one setting the two frontends deliberately disagree on. A
        # window is read at a desk, where three hours of history is worth
        # the space; the overlay is glanced at mid-game, where the number
        # in half a second is the whole design goal.
        cfg = self.load()
        self.assertTrue(cfg.graph.in_window)
        self.assertFalse(cfg.graph.in_vr)
        # Eight hours: long enough to hold a night, which is the span
        # the official app shows and the one worth waking up to.
        self.assertEqual(cfg.graph.window_min, 480.0)
        self.assertEqual(cfg.graph.axis_high_mgdl, 300.0)

    def test_graph_settings_are_read(self):
        cfg = self.load(
            "\n[graph]\nin_window = false\nin_vr = true\nwindow_min = 240\n"
            "axis_high_mgdl = 280\n"
        )
        self.assertFalse(cfg.graph.in_window)
        self.assertTrue(cfg.graph.in_vr)
        self.assertEqual(cfg.graph.window_min, 240.0)
        self.assertEqual(cfg.graph.axis_high_mgdl, 280.0)

    def test_the_bottom_of_the_axis_is_not_a_setting(self):
        # It used to be. Removing it has to fail loudly rather than
        # quietly ignore the line somebody already had in their file --
        # which is exactly what _check_keys is for.
        with self.assertRaises(ValueError) as caught:
            self.load("\n[graph]\naxis_low_mgdl = 40\n")
        self.assertIn("axis_low_mgdl", str(caught.exception))

    def test_a_blank_patient_id_stays_a_blank_string(self):
        # Empty is how the file spells "not set", and both readers ask
        # these for truthiness rather than for None, so there is nothing
        # left for a second empty value to mean. What must not happen is
        # the blank being sent as a patient id, which would 404.
        cfg = self.load(account=ACCOUNT + 'patient_id = ""\nregion = ""\n')
        self.assertEqual(cfg.account.patient_id, "")
        self.assertEqual(cfg.account.region, "")
        self.assertFalse(cfg.account.patient_id)
        self.assertFalse(cfg.account.region)

    def test_an_absent_patient_id_is_the_same_blank(self):
        # Absent and blank have to agree: one type per field is what
        # lets the file be written back by walking the dataclasses.
        cfg = self.load()
        self.assertEqual(cfg.account.patient_id, "")
        self.assertEqual(cfg.account.region, "")

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
        self.assertRejected("\n[vr]\nhand = 'both'\n")

    def test_placement_needs_three_numbers(self):
        self.assertRejected("\n[vr]\noffset = [0.0, 0.1]\n")
        self.assertRejected("\n[vr]\nrotation_deg = [0, 0, 0, 0]\n")

    def test_orbit_radius_must_be_positive(self):
        # A zero radius puts the face on the arm's own centreline, where
        # there is no outward direction to turn it towards.
        self.assertRejected("\n[vr]\norbit_radius_m = 0\n")
        self.assertRejected("\n[vr]\norbit_radius_m = -0.06\n")

    def test_orbit_limit_must_be_within_half_a_turn(self):
        message = self.assertRejected("\n[vr]\norbit_limit_deg = 181\n")
        self.assertIn("(0, 180]", message)
        self.assertRejected("\n[vr]\norbit_limit_deg = 0\n")
        self.assertRejected("\n[vr]\norbit_limit_deg = -20\n")

    def test_half_a_turn_either_way_is_allowed(self):
        # 180 each way is the whole circle: the most travel that can be
        # asked for, rather than one degree too much.
        cfg = self.load("\n[vr]\norbit_limit_deg = 180\n")
        self.assertEqual(cfg.vr.orbit_limit_deg, 180.0)

    def test_orbit_is_checked_even_when_it_is_switched_off(self):
        # Orbit is turned on from inside the headset. A radius that is
        # only rejected at that point is rejected at the worst moment.
        self.assertRejected("\n[vr]\norbit = false\norbit_radius_m = 0\n")

    def test_gaze_angles_must_be_ordered(self):
        # Equal bounds would step from full to the floor at one angle
        # rather than fade across a span, which is the one thing a fade
        # must not do: a face that blinks reads as a fault.
        message = self.assertRejected(
            "\n[vr]\ngaze_full_deg = 45\ngaze_fade_deg = 45\n"
        )
        self.assertIn("full < fade", message)
        self.assertRejected("\n[vr]\ngaze_full_deg = 60\ngaze_fade_deg = 30\n")
        self.assertRejected("\n[vr]\ngaze_full_deg = -5\n")
        self.assertRejected("\n[vr]\ngaze_fade_deg = 181\n")

    def test_the_gaze_floor_may_not_reach_zero(self):
        # This is the condition NOTES.md set for the fade existing at
        # all. A face that faded to nothing would look exactly like the
        # process having died, which is the failure the whole thing
        # exists to avoid, so it is a rule and not just a default.
        message = self.assertRejected("\n[vr]\ngaze_min_alpha = 0\n")
        self.assertIn(str(config_mod.GAZE_ALPHA_FLOOR), message)
        self.assertRejected("\n[vr]\ngaze_min_alpha = 0.05\n")
        self.assertRejected("\n[vr]\ngaze_min_alpha = -1\n")
        self.assertRejected("\n[vr]\ngaze_min_alpha = 1.5\n")

    def test_the_gaze_floor_may_be_fully_opaque(self):
        # A floor of 1 is a fade that does nothing. Pointless rather than
        # wrong, and rejecting it would only be a trap while tuning.
        cfg = self.load("\n[vr]\ngaze_min_alpha = 1.0\n")
        self.assertEqual(cfg.vr.gaze_min_alpha, 1.0)

    def test_a_value_of_the_wrong_type_names_the_key(self):
        # Unnamed, this is `could not convert string to float: 'wide'`,
        # which says nothing about which line to go and fix.
        message = self.assertRejected("\n[vr]\nwidth_m = 'wide'\n")
        self.assertIn("vr.width_m", message)
        self.assertIn("number", message)

    def test_a_quoted_boolean_is_not_believed_backwards(self):
        # `bool("false")` is True, and so is every other non-empty
        # string, so a quoted false used to turn the setting on --
        # silently, with nothing about the file looking wrong.
        cfg = self.load('\n[polling]\nalert_on_low = "false"\n')
        self.assertFalse(cfg.polling.alert_on_low)

    def test_a_boolean_that_is_neither_is_refused_by_name(self):
        # Two spellings, and no guessing at a third.
        message = self.assertRejected("\n[graph]\nin_vr = 'yes'\n")
        self.assertIn("graph.in_vr", message)

    def test_the_toml_spellings_are_taken(self):
        # The same conversion is what a settings window hands its values
        # through, and a checkbox on the way out is a Python bool.
        self.assertIs(config_mod.parse("graph", "in_vr", "true"), True)
        self.assertIs(config_mod.parse("graph", "in_vr", "False"), False)
        self.assertIs(config_mod.parse("graph", "in_vr", False), False)

    def test_three_numbers_are_required_where_three_numbers_belong(self):
        # A bare number is not iterable and a string comes apart into
        # characters. Both are refused by name rather than by traceback.
        message = self.assertRejected("\n[vr]\noffset = 3\n")
        self.assertIn("vr.offset", message)
        self.assertIn("vr.rotation_deg", self.assertRejected(
            "\n[vr]\nrotation_deg = 'flat'\n"
        ))

    def test_gaze_is_checked_even_when_it_is_switched_off(self):
        # Like orbit, the fade is turned on from inside the headset. A
        # setting only rejected at that point is rejected at the worst
        # moment.
        self.assertRejected("\n[vr]\ngaze_fade = false\ngaze_min_alpha = 0\n")

    def test_the_window_scale_has_both_a_floor_and_a_ceiling(self):
        for scale in (0.0, 0.1, 8.0):
            with self.subTest(scale=scale):
                with self.assertRaises(ValueError) as caught:
                    self.load(f"\n[window]\nscale = {scale}\n")
                self.assertIn("window.scale", str(caught.exception))

    def test_the_window_scale_bounds_themselves_are_allowed(self):
        for scale in (WINDOW_SCALE_MIN, WINDOW_SCALE_MAX):
            with self.subTest(scale=scale):
                cfg = self.load(f"\n[window]\nscale = {scale}\n")
                self.assertEqual(cfg.window.scale, scale)

    def test_the_window_scale_is_checked_without_the_window(self):
        # Same rule as orbit and gaze: a setting only rejected by the
        # frontend that happens to read it is rejected at the worst
        # possible moment. This is the config, not the window.
        with self.assertRaises(ValueError):
            self.load(f"\n[window]\nscale = 99\n")

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

    def test_the_graph_axis_must_contain_the_target_range(self):
        # The band showing the range is what lets the trace be read
        # without an axis drawn next to it. An axis that clips the band
        # against an edge turns it into a floor or a ceiling, which says
        # something quite different.
        message = self.assertRejected("\n[graph]\naxis_high_mgdl = 150\n")
        self.assertIn("target range", message)
        # An axis top under the floor is the same failure, further gone.
        self.assertRejected("\n[graph]\naxis_high_mgdl = 40\n")

    def test_a_low_threshold_under_the_graph_floor_is_rejected(self):
        # The floor does not move, so a low_mgdl below it would put the
        # band's own edge off the bottom of the chart. The message has
        # to send the reader to the threshold, since the axis is not
        # theirs to lower.
        message = self.assertRejected("\n[thresholds]\nlow_mgdl = 45\n")
        self.assertIn(f"{AXIS_FLOOR_MGDL:.0f}", message)
        self.assertIn("low_mgdl", message)

    def test_the_thresholds_themselves_are_a_legal_axis(self):
        # An axis stopping exactly at high_mgdl is allowed: the band
        # then fills the plot, which is unusual but not contradictory.
        cfg = self.load("\n[graph]\naxis_high_mgdl = 180\n")
        self.assertEqual(cfg.graph.axis_high_mgdl, 180.0)

    def test_the_floor_itself_is_a_legal_low_threshold(self):
        cfg = self.load(f"\n[thresholds]\nlow_mgdl = {AXIS_FLOOR_MGDL:.0f}\n")
        self.assertEqual(cfg.thresholds.low_mgdl, AXIS_FLOOR_MGDL)

    def test_the_graph_window_must_be_long_enough_to_be_labelled(self):
        # The time axis is ruled at fixed points on the local clock, not
        # at whatever divides the window, so a window shorter than that
        # step can fall between two of them and be drawn with no label
        # at all.
        message = self.assertRejected("\n[graph]\nwindow_min = 60\n")
        self.assertIn("graph.window_min", message)
        self.assertIn(f"{TICK_MAJOR_MIN:.0f}", message)

    def test_a_graph_window_of_zero_means_all_of_it(self):
        # Not a length under the floor: a different request entirely.
        cfg = self.load("\n[graph]\nwindow_min = 0\n")
        self.assertEqual(cfg.graph.window_min, 0.0)

    def test_a_negative_graph_window_is_not_a_third_meaning(self):
        self.assertRejected("\n[graph]\nwindow_min = -60\n")

    def test_the_graph_window_floor_itself_is_allowed(self):
        floor = TICK_MAJOR_MIN
        cfg = self.load(f"\n[graph]\nwindow_min = {floor}\n")
        self.assertEqual(cfg.graph.window_min, floor)

    def test_graph_settings_are_checked_with_the_graph_switched_off(self):
        # They reload with everything else and either frontend can be
        # turned on from inside the headset, so a value only rejected
        # once something draws it is rejected at the worst moment.
        self.assertRejected(
            "\n[graph]\nin_window = false\nin_vr = false\nwindow_min = 15\n"
        )

    def test_the_graph_window_floor_is_the_label_step_itself(self):
        # Written down once. The floor exists because of how the axis is
        # ruled, so reading it off anything but the ruling would let the
        # two drift apart.
        message = self.assertRejected("\n[graph]\nwindow_min = 179\n")
        self.assertIn(f"at least {TICK_MAJOR_MIN:.0f}", message)

    def test_the_trend_window_is_no_longer_a_setting(self):
        # It was, and the file it was in is not rewritten by anything
        # here -- so an existing config.toml carrying it has to fail
        # where the key is rather than by quietly doing nothing, which
        # is what #8 made the rule for every unrecognised key.
        message = self.assertRejected("\n[trend]\nwindow_min = 60\n")
        self.assertIn("trend.window_min", message)
        # And it says where the one surviving window_min lives, which is
        # the graph rather than the arrow.
        self.assertIn("[graph]", message)

    def test_trend_settings_are_checked_even_when_the_fit_is_off(self):
        # local is flipped from inside the headset like everything else
        # here. A value only rejected once the arrow is switched on is
        # rejected at the worst possible moment.
        self.assertRejected("\n[trend]\nlocal = false\nfast_mgdl_min = 0\n")

    def test_the_fast_rate_must_be_positive(self):
        # It divides the slope, so zero would take the draw loop down on
        # the first reading rather than at startup. Negative would draw
        # every arrow backwards, which is worse than crashing.
        message = self.assertRejected("\n[trend]\nfast_mgdl_min = 0\n")
        self.assertIn("fast_mgdl_min", message)
        self.assertRejected("\n[trend]\nfast_mgdl_min = -2\n")

    def test_the_alert_channels_default_on(self):
        # An existing config.toml keeps buzzing as it did, and gains
        # the sound -- which is the channel that works on the stack
        # where the buzz does not.
        cfg = self.load()
        self.assertTrue(cfg.polling.alert_on_low)
        self.assertTrue(cfg.polling.alert_haptic)
        self.assertTrue(cfg.polling.alert_sound)
        self.assertEqual(cfg.polling.sound_path, "")

    def test_the_alert_fires_once_by_default(self):
        cfg = self.load()
        self.assertEqual(cfg.polling.repeat_every_min, 0.0)
        self.assertEqual(cfg.polling.rearm_margin_mgdl, 5.0)

    def test_alert_settings_are_read(self):
        cfg = self.load(
            "\n[polling]\nalert_haptic = false\nalert_sound = false\n"
            "rearm_margin_mgdl = 0\nrepeat_every_min = 15\n"
        )
        self.assertFalse(cfg.polling.alert_haptic)
        self.assertFalse(cfg.polling.alert_sound)
        self.assertEqual(cfg.polling.rearm_margin_mgdl, 0.0)
        self.assertEqual(cfg.polling.repeat_every_min, 15.0)

    def test_polling_floor_is_thirty_seconds(self):
        # The sensor updates about once a minute. Anything faster returns
        # the same value and only risks the account being blocked.
        message = self.assertRejected("\n[polling]\ninterval_sec = 29\n")
        self.assertIn("30", message)

    def test_a_negative_rearm_margin_is_rejected(self):
        # It would re-arm below the threshold, so a reading sitting
        # just under low_mgdl would announce itself over and over.
        with self.assertRaises(ValueError) as caught:
            self.load("\n[polling]\nrearm_margin_mgdl = -5\n")
        self.assertIn("rearm_margin_mgdl", str(caught.exception))

    def test_a_zero_rearm_margin_is_allowed(self):
        cfg = self.load("\n[polling]\nrearm_margin_mgdl = 0\n")
        self.assertEqual(cfg.polling.rearm_margin_mgdl, 0.0)

    def test_a_repeat_faster_than_the_sensor_is_rejected(self):
        # A new reading only arrives about once a minute, so anything
        # shorter would re-announce the same one.
        with self.assertRaises(ValueError) as caught:
            self.load("\n[polling]\nrepeat_every_min = 0.5\n")
        self.assertIn("repeat_every_min", str(caught.exception))

    def test_zero_repeat_means_off_and_is_allowed(self):
        cfg = self.load("\n[polling]\nrepeat_every_min = 0\n")
        self.assertEqual(cfg.polling.repeat_every_min, 0.0)

    def test_a_sound_path_must_be_a_wav(self):
        with self.assertRaises(ValueError) as caught:
            self.load(f'\n[polling]\nsound_path = "alert.mp3"\n')
        self.assertIn(".wav", str(caught.exception))

    def test_a_missing_sound_file_is_caught_at_load(self):
        # Not at the moment a low arrives, which is the one time a
        # typo in this path must not be what goes wrong.
        with self.assertRaises(ValueError) as caught:
            self.load(f'\n[polling]\nsound_path = "nowhere.wav"\n')
        self.assertIn("does not exist", str(caught.exception))

    def test_a_sound_file_that_is_there_is_accepted(self):
        wav = self.path.parent / "beep.wav"
        wav.write_bytes(b"RIFF")
        body = f'\n[polling]\nsound_path = "{wav.as_posix()}"\n'
        # Kept as written, not normalised: it is handed to PlaySound
        # verbatim, and TOML needs forward slashes on Windows anyway
        # because a backslash is an escape inside a basic string.
        self.assertEqual(self.load(body).polling.sound_path, wav.as_posix())

    def test_exactly_thirty_seconds_is_allowed(self):
        cfg = self.load("\n[polling]\ninterval_sec = 30\n")
        self.assertEqual(cfg.polling.interval_sec, 30.0)


class UnknownKeys(ConfigTestCase):
    """A key nothing reads is an error rather than a shrug.

    Every setting is loaded with `.get(key, default)`, which cannot tell
    absent from misspelled or misfiled, so without this check the file
    accepts anything and the setting simply does not happen. The rows
    below are the ways that was reachable: right key wrong section, no
    section at all, misspelled key, misspelled section.
    """

    def test_a_key_in_the_wrong_section_is_rejected(self):
        with self.assertRaises(ValueError) as caught:
            self.load("\n[display]\nwindow_min = 30\n")
        message = str(caught.exception)
        self.assertIn("display.window_min", message)
        # And says where it should have gone, since the point of
        # failing is to save the reader working that out.
        self.assertIn("[graph]", message)

    def test_a_key_two_sections_shared_would_name_both_of_them(self):
        # Nothing is in two sections now -- `window_min` was, in [trend]
        # and [graph], until the arrow stopped having a window -- but
        # "how far back" is exactly the kind of key that gets added to a
        # second section, and naming only the first would send half the
        # people who misfiled it to the wrong place.
        self.assertEqual(config_mod._sections(["graph", "trend"]), "[graph] or [trend]")

    def test_a_threshold_in_the_wrong_section_is_rejected(self):
        # The case this is really for: silently keeping 70.0 here means
        # an alert that does not fire when the user thinks it will.
        with self.assertRaises(ValueError) as caught:
            self.load("\n[display]\nlow_mgdl = 500\n")
        self.assertIn("[thresholds]", str(caught.exception))

    def test_a_key_outside_any_section_is_rejected(self):
        with self.assertRaises(ValueError) as caught:
            self.load(account="window_min = 30\n" + ACCOUNT)
        message = str(caught.exception)
        self.assertIn("window_min", message)
        self.assertIn("[graph]", message)

    def test_a_misspelled_key_is_rejected_with_the_spelling_meant(self):
        with self.assertRaises(ValueError) as caught:
            self.load("\n[graph]\nwindowmin = 30\n")
        message = str(caught.exception)
        self.assertIn("graph.windowmin", message)
        self.assertIn("window_min", message)

    def test_a_misspelled_section_is_rejected(self):
        with self.assertRaises(ValueError) as caught:
            self.load("\n[trends]\nlocal = false\n")
        message = str(caught.exception)
        self.assertIn("[trends]", message)
        self.assertIn("did you mean [trend]", message)

    def test_a_key_nothing_resembles_is_told_what_the_section_takes(self):
        # The branch with no other clue in it: not a typo of anything and
        # not filed in the wrong place, so naming it and stopping would
        # leave the reader to go and find the list themselves.
        with self.assertRaises(ValueError) as caught:
            self.load("\n[polling]\nnonsense = 1\n")
        message = str(caught.exception)
        self.assertIn("polling.nonsense", message)
        self.assertIn("interval_sec", message)
        self.assertIn("alert_on_low", message)

    def test_a_section_nothing_resembles_is_told_what_the_sections_are(self):
        with self.assertRaises(ValueError) as caught:
            self.load("\n[whatever]\nx = 1\n")
        message = str(caught.exception)
        self.assertIn("[whatever]", message)
        self.assertIn("[thresholds]", message)

    def test_a_stray_key_nothing_resembles_says_so(self):
        with self.assertRaises(ValueError) as caught:
            self.load(account="nonsense = 1\n" + ACCOUNT)
        self.assertIn("not a setting in any of them", str(caught.exception))

    def test_every_unknown_key_is_named_at_once(self):
        # Fixing one and being stopped by the next is the worst way to
        # hand back a list of typos.
        with self.assertRaises(ValueError) as caught:
            self.load("\n[polling]\nnonsense = 1\nalso_nonsense = 2\n")
        message = str(caught.exception)
        self.assertIn("polling.nonsense", message)
        self.assertIn("polling.also_nonsense", message)

    def test_the_account_section_is_checked_like_the_others(self):
        # It has no exemption. One rule beats a section that has to be
        # remembered as the odd one out.
        with self.assertRaises(ValueError) as caught:
            self.load(account=ACCOUNT + 'extra_thing = "whatever"\n')
        self.assertIn("account.extra_thing", str(caught.exception))

    def test_a_misspelled_account_key_is_caught_at_startup(self):
        # `api_verison` keeping the default would otherwise surface as a
        # login the API rejects, hours later, with nothing pointing back
        # at the file.
        with self.assertRaises(ValueError) as caught:
            self.load(account=ACCOUNT + 'api_verison = "4.16.0"\n')
        self.assertIn("api_version", str(caught.exception))

    def test_a_correct_account_section_loads(self):
        cfg = self.load(account=ACCOUNT + 'region = "jp"\n')
        self.assertEqual(cfg.account.region, "jp")

    def test_a_vr_key_left_in_display_is_sent_to_its_new_section(self):
        # The migration message. These keys were in [display] until the
        # sections were split, so a config.toml written before that is
        # full of them -- and each has to name where it goes rather than
        # only be refused, since the error is the list of what to move.
        with self.assertRaises(ValueError) as caught:
            self.load("\n[display]\nhand = 'right'\n")
        message = str(caught.exception)
        self.assertIn("display.hand", message)
        self.assertIn("[vr]", message)

    def test_the_two_sections_are_read_side_by_side(self):
        # One dataclass each now, and neither may swallow the other's
        # keys.
        cfg = self.load("\n[display]\nunit = 'mmol'\n\n[vr]\nhand = 'right'\n")
        self.assertEqual(cfg.display.unit, "mmol")
        self.assertEqual(cfg.vr.hand, "right")

    def test_the_example_config_is_fully_recognised(self):
        # The example is what a user copies. If it drifted from the
        # loader, the first thing they did would be to fail to start.
        example = Path(__file__).resolve().parents[1] / "config.example.toml"
        with example.open("rb") as fh:
            config_mod._check_keys(tomllib.load(fh))


class FieldKinds(unittest.TestCase):
    """The four types a setting may be declared as, and the guard on them.

    `load` and `save` both walk the annotations, and they can walk
    exactly these four. A fifth would load as whatever TOML happened to
    hand over and save as something else -- found by whoever hit it,
    rather than by whoever added it -- so it fails at import instead.
    """

    def test_every_setting_declared_is_one_of_the_four(self):
        # This runs at import as well. Here so it is a named failure
        # rather than every test in the suite erroring at once.
        config_mod._check_field_kinds()

    def test_the_table_and_the_key_check_are_the_same_table(self):
        self.assertEqual(set(config_mod.FIELD_TYPES), set(config_mod.SECTION_KEYS))
        for section, kinds in config_mod.FIELD_TYPES.items():
            with self.subTest(section):
                self.assertEqual(
                    config_mod.SECTION_KEYS[section], frozenset(kinds)
                )

    def test_the_sections_are_the_fields_of_config(self):
        # If these drifted, a whole section would stop being read while
        # its keys carried on being recognised.
        self.assertEqual(
            set(config_mod.SECTIONS),
            {f.name for f in fields(config_mod.Config)},
        )

    def test_an_unsupported_annotation_is_refused_by_name(self):
        with self.assertRaises(TypeError) as caught:
            config_mod._check_field_kinds({"polling": {"retries": int}})
        message = str(caught.exception)
        self.assertIn("polling.retries", message)

    def test_the_four_are_accepted(self):
        config_mod._check_field_kinds(
            {
                "made_up": {
                    "a": str,
                    "b": float,
                    "c": bool,
                    "d": config_mod.VECTOR3,
                }
            }
        )


class Saving(ConfigTestCase):
    """Writing a Config back into the file it was read from.

    Nothing calls `save` yet; it is what a settings window needs. What is
    asserted here is that it can be called on a real config.toml without
    the user losing anything -- their comments, their formatting, or the
    password, which is the one value in the file that cannot be worked
    out again.
    """

    def test_a_round_trip_changes_nothing(self):
        cfg = self.load(
            "\n[display]\nunit = 'mmol'\n"
            "\n[vr]\nhand = 'right'\noffset = [0.0, -0.02, 0.1]\n"
            "\n[polling]\ninterval_sec = 90\n"
        )
        config_mod.save(cfg, self.path)
        self.assertEqual(asdict(config_mod.load(self.path)), asdict(cfg))

    def test_saving_an_unedited_config_leaves_the_file_alone(self):
        # The strongest form of the anti-churn rule: a save that changed
        # nothing must write nothing, byte for byte.
        body = ACCOUNT + "\n[vr]\nhand = 'right'\nwidth_m = 0.2\n"
        self.path.write_text(body, encoding="utf-8")
        config_mod.save(config_mod.load(self.path), self.path)
        self.assertEqual(self.path.read_text(encoding="utf-8"), body)

    def test_comments_survive_a_save(self):
        # They are the documentation for anyone editing by hand, and
        # most of what they say is not repeated anywhere that person
        # will look.
        self.path.write_text(
            ACCOUNT + "\n# 90 because the sensor is slow here\n"
            "[polling]\ninterval_sec = 90\n",
            encoding="utf-8",
        )
        cfg = config_mod.load(self.path)
        cfg.polling.interval_sec = 120.0
        config_mod.save(cfg, self.path)
        text = self.path.read_text(encoding="utf-8")
        self.assertIn("# 90 because the sensor is slow here", text)
        self.assertIn("interval_sec = 120", text)

    def test_an_untouched_integer_is_not_churned_into_a_float(self):
        # Held as a float, written as `60`. Rewriting it as `60.0` on
        # every save would bury the one line that did change under a
        # diff of lines that did not.
        cfg = self.load("\n[polling]\ninterval_sec = 60\n")
        config_mod.save(cfg, self.path)
        self.assertIn("interval_sec = 60\n", self.path.read_text(encoding="utf-8"))

    def test_a_whole_number_stays_a_whole_number(self):
        # `low_mgdl = 70` nudged to 75 should read `75`. Every number
        # here is held as a float, so without this the settings window
        # would put a `.0` on every threshold it ever touched.
        cfg = self.load("\n[thresholds]\nlow_mgdl = 70\n")
        cfg.thresholds.low_mgdl = 75.0
        config_mod.save(cfg, self.path)
        self.assertIn("low_mgdl = 75\n", self.path.read_text(encoding="utf-8"))
        self.assertEqual(config_mod.load(self.path).thresholds.low_mgdl, 75.0)

    def test_a_fraction_is_still_written_as_one(self):
        # The rule above is about how a whole number is spelled, not
        # about rounding anything.
        cfg = self.load("\n[vr]\nwidth_m = 1\n")
        cfg.vr.width_m = 0.16
        config_mod.save(cfg, self.path)
        self.assertIn("width_m = 0.16", self.path.read_text(encoding="utf-8"))
        self.assertEqual(config_mod.load(self.path).vr.width_m, 0.16)

    def test_a_changed_setting_the_file_never_had_is_added(self):
        cfg = self.load()
        cfg.thresholds.low_mgdl = 80.0
        config_mod.save(cfg, self.path)
        text = self.path.read_text(encoding="utf-8")
        self.assertIn("[thresholds]", text)
        self.assertEqual(config_mod.load(self.path).thresholds.low_mgdl, 80.0)

    def test_a_default_the_file_never_had_is_left_out(self):
        # Changing one threshold must not paste all fifty settings into
        # a file that was holding six.
        cfg = self.load()
        cfg.window.scale = 2.0
        config_mod.save(cfg, self.path)
        text = self.path.read_text(encoding="utf-8")
        self.assertIn("scale = 2", text)
        self.assertNotIn("always_on_top", text)
        self.assertNotIn("[vr]", text)

    def test_placement_goes_back_as_an_array(self):
        # TOML has no tuple, so the one compound kind has to survive the
        # trip out and back.
        cfg = self.load()
        cfg.vr.offset = (0.0, -0.02, 0.12)
        config_mod.save(cfg, self.path)
        self.assertIn("offset = [", self.path.read_text(encoding="utf-8"))
        self.assertEqual(config_mod.load(self.path).vr.offset, (0.0, -0.02, 0.12))

    def test_the_password_survives(self):
        cfg = self.load()
        cfg.display.unit = "mmol"
        config_mod.save(cfg, self.path)
        self.assertEqual(config_mod.load(self.path).account.password, "secret")

    def test_a_config_the_loader_would_refuse_is_not_written(self):
        # save() validates first. Writing an invalid file would leave a
        # config.toml the app cannot start from, discovered the next
        # morning by which time nobody remembers typing it.
        cfg = self.load()
        before = self.path.read_text(encoding="utf-8")
        cfg.polling.interval_sec = 5.0
        with self.assertRaises(ValueError):
            config_mod.save(cfg, self.path)
        self.assertEqual(self.path.read_text(encoding="utf-8"), before)

    def test_a_file_that_is_not_there_yet_is_written_from_nothing(self):
        target = Path(self._dir.name) / "fresh.toml"
        cfg = config_mod.Config()
        cfg.account.email = "someone@example.com"
        cfg.account.password = "secret"
        config_mod.save(cfg, target)
        self.assertEqual(asdict(config_mod.load(target)), asdict(cfg))

    def test_the_temporary_file_does_not_survive(self):
        # The write lands beside the real file so the move is atomic.
        # What must not happen is a config.toml.new left in the checkout.
        cfg = self.load()
        cfg.thresholds.low_mgdl = 80.0
        config_mod.save(cfg, self.path)
        self.assertEqual(
            sorted(p.name for p in Path(self._dir.name).iterdir()),
            [self.path.name],
        )


if __name__ == "__main__":
    unittest.main()
