"""Placement presets: one file per answer to where the face goes.

What has to hold is mostly about not losing somebody's placement. A
preset is a set of numbers found by nudging them with a headset on,
which is slow and fiddly, and every failure here that matters is one
where those numbers get silently replaced by another preset's.

`tools/check_settings.py` drives the same thing through a real window.
This file covers what can be asserted without one.
"""

from __future__ import annotations

import tempfile
import tomllib
import unittest
from pathlib import Path

from cgm.core import config as config_mod
from cgm.core import presets

ACCOUNT = '[account]\nemail = "someone@example.com"\npassword = "secret"\n'


class PresetTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.path = Path(self._dir.name) / "config.toml"
        self.folder = presets.directory(self.path)

    def config(self, vr: str = "") -> None:
        self.path.write_text(ACCOUNT + "\n[vr]\n" + vr, encoding="utf-8")

    def preset(self, name: str, body: str) -> Path:
        self.folder.mkdir(parents=True, exist_ok=True)
        target = self.folder / f"{name}.toml"
        target.write_text("[vr]\n" + body, encoding="utf-8")
        return target

    def load(self):
        return config_mod.load(self.path)


class Loading(PresetTestCase):
    def test_no_preset_reads_no_file(self):
        # What every config did before presets existed, and still the
        # default. A presets directory lying around must not matter.
        self.config("offset = [0.0, 0.02, 0.1]\n")
        self.preset("wrist", "offset = [9.0, 9.0, 9.0]\n")
        self.assertEqual(self.load().vr.offset, (0.0, 0.02, 0.1))

    def test_the_chosen_preset_wins_over_vr(self):
        self.config('preset = "wrist"\noffset = [0.0, 0.02, 0.1]\n')
        self.preset("wrist", "offset = [0.0, 0.0, 0.22]\norbit = true\n")
        cfg = self.load()
        self.assertEqual(cfg.vr.offset, (0.0, 0.0, 0.22))
        self.assertTrue(cfg.vr.orbit)

    def test_what_a_preset_leaves_out_comes_from_vr(self):
        # A preset only has to hold what it disagrees about.
        self.config('preset = "wrist"\nrotation_deg = [10.0, 20.0, 30.0]\n')
        self.preset("wrist", "offset = [0.0, 0.0, 0.22]\n")
        self.assertEqual(self.load().vr.rotation_deg, (10.0, 20.0, 30.0))

    def test_shared_settings_cannot_be_put_in_a_preset(self):
        # hand is your hardware, not a placement. Accepting it in a
        # preset file would make it silently per-preset for whoever
        # typed it there, and ignored for everyone reading config.toml.
        self.config('preset = "wrist"\n')
        self.preset("wrist", 'hand = "right"\n')
        with self.assertRaises(ValueError) as caught:
            self.load()
        self.assertIn("hand is not a preset setting", str(caught.exception))
        self.assertIn("stays in config.toml", str(caught.exception))

    def test_a_preset_holds_vr_and_nothing_else(self):
        self.config('preset = "wrist"\n')
        self.folder.mkdir(parents=True)
        (self.folder / "wrist.toml").write_text(
            "[thresholds]\nlow_mgdl = 80\n", encoding="utf-8"
        )
        with self.assertRaises(ValueError) as caught:
            self.load()
        self.assertIn("[thresholds]", str(caught.exception))

    def test_a_missing_preset_is_named(self):
        self.config('preset = "nowhere"\n')
        with self.assertRaises(FileNotFoundError) as caught:
            self.load()
        self.assertIn("nowhere.toml", str(caught.exception))

    def test_a_preset_is_validated_like_config_toml(self):
        # Validation runs after the preset is applied, so a preset cannot
        # smuggle in a value config.toml would have been refused for.
        self.config('preset = "wrist"\n')
        self.preset("wrist", "orbit_radius_m = -1.0\n")
        with self.assertRaises(ValueError) as caught:
            self.load()
        self.assertIn("orbit_radius_m", str(caught.exception))

    def test_a_badly_typed_preset_value_names_the_preset(self):
        self.config('preset = "wrist"\n')
        self.preset("wrist", 'width_m = "wide"\n')
        with self.assertRaises(ValueError) as caught:
            self.load()
        self.assertIn("preset wrist", str(caught.exception))


class Names(PresetTestCase):
    def test_a_name_cannot_leave_the_presets_folder(self):
        # The name becomes a path. "../config" would write over the file
        # holding the password.
        for name in ("../config", "..", "a/b", "a\\b", ".hidden", "", "x" * 65):
            with self.subTest(name=name), self.assertRaises(ValueError):
                presets.check_name(name)

    def test_ordinary_names_are_fine(self):
        for name in ("wrist", "hand", "Wrist-2", "seated_v1", "a.b"):
            with self.subTest(name=name):
                presets.check_name(name)

    def test_config_toml_cannot_name_an_unsafe_preset(self):
        self.config('preset = "../config"\n')
        with self.assertRaises(ValueError):
            self.load()

    def test_available_lists_the_files_and_skips_what_cannot_be_chosen(self):
        self.preset("wrist", "")
        self.preset("hand", "")
        self.folder.joinpath(".hidden.toml").write_text("", encoding="utf-8")
        self.folder.joinpath("notes.txt").write_text("", encoding="utf-8")
        self.assertEqual(presets.available(self.path), ["hand", "wrist"])

    def test_no_folder_means_no_presets(self):
        self.assertEqual(presets.available(self.path), [])


class Saving(PresetTestCase):
    def test_saving_under_a_preset_writes_the_preset_file(self):
        self.config('preset = "wrist"\noffset = [0.0, 0.02, 0.1]\n')
        self.preset("wrist", "offset = [0.0, 0.0, 0.22]\n")

        cfg = self.load()
        cfg.vr.offset = (0.0, 0.01, 0.25)
        config_mod.save(cfg, self.path)

        preset = tomllib.loads((self.folder / "wrist.toml").read_text("utf-8"))
        self.assertEqual(preset["vr"]["offset"], [0.0, 0.01, 0.25])

    def test_and_leaves_config_toml_alone_for_those_keys(self):
        # Two answers to the same question, with the preset's silently
        # winning, is the failure the key check exists to prevent.
        self.config('preset = "wrist"\noffset = [0.0, 0.02, 0.1]\n')
        self.preset("wrist", "offset = [0.0, 0.0, 0.22]\n")

        cfg = self.load()
        cfg.vr.offset = (0.0, 0.01, 0.25)
        config_mod.save(cfg, self.path)

        own = tomllib.loads(self.path.read_text("utf-8"))
        self.assertEqual(own["vr"]["offset"], [0.0, 0.02, 0.1])

    def test_shared_settings_still_go_to_config_toml(self):
        self.config('preset = "wrist"\n')
        self.preset("wrist", "")

        cfg = self.load()
        cfg.vr.hand = "right"
        config_mod.save(cfg, self.path)

        own = tomllib.loads(self.path.read_text("utf-8"))
        self.assertEqual(own["vr"]["hand"], "right")
        preset = tomllib.loads((self.folder / "wrist.toml").read_text("utf-8"))
        self.assertNotIn("hand", preset["vr"])

    def test_without_a_preset_nothing_is_written_to_presets(self):
        self.config("")
        cfg = self.load()
        cfg.vr.offset = (0.0, 0.01, 0.25)
        config_mod.save(cfg, self.path)
        self.assertFalse(self.folder.exists())

    def test_a_new_preset_file_gets_the_header(self):
        # The only place the file says what it is for, to whoever opens
        # it in a text editor having never heard of presets.
        self.config('preset = "wrist"\n')
        self.folder.mkdir(parents=True)
        (self.folder / "wrist.toml").write_text("[vr]\n", encoding="utf-8")
        cfg = self.load()
        (self.folder / "wrist.toml").unlink()
        config_mod.save(cfg, self.path)
        text = (self.folder / "wrist.toml").read_text("utf-8")
        self.assertIn('preset = "wrist"', text)
        self.assertIn("stay in", text)

    def test_comments_in_a_preset_file_survive(self):
        self.config('preset = "wrist"\n')
        self.preset("wrist", "# found at 3am, do not touch\noffset = [0.0, 0.0, 0.22]\n")
        cfg = self.load()
        cfg.vr.width_m = 0.12
        config_mod.save(cfg, self.path)
        self.assertIn(
            "found at 3am", (self.folder / "wrist.toml").read_text("utf-8")
        )


class Switching(PresetTestCase):
    """Changing which preset is live, without losing either of them."""

    def setUp(self) -> None:
        super().setUp()
        self.config('offset = [0.0, 0.02, 0.1]\n')
        self.preset("wrist", "offset = [0.0, 0.0, 0.22]\n")
        self.preset("hand", "offset = [0.0, 0.03, 0.08]\n")

    def offset_in(self, name: str) -> list:
        text = (self.folder / f"{name}.toml").read_text("utf-8")
        return tomllib.loads(text)["vr"]["offset"]

    def test_switching_does_not_overwrite_the_preset_switched_to(self):
        # The bug this whole class is here for. The first version of the
        # window switched by loading, setting vr.preset and saving, and
        # save writes the loaded placement into the named preset. The
        # loaded placement was the *old* one, so choosing "wrist" poured
        # the previous placement over wrist.toml and destroyed it.
        config_mod.select_preset(self.path, "wrist")
        self.assertEqual(self.offset_in("wrist"), [0.0, 0.0, 0.22])

        config_mod.select_preset(self.path, "hand")
        self.assertEqual(self.offset_in("hand"), [0.0, 0.03, 0.08])
        self.assertEqual(self.offset_in("wrist"), [0.0, 0.0, 0.22])

    def test_switching_changes_what_loads(self):
        cfg = config_mod.select_preset(self.path, "wrist")
        self.assertEqual(cfg.vr.offset, (0.0, 0.0, 0.22))
        self.assertEqual(self.load().vr.offset, (0.0, 0.0, 0.22))

    def test_switching_to_none_keeps_the_key_visible(self):
        # Empty rather than removed: the file is where settings are
        # found, and one that has vanished cannot be found again.
        config_mod.select_preset(self.path, "wrist")
        config_mod.select_preset(self.path, "")
        own = tomllib.loads(self.path.read_text("utf-8"))
        self.assertEqual(own["vr"]["preset"], "")
        self.assertEqual(self.load().vr.offset, (0.0, 0.02, 0.1))

    def test_save_refuses_to_switch(self):
        # The same mistake, made by some future caller rather than the
        # window. Loud rather than a silent overwrite.
        cfg = self.load()
        cfg.vr.preset = "wrist"
        with self.assertRaises(ValueError) as caught:
            config_mod.save(cfg, self.path)
        self.assertIn("select_preset", str(caught.exception))
        self.assertEqual(self.offset_in("wrist"), [0.0, 0.0, 0.22])

    def test_a_preset_that_will_not_load_leaves_the_old_choice(self):
        # Kept, rather than a config.toml the app will not start from.
        config_mod.select_preset(self.path, "wrist")
        self.preset("broken", "orbit_radius_m = -1.0\n")
        before = self.path.read_text("utf-8")

        with self.assertRaises(ValueError):
            config_mod.select_preset(self.path, "broken")
        self.assertEqual(self.path.read_text("utf-8"), before)
        self.assertEqual(self.load().vr.preset, "wrist")

    def test_a_preset_that_is_not_there_leaves_the_old_choice(self):
        before = self.path.read_text("utf-8")
        with self.assertRaises(FileNotFoundError):
            config_mod.select_preset(self.path, "nowhere")
        self.assertEqual(self.path.read_text("utf-8"), before)

    def test_an_unsafe_name_is_refused_before_anything_is_written(self):
        before = self.path.read_text("utf-8")
        with self.assertRaises(ValueError):
            config_mod.select_preset(self.path, "../config")
        self.assertEqual(self.path.read_text("utf-8"), before)


class Keys(unittest.TestCase):
    def test_every_preset_key_is_a_real_setting(self):
        # Checked at import too; this says so where a reader will look.
        self.assertLessEqual(
            presets.PRESET_KEYS, set(config_mod.FIELD_TYPES[presets.SECTION])
        )

    def test_the_selector_is_not_itself_a_preset_key(self):
        self.assertNotIn(presets.SELECTOR, presets.PRESET_KEYS)

    def test_hardware_and_eyes_stay_shared(self):
        # The line PRESET_KEYS draws, and the one thing in the module
        # worth arguing about. Moving any of these across should be a
        # decision, and this is where it gets noticed.
        for key in ("hand", "opacity", "arm_guide", "gaze_fade"):
            with self.subTest(key):
                self.assertNotIn(key, presets.PRESET_KEYS)


if __name__ == "__main__":
    unittest.main()
