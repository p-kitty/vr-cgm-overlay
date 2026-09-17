"""The first run: when to ask for an account, and what asking writes.

The window itself is `tools/check_signin.py`. What is here is every
decision under it, with a stand-in for the LibreLinkUp client -- the real
one is never called, for the reason tests/ never mocks HTTP: the only
risk worth having there is the live API, and `--dry-run` is what sees it.
"""

from __future__ import annotations

import io
import logging
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path
from unittest import mock

from cgm.core import config as config_mod
from cgm.core import firstrun, paths
from cgm.core.librelink import AuthError, LibreLinkError
from cgm.main import main

# This repository's, not `paths.CHECKOUT`'s. CI tests a regular install,
# where the package sits in site-packages and has no checkout above it.
EXAMPLE = Path(__file__).resolve().parents[1] / "config.example.toml"


class TempDir(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        self.path = self.dir / "config.toml"

    def write(self, text: str) -> Path:
        self.path.write_text(text, encoding="utf-8")
        return self.path


class NeedsAccount(TempDir):
    def test_no_file_is_a_first_run(self):
        self.assertTrue(firstrun.needs_account(self.path))

    def test_the_example_as_copied_is_a_first_run(self):
        # The README's own instructions produce exactly this file.
        self.write(EXAMPLE.read_text(encoding="utf-8"))
        self.assertTrue(firstrun.needs_account(self.path))

    def test_a_filled_in_account_is_not(self):
        self.write('[account]\nemail = "a@b.c"\npassword = "pw"\n')
        self.assertFalse(firstrun.needs_account(self.path))

    def test_an_email_with_no_password_is(self):
        self.write('[account]\nemail = "a@b.c"\n')
        self.assertTrue(firstrun.needs_account(self.path))

    def test_a_broken_file_is_reported_rather_than_asked_over(self):
        # A typo in a file somebody has been editing is not a first run,
        # and a sign-in window would hide what is actually wrong.
        self.write("[account\nemail = ")
        self.assertFalse(firstrun.needs_account(self.path))


class SavedEmail(TempDir):
    def test_the_address_in_the_file_fills_the_box(self):
        self.write('[account]\nemail = "a@b.c"\n')
        self.assertEqual(firstrun.saved_email(self.path), "a@b.c")

    def test_the_placeholder_does_not(self):
        self.write(EXAMPLE.read_text(encoding="utf-8"))
        self.assertEqual(firstrun.saved_email(self.path), "")

    def test_no_file_is_an_empty_box(self):
        self.assertEqual(firstrun.saved_email(self.path), "")


def client_raising(exc: Exception | None):
    """A client class whose fetch raises `exc`, or returns when None."""

    class Client:
        seen: list[tuple[str, str]] = []

        def __init__(self, email, password):
            Client.seen.append((email, password))

        def get_latest(self):
            if exc is not None:
                raise exc

    return Client


class TryAccount(unittest.TestCase):
    def test_a_reading_means_it_worked(self):
        client = client_raising(None)
        self.assertIsNone(firstrun.try_account(" a@b.c ", "pw", client_class=client))
        # Stripped: a pasted address often carries a space.
        self.assertEqual(client.seen, [("a@b.c", "pw")])

    def test_an_empty_box_is_not_sent(self):
        client = client_raising(None)
        self.assertIsNotNone(firstrun.try_account("a@b.c", "", client_class=client))
        self.assertEqual(client.seen, [])

    def test_a_refused_sign_in_points_at_the_app_mix_up(self):
        problem = firstrun.try_account(
            "a@b.c", "pw", client_class=client_raising(AuthError("no token"))
        )
        self.assertIn("LibreLinkUp app, not LibreLink", problem)
        self.assertIn("no token", problem)

    def test_following_nobody_says_what_the_client_said(self):
        problem = firstrun.try_account(
            "a@b.c",
            "pw",
            client_class=client_raising(LibreLinkError("no connections found")),
        )
        self.assertIn("no connections found", problem)

    def test_no_network_is_a_sentence_not_a_traceback(self):
        problem = firstrun.try_account(
            "a@b.c", "pw", client_class=client_raising(ConnectionError("down"))
        )
        self.assertIn("internet connection", problem)


class WriteAccount(TempDir):
    def test_a_new_file_starts_from_the_example_and_loads(self):
        path = self.dir / "vr-cgm-overlay" / "config.toml"
        firstrun.write_account(path, "a@b.c", "pw", example=EXAMPLE)
        cfg = config_mod.load(path)
        self.assertEqual((cfg.account.email, cfg.account.password), ("a@b.c", "pw"))
        # The comments are the reason it starts from the example at all.
        self.assertIn("# LibreLinkUp password.", path.read_text(encoding="utf-8"))

    def test_an_existing_file_keeps_everything_else(self):
        self.write(
            '# mine\n[account]\nemail = ""\npassword = ""\n\n[display]\nunit = "mmol"\n'
        )
        firstrun.write_account(self.path, "a@b.c", "pw", example=EXAMPLE)
        self.assertIn("# mine", self.path.read_text(encoding="utf-8"))
        self.assertEqual(config_mod.load(self.path).display.unit, "mmol")

    def test_with_no_example_it_still_writes_a_config_that_starts(self):
        firstrun.write_account(self.path, "a@b.c", "pw", example=self.dir / "missing")
        self.assertEqual(config_mod.load(self.path).account.email, "a@b.c")

    def test_a_file_the_app_would_refuse_is_left_as_it_was(self):
        before = '[account]\nemail = ""\n\n[polling]\ninterval_sec = 5\n'
        self.write(before)
        with self.assertRaises(ValueError):
            firstrun.write_account(self.path, "a@b.c", "pw", example=EXAMPLE)
        self.assertEqual(self.path.read_text(encoding="utf-8"), before)
        self.assertEqual([p.name for p in self.dir.iterdir()], ["config.toml"])


class ExampleConfig(unittest.TestCase):
    def test_a_checkout_uses_its_own(self):
        self.assertEqual(
            paths.example_config(frozen=False), paths.CHECKOUT / "config.example.toml"
        )
        with EXAMPLE.open("rb") as fh:
            tomllib.load(fh)

    def test_the_build_looks_at_the_top_of_the_bundle(self):
        with mock.patch.object(sys, "_MEIPASS", r"C:\app\_internal", create=True):
            self.assertEqual(
                paths.example_config(frozen=True),
                Path(r"C:\app\_internal") / "config.example.toml",
            )


class Main(TempDir):
    """main() with the logger and the streams put back as it found them."""

    def setUp(self):
        super().setUp()
        handlers, level = logging.root.handlers[:], logging.root.level
        streams = sys.stdout, sys.stderr

        def restore():
            logging.root.handlers[:] = handlers
            logging.root.setLevel(level)
            sys.stdout, sys.stderr = streams

        self.addCleanup(restore)
        sys.stdout, sys.stderr = io.StringIO(), io.StringIO()

    def main(self, *args: str) -> int:
        return main(["--config", str(self.path), *args])

    def test_closing_the_sign_in_ends_the_run_quietly(self):
        with (
            mock.patch("cgm.main.ask_for_account", return_value=False) as ask,
            mock.patch("cgm.main.run") as run,
        ):
            self.assertEqual(self.main("--window"), 0)
        ask.assert_called_once_with(self.path)
        run.assert_not_called()
        self.assertEqual(sys.stderr.getvalue(), "")

    def test_a_saved_account_goes_on_to_run(self):
        def saved(path):
            firstrun.write_account(path, "a@b.c", "pw", example=EXAMPLE)
            return True

        with (
            mock.patch("cgm.main.ask_for_account", side_effect=saved),
            mock.patch("cgm.main.log_to_file"),
            mock.patch("cgm.main.log_uncaught"),
            mock.patch("cgm.main.run", return_value=0) as run,
        ):
            self.assertEqual(self.main("--window"), 0)
        run.assert_called_once()
        self.assertEqual(run.call_args.args[0].account.email, "a@b.c")

    def test_an_account_already_there_is_not_asked_for(self):
        self.write('[account]\nemail = "a@b.c"\npassword = "pw"\n')
        with (
            mock.patch("cgm.main.ask_for_account") as ask,
            mock.patch("cgm.main.log_to_file"),
            mock.patch("cgm.main.log_uncaught"),
            mock.patch("cgm.main.run", return_value=0),
        ):
            self.main("--window")
        ask.assert_not_called()

    def test_dry_run_is_told_rather_than_asked(self):
        with mock.patch("cgm.main.ask_for_account") as ask:
            self.assertEqual(self.main("--dry-run"), 2)
        ask.assert_not_called()
        self.assertIn("not found", sys.stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
