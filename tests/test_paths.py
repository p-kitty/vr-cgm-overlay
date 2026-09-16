"""Which config.toml a run falls back on, from a checkout and from a build."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

from cgm.core import paths


class DefaultConfig(unittest.TestCase):
    def test_a_checkout_keeps_it_beside_the_example(self):
        # Where it has always been, so the development setup is left as
        # it was -- and APPDATA has no say in it.
        found = paths.default_config(frozen=False, appdata=r"C:\Users\x\AppData\Roaming")
        self.assertEqual(found, paths.CHECKOUT / "config.toml")
        self.assertTrue((found.parent / "config.example.toml").exists())

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


if __name__ == "__main__":
    unittest.main()
