"""One running copy per config file.

The lock is a Windows named mutex, and a name answers the same inside
one process as across two, so the whole of it can be asserted here.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

from cgm.core.instance import claim, lock_name


class InstanceTestCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.config = Path(tmp.name) / "config.toml"

    def hold(self, config: Path):
        lock = claim(config)
        if lock is not None:
            self.addCleanup(lock.release)
        return lock


@unittest.skipUnless(sys.platform == "win32", "the lock is a Windows mutex")
class Claiming(InstanceTestCase):
    def test_the_first_copy_runs(self):
        self.assertIsNotNone(self.hold(self.config))

    def test_a_second_copy_on_the_same_config_does_not(self):
        self.hold(self.config)
        self.assertIsNone(self.hold(self.config))

    def test_a_copy_on_another_config_does(self):
        # Another config is another account, which is two people
        # followed rather than one followed twice.
        self.hold(self.config)
        self.assertIsNotNone(self.hold(self.config.with_name("other.toml")))

    def test_the_name_comes_back_when_the_first_copy_ends(self):
        self.hold(self.config).release()
        self.assertIsNotNone(self.hold(self.config))


class Naming(InstanceTestCase):
    def test_one_file_has_one_name_whatever_its_case(self):
        # Windows paths are not case sensitive, so the same file spelled
        # two ways must not be allowed to run twice.
        upper = Path(str(self.config).upper())
        self.assertEqual(lock_name(self.config), lock_name(upper))

    def test_a_relative_path_names_the_file_it_resolves_to(self):
        self.assertEqual(lock_name(Path("config.toml")), lock_name(Path.cwd() / "config.toml"))

    def test_the_name_stays_in_this_sign_in_and_has_no_path_in_it(self):
        # A backslash after the prefix would be read as a namespace.
        name = lock_name(self.config)
        self.assertTrue(name.startswith("Local\\"))
        self.assertNotIn("\\", name.removeprefix("Local\\"))


if __name__ == "__main__":
    unittest.main()
