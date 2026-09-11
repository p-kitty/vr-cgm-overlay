"""Starting with Windows, and the dialog that stands in for a console.

The shortcut is written for real, by the shell, into a scratch folder
rather than the user's own Startup folder, and read back the same way:
a .lnk is only ever what the shell says it is. `cgm.main`'s side is
driven with the shortcut and the dialog stood in for.
"""

from __future__ import annotations

import io
import logging
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from cgm.core import startup
from cgm.core.instance import claim
from cgm.main import main, report

CONFIG = '[account]\nemail = "a@b.c"\npassword = "secret"\n'

# Read back through Shell.Application rather than through the module's
# own code, so a mistake made the same way on both sides cannot pass --
# and not through WScript.Shell, which loses the same characters on the
# way out that it used to lose on the way in. The paths go across in
# environment variables so none of them has to survive being quoted.
_READ = (
    "[Console]::OutputEncoding = [Text.Encoding]::UTF8;"
    "$f = (New-Object -ComObject Shell.Application).NameSpace($env:CGM_DIR).ParseName($env:CGM_NAME);"
    "$s = $f.GetLink; $s.Path; $s.Arguments; $s.WorkingDirectory"
)


@unittest.skipUnless(sys.platform == "win32", "the Startup folder is Windows")
class Installing(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        # Japanese, as in the checkout this was written in, beside Hangul
        # and Polish. The shortcut once went through the ANSI code page,
        # and no single code page holds all three, so this fails on every
        # machine if it ever does again -- not only on the ones whose
        # code page happens not to be Japanese.
        root = Path(tmp.name)
        self.folder = root / "スタートアップ-시작"
        self.config = root / "設定-설정-ąę" / "config.toml"

    def read(self, link: Path) -> list[str]:
        """Target, arguments and working directory, as the shell has them."""
        done = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", _READ],
            env={**os.environ, "CGM_DIR": str(link.parent), "CGM_NAME": link.name},
            capture_output=True,
            encoding="utf-8",
            timeout=60,
        )
        self.assertEqual(done.returncode, 0, done.stderr)
        return done.stdout.splitlines()

    def test_nothing_is_there_to_begin_with(self):
        self.assertIsNone(startup.registered(folder=self.folder))

    def test_the_shortcut_runs_pythonw_on_the_config(self):
        # pythonw, or a sign-in opens a console window beside the face.
        link = startup.install(self.config, folder=self.folder)
        self.assertEqual(startup.registered(folder=self.folder), link)
        target, args, workdir = self.read(link)
        self.assertEqual(Path(target), startup.pythonw())
        self.assertEqual(args, startup.arguments(self.config))
        self.assertEqual(Path(workdir), self.config.parent)

    def test_installing_again_replaces_rather_than_adds(self):
        # It is how a moved checkout or another config gets pointed at.
        startup.install(self.config, folder=self.folder)
        other = self.config.with_name("other.toml")
        link = startup.install(other, folder=self.folder)
        self.assertEqual(list(self.folder.iterdir()), [link])
        self.assertIn(str(other.resolve()), self.read(link)[1])

    def test_uninstall_takes_it_away(self):
        startup.install(self.config, folder=self.folder)
        self.assertTrue(startup.uninstall(folder=self.folder))
        self.assertIsNone(startup.registered(folder=self.folder))

    def test_uninstalling_what_is_not_there_says_so(self):
        self.assertFalse(startup.uninstall(folder=self.folder))

    def test_a_shortcut_the_shell_refuses_is_an_oserror(self):
        # OSError is what --install-startup reports as a sentence;
        # anything else would reach the user as a traceback.
        with self.assertRaises(OSError):
            startup.save_link(
                self.folder / "missing" / startup.LINK_NAME,
                startup.pythonw(),
                "",
                self.folder,
            )


class Arguments(unittest.TestCase):
    def test_run_the_package_on_the_config_named_in_full_and_quoted(self):
        # A path with a space in it is two arguments unless it is quoted,
        # and a relative one would be relative to wherever Windows starts
        # the shortcut from.
        args = startup.arguments(Path("config.toml"))
        self.assertEqual(args, f'-m cgm --config "{Path("config.toml").resolve()}"')

    def test_the_interpreter_is_the_console_less_one_beside_this(self):
        scripts = Path(r"C:\apps\vr cgm\.venv\Scripts")
        self.assertEqual(
            startup.pythonw(scripts / "python.exe"), scripts / "pythonw.exe"
        )


class MainTestCase(unittest.TestCase):
    """main() with the root logger put back afterwards, as it found it."""

    def setUp(self):
        handlers = logging.root.handlers[:]
        level = logging.root.level
        self.addCleanup(self.restore, handlers, level)
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        self.config = self.dir / "config.toml"
        self.config.write_text(CONFIG, encoding="utf-8")
        self.err = io.StringIO()
        streams = sys.stdout, sys.stderr
        self.addCleanup(self.put_back, streams)
        sys.stdout, sys.stderr = io.StringIO(), self.err

    @staticmethod
    def restore(handlers, level):
        logging.root.handlers[:] = handlers
        logging.root.setLevel(level)

    @staticmethod
    def put_back(streams):
        sys.stdout, sys.stderr = streams

    def main(self, *args: str, config: Path | None = None) -> int:
        return main(["--config", str(config or self.config), *args])


class Flags(MainTestCase):
    def test_install_registers_the_config_it_was_run_with(self):
        with mock.patch("cgm.core.startup.install", return_value="cmd") as install:
            self.assertEqual(self.main("--install-startup"), 0)
        install.assert_called_once_with(self.config)

    def test_a_config_the_app_would_refuse_is_never_registered(self):
        with mock.patch("cgm.core.startup.install") as install:
            code = self.main("--install-startup", config=self.dir / "nowhere.toml")
        self.assertEqual(code, 2)
        install.assert_not_called()

    def test_uninstall_does_not_need_the_config_to_be_valid(self):
        # Taking a registration away must not depend on the file it was
        # registered with still loading.
        with mock.patch("cgm.core.startup.uninstall", return_value=True) as uninstall:
            code = self.main("--uninstall-startup", config=self.dir / "nowhere.toml")
        self.assertEqual(code, 0)
        uninstall.assert_called_once_with()


@unittest.skipUnless(sys.platform == "win32", "the lock is a Windows mutex")
class AlreadyRunning(MainTestCase):
    def test_a_second_copy_says_so_and_stops(self):
        lock = claim(self.config)
        self.addCleanup(lock.release)
        # Stopped before anything is opened: no window, no fetch, and no
        # second writer on the log file.
        with mock.patch("cgm.main.run") as run:
            self.assertEqual(self.main("--window"), 1)
        run.assert_not_called()
        self.assertIn("already running", self.err.getvalue())
        self.assertFalse((self.dir / "logs").exists())


class Reporting(unittest.TestCase):
    def setUp(self):
        streams = sys.stdout, sys.stderr
        self.addCleanup(MainTestCase.put_back, streams)
        # print(file=None) falls back to stdout, so that is caught too.
        sys.stdout = io.StringIO()

    def test_with_a_console_it_is_printed_and_nothing_pops_up(self):
        sys.stderr = io.StringIO()
        with mock.patch("cgm.desk.dialog.show_error") as show:
            report("config error: nope")
        self.assertIn("config error: nope", sys.stderr.getvalue())
        show.assert_not_called()

    def test_with_no_console_it_is_shown_in_a_dialog(self):
        # pythonw, which is how a sign-in starts the app: without this a
        # config error is a window that never appears.
        sys.stderr = None
        with mock.patch("cgm.desk.dialog.show_error") as show:
            report("config error: nope")
        show.assert_called_once_with("config error: nope")


if __name__ == "__main__":
    unittest.main()
