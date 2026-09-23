"""Where config.toml lives when nobody says, which depends on how this runs.

Two ways to run the same code, and each has its own answer.

**From a checkout** -- `pip install -e .`, the way it is developed --
the config sits at the top of the checkout beside config.example.toml,
as it always has. Resolved from this module rather than the working
directory, so the command works from anywhere.

**As the bundled Windows app** -- PyInstaller's build, the way it is
handed to people who do not have Python -- there is no checkout. The
module's own path points inside the unpacked bundle, which is replaced
wholesale by the next release, so nothing kept there would survive an
update. The config goes under `%APPDATA%` instead, which is where
Windows expects a program's per-user settings and which an update does
not touch.

The two never share a file, deliberately: trying a build on the same
machine the code is developed on leaves the development config, its
log and its window position alone. `--config` still overrides either.

Wherever it ends up, `reveal` opens it in Explorer, because the second
of those places is one Explorer hides and nobody finds unprompted.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

CONFIG_NAME = "config.toml"
EXAMPLE_NAME = "config.example.toml"

# The folder under %APPDATA%. The project's name rather than a vendor
# folder over it: there is no vendor, and it is what someone looking for
# it will search for.
APP_DIR_NAME = "vr-cgm-overlay"

# src/cgm/core/paths.py -> the checkout root.
CHECKOUT = Path(__file__).resolve().parents[3]


def is_frozen() -> bool:
    """True inside the PyInstaller build, which sets `sys.frozen`."""
    return bool(getattr(sys, "frozen", False))


def default_config(*, frozen: bool | None = None, appdata: str | None = None) -> Path:
    """The config.toml a run uses when `--config` is not given."""
    if frozen is None:
        frozen = is_frozen()
    if not frozen:
        return CHECKOUT / CONFIG_NAME
    if appdata is None:
        appdata = os.environ.get("APPDATA")
    if not appdata:
        # Every Windows sign-in sets it, so this is a stripped
        # environment rather than a real machine. Beside the executable
        # at least puts the file somewhere a person can find.
        return Path(sys.executable).resolve().parent / CONFIG_NAME
    return Path(appdata) / APP_DIR_NAME / CONFIG_NAME


def example_config(*, frozen: bool | None = None) -> Path:
    """config.example.toml: beside the checkout, or unpacked with the build.

    A first run starts its config.toml from this, so the comments that
    explain every setting are in the file from the beginning.
    `tools/build_exe.py` puts it at the top of the bundle, which is
    `sys._MEIPASS` once the app is running.
    """
    if frozen is None:
        frozen = is_frozen()
    if frozen:
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent)) / EXAMPLE_NAME
    return CHECKOUT / EXAMPLE_NAME


def reveal_command(path: Path) -> str:
    """The Explorer command line that shows `path`, selected in its folder.

    A file that is not there yet -- a run that has not saved one -- gets
    the nearest folder that is, rather than an Explorer that opens on
    Documents because the thing it was told to select does not exist.

    One string rather than a list on purpose. Explorer reads its own
    command line and wants `/select,"<path>"`: quoted after the comma,
    not around the whole switch, which is what `subprocess` would make
    of a list the moment the path had a space in it.
    """
    path = Path(path).resolve()
    if path.is_file():
        return f'explorer /select,"{path}"'
    folder = path.parent
    while not folder.is_dir() and folder != folder.parent:
        folder = folder.parent
    return f'explorer "{folder}"'


def reveal(path: Path, *, launch=subprocess.Popen) -> None:
    """Open Explorer on `path`. OSError if there is no Explorer to open.

    Started and left: Explorer answers 1 whether or not it found
    anything, so there is no exit code worth waiting for.
    """
    if sys.platform != "win32":
        raise OSError("opening the folder needs Windows")
    launch(reveal_command(path))
