"""Putting the app's icon on its windows.

Without this every window is Tk's red feather, and under pythonw the
taskbar button beside it is Python's -- neither of which says which of
the things running is the glucose readout. The picture itself is
`cgm.face.icon`; this only hands it to Tk.

tkinter is imported at module scope, so like the windows that call it
this is imported lazily.
"""

from __future__ import annotations

import ctypes
import logging
import sys
import tkinter as tk

from PIL import ImageTk

from cgm.face.icon import draw_icon

log = logging.getLogger("vrcgm")

# Windows picks a title bar icon and a taskbar icon out of these by
# size, and scales whichever is nearest. Enough to cover 100% to 300%
# display scaling without shipping every size the .ico has.
SIZES = (16, 24, 32, 48, 64, 256)

# Who this process is, as far as the taskbar is concerned. Without one,
# Windows files the window under the executable that opened it --
# pythonw.exe -- and draws Python's icon on the button whatever the
# window says. With one, the button is this app's and wears the
# window's icon.
APP_ID = "p-kitty.vr-cgm-overlay"


def give_icon(root: tk.Tk) -> None:
    """Set the icon on `root` and on every Toplevel opened under it later.

    A missing icon is not worth a window that fails to open, so a Tk
    that will not take it is logged and let go.
    """
    _claim_taskbar_button()
    try:
        photos = [ImageTk.PhotoImage(draw_icon(size), master=root) for size in SIZES]
        # default=True, so the settings window, opened later as a
        # Toplevel of this root, carries it without asking.
        root.iconphoto(True, *photos)
    except tk.TclError:
        log.warning("could not set the window icon", exc_info=True)
        return
    # Tk copies the pixels when the icon is set, but a PhotoImage that
    # Python drops is deleted from Tk as well. Held for the root's life
    # rather than trusting that copy to be the only one Tk needs.
    root._cgm_icon = photos  # type: ignore[attr-defined]


def _claim_taskbar_button() -> None:
    """Give the process an AppUserModelID on Windows. Setting it twice is harmless."""
    if sys.platform != "win32":
        return
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID)
    except (AttributeError, OSError):
        log.warning("could not set the taskbar identity", exc_info=True)
