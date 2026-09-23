"""Which config.toml a run falls back on, from a checkout and from a build."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

from cgm.core import paths


class DefaultConfig(unittest.TestCase):
    def test_a_checkout_keeps_it_beside_the_example(self):
        # Where it has always been, so the development setup is left as
        # it was -- and APPDATA has no say in it.
        found = paths.default_config(frozen=False, appdata=r"C:\Users\x\AppData\Roaming")
        self.assertEqual(found, paths.CHECKOUT / "config.toml")

    def test_the_checkout_is_the_top_of_the_source_tree(self):
        # src/cgm/core/paths.py, three folders up. Checked from the
        # module's own path rather than by looking for config.example.toml
        # there: CI tests a regular install, from site-packages, where
        # there is no checkout above the package to find.
        module = Path(paths.__file__).resolve()
        self.assertEqual(paths.CHECKOUT, module.parents[3])
        self.assertEqual(module.parent.name, "core")

    def test_the_bundled_app_keeps_it_under_appdata(self):
        # Not inside the bundle, which the next release replaces whole.
        found = paths.default_config(frozen=True, appdata=r"C:\Users\x\AppData\Roaming")
        self.assertEqual(
            found, Path(r"C:\Users\x\AppData\Roaming") / "vr-cgm-overlay" / "config.toml"
        )

    def test_with_no_appdata_the_bundled_app_looks_beside_itself(self):
        found = paths.default_config(frozen=True, appdata="")
        self.assertEqual(found, Path(sys.executable).resolve().parent / "config.toml")

    def test_this_process_is_not_the_build(self):
        self.assertFalse(paths.is_frozen())


class Reveal(unittest.TestCase):
    def setUp(self):
        # A space in it, since that is where quoting goes wrong.
        made = self.enterContext(tempfile.TemporaryDirectory(prefix="a b "))
        self.folder = Path(made).resolve()

    def test_an_existing_file_is_selected_in_its_folder(self):
        config = self.folder / "config.toml"
        config.write_text("", encoding="utf-8")
        # Quoted after the comma, which is the form Explorer understands.
        self.assertEqual(paths.reveal_command(config), f'explorer /select,"{config}"')

    def test_a_file_not_written_yet_opens_its_folder(self):
        config = self.folder / "config.toml"
        self.assertEqual(paths.reveal_command(config), f'explorer "{self.folder}"')

    def test_a_folder_not_made_yet_opens_the_nearest_that_is(self):
        config = self.folder / "vr-cgm-overlay" / "config.toml"
        self.assertEqual(paths.reveal_command(config), f'explorer "{self.folder}"')

    @unittest.skipUnless(sys.platform == "win32", "Explorer is Windows")
    def test_reveal_launches_that_command(self):
        config = self.folder / "config.toml"
        launched = []
        paths.reveal(config, launch=launched.append)
        self.assertEqual(launched, [paths.reveal_command(config)])


if __name__ == "__main__":
    unittest.main()
