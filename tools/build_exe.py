"""Build the Windows app: a folder with vr-cgm-overlay.exe and everything it needs.

    pip install -e ".[vr,build]"
    python tools/build_exe.py

The result is `dist/vr-cgm-overlay/`, which runs on a machine with no
Python at all -- the interpreter, Pillow, tkinter and the SteamVR
bindings are inside it. Zip that folder to hand it on.

A folder rather than PyInstaller's single-file mode. The single file
unpacks itself into a temporary directory on every start, which is
slower, and it is the shape antivirus heuristics flag most often, which
for a program nobody has signed is the difference between a warning and
a quarantine.

Built without a console (`--windowed`), because it is started from
Explorer and from the Startup folder, where a console is only a black
window beside the face. `cgm.main` already runs that way under pythonw:
errors that stop it go to a dialog, and the log goes to `logs/`.

The config is not in the build. The app looks for it under %APPDATA%;
see `cgm.core.paths`.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NAME = "vr-cgm-overlay"


def main() -> int:
    try:
        import PyInstaller.__main__
    except ImportError:
        print('PyInstaller is not installed; pip install -e ".[vr,build]"', file=sys.stderr)
        return 2
    try:
        import openvr  # noqa: F401
    except ImportError:
        # A build without it runs, and silently never shows the overlay.
        print('the SteamVR bindings are not installed; pip install -e ".[vr,build]"', file=sys.stderr)
        return 2

    work = ROOT / "build" / "pyinstaller"
    PyInstaller.__main__.run(
        [
            str(ROOT / "src" / "cgm" / "__main__.py"),
            "--name", NAME,
            "--windowed",
            "--noconfirm",
            "--clean",
            # The package from this checkout, installed or not.
            "--paths", str(ROOT / "src"),
            # openvr finds libopenvr_api_64.dll beside its own
            # __init__.py through importlib.resources, which no import
            # analysis can see, so the DLL is named here or left behind.
            "--collect-binaries", "openvr",
            # `cgm.vr` and `cgm.desk` are imported inside functions, so
            # say so rather than rely on the analysis reaching them.
            "--collect-submodules", "cgm",
            "--distpath", str(ROOT / "dist"),
            "--workpath", str(work),
            "--specpath", str(work),
        ]
    )
    exe = ROOT / "dist" / NAME / f"{NAME}.exe"
    print(f"built {exe}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
