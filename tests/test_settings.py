"""The settings window's parts that do not need a window.

Tk is not started here, for the reason `tests/test_desk.py` gives: a
root would open a window on whoever runs the suite, and putting a
checkbox on a frame is something Tk either does or does not.

What is worth asserting is everything between the boxes and the file.
The form is built by walking `cgm.core.config.FIELD_TYPES`, so the
checks are about that walk staying honest -- every setting has a widget
it can be saved out of and lands on exactly one tab, every table keyed
by name names something real, and a value that goes into a box comes
back out of the file as what it was. Then the save itself: it must refuse rather than write a
config.toml the app would not start from, and it must leave alone
whatever it was not handed.
"""

from __future__ import annotations

import tempfile
import unittest
from dataclasses import asdict
from pathlib import Path

from cgm.core import config as config_mod
from cgm.desk import settings as settings_mod

ACCOUNT = '[account]\nemail = "someone@example.com"\npassword = "secret"\n'

NAMED = (
    ("HINTS", settings_mod.HINTS),
    ("CHOICES", settings_mod.CHOICES),
    ("SECRET", settings_mod.SECRET),
)

VECTOR = ("vr", "offset")


def names() -> set[str]:
    """Every `section.key` there is."""
    return {
        f"{section}.{key}"
        for section, keys in config_mod.FIELD_TYPES.items()
        for key in keys
    }


def boxes(cfg: config_mod.Config) -> dict[tuple[str, str], object]:
    """What the window's variables would hand back, for a whole config.

    One string per box, and three of them for a vector, which is what
    `Vector3Var.get` returns.
    """
    out: dict[tuple[str, str], object] = {}
    for section in settings_mod.sections():
        held = getattr(cfg, section)
        for key, kind in config_mod.FIELD_TYPES[section].items():
            value = getattr(held, key)
            out[(section, key)] = (
                [settings_mod.spell(part) for part in value]
                if kind == config_mod.VECTOR3
                else settings_mod.spell(value)
            )
    return out


class Offered(unittest.TestCase):
    def test_every_section_is_offered(self):
        # `[vr]` was left out at first, on the grounds that placement can
        # only be judged with the headset on. It still can -- and Save
        # here is the same loop as saving the file, so that was never a
        # reason to withhold it.
        self.assertEqual(settings_mod.sections(), list(config_mod.FIELD_TYPES))
        self.assertIn("vr", settings_mod.sections())

    def test_the_tabs_follow_the_config(self):
        # So the window and the file read in the same order.
        self.assertEqual(settings_mod.sections(), list(config_mod.FIELD_TYPES))

    def test_a_note_is_about_a_tab_that_exists(self):
        labels = {label for label, _section, _keys in settings_mod.tabs()}
        for label, note in settings_mod.NOTES.items():
            with self.subTest(label):
                self.assertIn(label, labels)
                self.assertTrue(note.strip())

    def test_placement_says_how_it_is_meant_to_be_used(self):
        # The tab a desk cannot judge on its own. Saying nothing would
        # leave somebody typing offsets and wondering why nothing looks
        # different on their monitor.
        note = settings_mod.NOTES["vr"]
        self.assertIn("headset", note)
        self.assertIn("placement.md", note)

    def test_every_setting_has_a_widget(self):
        # This runs at import too. Here so it fails by name rather than
        # by every test in the file erroring at once.
        settings_mod._check_shown_kinds()

    def test_a_kind_with_no_widget_is_refused(self):
        # What would otherwise happen: a box holding text nothing can
        # convert back, found by whoever pressed Save.
        with self.assertRaises(TypeError) as caught:
            settings_mod._check_shown_kinds({"polling": {"retries": int}})
        self.assertIn("polling.retries", str(caught.exception))

    def test_three_numbers_have_a_widget(self):
        settings_mod._check_shown_kinds({"vr": {"offset": config_mod.VECTOR3}})


class Tabs(unittest.TestCase):
    """The tabs, once the long section is carved up.

    A notebook is as tall as its tallest page, so `[vr]` -- fourteen
    settings against seven for the next largest -- was setting the height
    of the whole window. It is three tabs now, and what has to stay true
    is that carving them up did not lose a setting or show one twice.
    """

    def test_every_setting_is_on_exactly_one_tab(self):
        shown = [
            (section, key)
            for _label, section, keys in settings_mod.tabs()
            for key in keys
        ]
        self.assertEqual(len(shown), len(set(shown)), "a setting is on two tabs")
        self.assertEqual(set(shown), {tuple(n.split(".")) for n in names()})

    def test_a_section_that_is_not_carved_is_one_whole_tab(self):
        for label, section, keys in settings_mod.tabs():
            if section in settings_mod.GROUPS:
                continue
            with self.subTest(label):
                self.assertEqual(label, section)
                self.assertEqual(list(keys), list(config_mod.FIELD_TYPES[section]))

    def test_a_carved_section_keeps_its_own_tab_first(self):
        # The leftovers are computed rather than listed, so a setting
        # added to `[vr]` appears there without anybody editing GROUPS.
        labels = [label for label, _section, _keys in settings_mod.tabs()]
        for section, groups in settings_mod.GROUPS.items():
            with self.subTest(section):
                at = labels.index(section)
                self.assertEqual(
                    labels[at + 1 : at + 1 + len(groups)],
                    [label for label, _keys in groups],
                )
                self.assertTrue(
                    all(label.startswith(f"{section} ") for label, _keys in groups),
                    "a carved tab has to say which section it is part of",
                )

    def test_no_tab_is_taller_than_the_sections_nobody_carved(self):
        # The whole of what the split bought. Without this the window
        # goes back to being shaped by one section as soon as a fifteenth
        # setting lands in `[vr]`.
        room = max(
            len(config_mod.FIELD_TYPES[section])
            for section in settings_mod.sections()
            if section not in settings_mod.GROUPS
        )
        for label, _section, keys in settings_mod.tabs():
            with self.subTest(label):
                self.assertLessEqual(len(keys), room)

    def test_the_groups_are_checked(self):
        # Runs at import too, the same way the widget check does.
        settings_mod._check_groups()

    def test_a_group_naming_a_setting_that_does_not_exist_is_refused(self):
        # Otherwise it is quiet twice over: the key stays on the
        # section's own tab, and the carved tab reads a field the
        # dataclass has not got, while the window is being built.
        with self.assertRaises(KeyError) as caught:
            settings_mod._check_groups({"vr": (("vr orbit", ("orbut",)),)})
        self.assertIn("vr.orbut", str(caught.exception))

    def test_a_setting_on_two_tabs_is_refused(self):
        with self.assertRaises(KeyError) as caught:
            settings_mod._check_groups(
                {"vr": (("vr orbit", ("orbit",)), ("vr gaze", ("orbit",)))}
            )
        self.assertIn("vr.orbit", str(caught.exception))

    def test_carving_a_section_that_does_not_exist_is_refused(self):
        with self.assertRaises(KeyError):
            settings_mod._check_groups({"vrr": (("vrr orbit", ("orbit",)),)})


class Tables(unittest.TestCase):
    """The three tables keyed by setting name.

    A typo in one of them is invisible: the hint simply never appears,
    the dropdown is a text box, or the password is not masked. None of
    those look like a mistake from the outside.
    """

    def test_every_key_names_a_setting_that_exists(self):
        for label, table in NAMED:
            for name in table:
                with self.subTest(f"{label}[{name}]"):
                    self.assertIn(name, names())

    def test_no_key_names_a_section_that_is_not_offered(self):
        offered = set(settings_mod.sections())
        for label, table in NAMED:
            for name in table:
                with self.subTest(f"{label}[{name}]"):
                    self.assertIn(name.split(".")[0], offered)

    def test_the_password_is_masked(self):
        self.assertIn("account.password", settings_mod.SECRET)

    def test_every_choice_is_a_value_the_loader_takes(self):
        for name, options in settings_mod.CHOICES.items():
            section, _, key = name.partition(".")
            for option in options:
                with self.subTest(f"{name} = {option}"):
                    cfg = config_mod.Config()
                    cfg.account.email = "someone@example.com"
                    cfg.account.password = "secret"
                    setattr(
                        getattr(cfg, section),
                        key,
                        config_mod.parse(section, key, option),
                    )
                    config_mod._validate(cfg)

    def test_a_choice_lists_more_than_one(self):
        # A dropdown with one entry is a label that looks clickable.
        for name, options in settings_mod.CHOICES.items():
            with self.subTest(name):
                self.assertGreater(len(options), 1)


class Spelling(unittest.TestCase):
    def test_a_default_survives_the_trip_through_a_text_box(self):
        # Every setting has to come back out of its own box as what it
        # was, or opening the window and pressing Save would be an edit.
        cfg = config_mod.Config()
        for (section, key), shown in boxes(cfg).items():
            with self.subTest(f"{section}.{key}"):
                self.assertEqual(
                    config_mod.parse(section, key, shown),
                    getattr(getattr(cfg, section), key),
                )

    def test_a_whole_number_is_shown_without_a_decimal_point(self):
        # Held as a float. A box reading `70.0` where the file says `70`
        # invites somebody to fix it and save a diff that changes nothing.
        self.assertEqual(settings_mod.spell(70.0), "70")
        self.assertEqual(settings_mod.spell(0.14), "0.14")
        self.assertEqual(settings_mod.spell("mgdl"), "mgdl")
        self.assertEqual(settings_mod.spell(True), "True")


class Applying(unittest.TestCase):
    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.path = Path(self._dir.name) / "config.toml"

    def write(self, body: str = "") -> config_mod.Config:
        self.path.write_text(ACCOUNT + body, encoding="utf-8")
        return config_mod.load(self.path)

    def test_values_land_on_the_config(self):
        cfg = self.write()
        settings_mod.apply_values(
            cfg,
            {
                ("thresholds", "low_mgdl"): "80",
                ("display", "unit"): "mmol",
                ("polling", "alert_haptic"): False,
            },
        )
        self.assertEqual(cfg.thresholds.low_mgdl, 80.0)
        self.assertEqual(cfg.display.unit, "mmol")
        self.assertFalse(cfg.polling.alert_haptic)

    def test_a_box_holding_the_wrong_thing_names_its_setting(self):
        cfg = self.write()
        with self.assertRaises(ValueError) as caught:
            settings_mod.apply_values(cfg, {("thresholds", "low_mgdl"): "eighty"})
        self.assertIn("thresholds.low_mgdl", str(caught.exception))

    def test_what_is_not_handed_over_is_not_touched(self):
        # The window always passes every box, but a partial set has to
        # leave the rest as the file had it -- which is what keeps a save
        # from being a rewrite of everything.
        cfg = self.write("\n[vr]\nhand = 'right'\noffset = [0.0, -0.02, 0.12]\n")
        before = asdict(cfg.vr)
        settings_mod.apply_values(cfg, {("thresholds", "low_mgdl"): "80"})
        self.assertEqual(asdict(cfg.vr), before)

    def test_three_boxes_become_one_setting(self):
        # `Vector3Var.get` hands back three strings, which `parse` takes
        # because a TOML array arrives as a list of numbers the same way.
        cfg = self.write()
        settings_mod.apply_values(cfg, {VECTOR: ["0.0", "-0.02", "0.12"]})
        self.assertEqual(cfg.vr.offset, (0.0, -0.02, 0.12))

    def test_a_box_of_three_that_holds_a_word_names_its_setting(self):
        cfg = self.write()
        with self.assertRaises(ValueError) as caught:
            settings_mod.apply_values(cfg, {VECTOR: ["0.0", "up a bit", "0.12"]})
        self.assertIn("vr.offset", str(caught.exception))


class Saving(unittest.TestCase):
    """Pressing Save, without the button.

    `SettingsWindow.save` is these three lines plus the widgets: re-read
    the file, apply, write.
    """

    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.path = Path(self._dir.name) / "config.toml"
        self.path.write_text(
            ACCOUNT + "\n# tuned by hand\n[vr]\noffset = [0.0, -0.02, 0.12]\n",
            encoding="utf-8",
        )

    def press_save(self, values: dict[tuple[str, str], object]) -> None:
        cfg = config_mod.load(self.path)
        settings_mod.apply_values(cfg, values)
        config_mod.save(cfg, self.path)

    def test_what_the_boxes_held_is_in_the_file(self):
        self.press_save({("thresholds", "low_mgdl"): "80"})
        self.assertEqual(config_mod.load(self.path).thresholds.low_mgdl, 80.0)

    def test_a_section_the_save_did_not_mention_survives(self):
        self.press_save({("thresholds", "low_mgdl"): "80"})
        text = self.path.read_text(encoding="utf-8")
        self.assertIn("# tuned by hand", text)
        self.assertEqual(config_mod.load(self.path).vr.offset, (0.0, -0.02, 0.12))

    def test_placement_can_be_nudged_from_here(self):
        # The whole reason [vr] is offered: change a number, press Save,
        # and the watcher moves the face within the second.
        self.press_save({VECTOR: ["0.0", "-0.03", "0.11"]})
        self.assertEqual(config_mod.load(self.path).vr.offset, (0.0, -0.03, 0.11))
        self.assertIn("# tuned by hand", self.path.read_text(encoding="utf-8"))

    def test_a_placement_rule_still_holds(self):
        # `_validate` is the same one the file goes through, so the
        # window cannot save a gaze fade that steps instead of fading.
        with self.assertRaises(ValueError):
            self.press_save(
                {
                    ("vr", "gaze_full_deg"): "45",
                    ("vr", "gaze_fade_deg"): "45",
                }
            )

    def test_nothing_is_written_when_it_would_not_load(self):
        # A threshold above the one above it. Writing it would leave a
        # config.toml the app cannot start from -- found the next
        # morning, by which time nobody remembers typing it.
        before = self.path.read_text(encoding="utf-8")
        with self.assertRaises(ValueError):
            self.press_save(
                {
                    ("thresholds", "low_mgdl"): "200",
                    ("thresholds", "high_mgdl"): "180",
                }
            )
        self.assertEqual(self.path.read_text(encoding="utf-8"), before)

    def test_the_polling_floor_is_still_the_floor(self):
        # The one rule a settings window makes easiest to break: a
        # slider or a spinbox would walk straight past 30.
        with self.assertRaises(ValueError):
            self.press_save({("polling", "interval_sec"): "5"})
        self.assertEqual(config_mod.load(self.path).polling.interval_sec, 60.0)

    def test_saving_the_values_it_opened_with_changes_nothing(self):
        before = self.path.read_text(encoding="utf-8")
        self.press_save(boxes(config_mod.load(self.path)))
        self.assertEqual(self.path.read_text(encoding="utf-8"), before)


if __name__ == "__main__":
    unittest.main()
