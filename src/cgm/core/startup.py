"""Starting with Windows: one shortcut in the user's Startup folder.

The resident process is meant to be started once and left, so the next
step is not having to start it at all. Windows opens everything in the
Startup folder at sign-in, and Task Manager lists it under Startup apps
with a switch -- so once this is installed, turning it off needs
nothing from this app.

A shortcut rather than a value under the registry's Run key, which
Windows treats the same way. This is software people install from a
repository, and a file they can see is one they can check and delete by
hand: `shell:startup` in the Run box opens the folder. A registry value
asks for the same trust with none of that, and "what did it change on
my machine" should have an answer anyone can find.

A .lnk is a binary format that only the Windows shell writes, so this
asks the shell through PowerShell, which every Windows 10 and 11
carries. The paths go across in environment variables rather than in
the script, so no path -- this checkout's own has Japanese in it -- has
to survive being quoted.

What the shortcut runs is `pythonw.exe -m cgm`, from the environment
this was installed from. pythonw is the console-less interpreter every
venv on Windows carries, so a sign-in opens the face and nothing else.
With no console there is nowhere for an error to be printed, which is
why `cgm.main` shows the ones that stop the process in a dialog instead.

The config is named in full: the one registered is the one that was
checked when it was registered.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

LINK_NAME = "vr-cgm-overlay.lnk"

# Keeps PowerShell from flashing a console window of its own when this
# is run from one that has none.
CREATE_NO_WINDOW = 0x08000000

_WRITE = (
    "$s = (New-Object -ComObject WScript.Shell).CreateShortcut($env:CGM_LINK);"
    "$s.TargetPath = $env:CGM_TARGET;"
    "$s.Arguments = $env:CGM_ARGS;"
    "$s.WorkingDirectory = $env:CGM_DIR;"
    "$s.Description = 'FreeStyle Libre glucose on your desktop and in SteamVR';"
    "$s.Save()"
)


def startup_folder() -> Path:
    """The folder Windows opens everything in at sign-in, for this user."""
    appdata = os.environ.get("APPDATA")
    if not appdata:
        raise OSError("APPDATA is not set, so there is no Startup folder to use")
    return Path(appdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"


def pythonw(python: Path | None = None) -> Path:
    """The console-less interpreter beside the one running this."""
    return (python or Path(sys.executable)).with_name("pythonw.exe")


def arguments(config_path: Path) -> str:
    """What the shortcut passes to pythonw."""
    return f'-m cgm --config "{config_path.resolve()}"'


def powershell(script: str, **env: str) -> str:
    """Run one PowerShell command. Its output, or OSError saying why not.

    Output is set to UTF-8 on both ends. PowerShell otherwise writes in
    the console's code page, and a path read back through that loses
    whatever the code page has no room for.
    """
    try:
        done = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                "[Console]::OutputEncoding = [Text.Encoding]::UTF8;" + script,
            ],
            env={**os.environ, **env},
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            creationflags=CREATE_NO_WINDOW,
            timeout=60,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        raise OSError(f"PowerShell did not run: {exc}") from exc
    if done.returncode != 0:
        raise OSError(f"PowerShell failed: {done.stderr.strip()}")
    return done.stdout


def install(config_path: Path, *, folder: Path | None = None) -> Path:
    """Put the shortcut in the Startup folder. Returns where it went.

    Installing again overwrites it, so it is also how to point it at a
    different config or a moved checkout.
    """
    if sys.platform != "win32":
        raise OSError("starting with Windows needs Windows")
    interpreter = pythonw()
    if not interpreter.exists():
        raise OSError(f"no pythonw.exe beside {sys.executable}")
    folder = folder or startup_folder()
    folder.mkdir(parents=True, exist_ok=True)
    link = folder / LINK_NAME
    powershell(
        _WRITE,
        CGM_LINK=str(link),
        CGM_TARGET=str(interpreter),
        CGM_ARGS=arguments(config_path),
        CGM_DIR=str(config_path.resolve().parent),
    )
    return link


def uninstall(*, folder: Path | None = None) -> bool:
    """Take the shortcut away. False if there was none."""
    link = (folder or startup_folder()) / LINK_NAME
    try:
        link.unlink()
    except FileNotFoundError:
        return False
    return True


def registered(*, folder: Path | None = None) -> Path | None:
    """The shortcut, if there is one."""
    link = (folder or startup_folder()) / LINK_NAME
    return link if link.exists() else None
