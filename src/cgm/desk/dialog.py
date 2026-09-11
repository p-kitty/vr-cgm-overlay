"""A message box for when there is no console to print to.

Started with Windows, the process runs under pythonw and has no stdout
or stderr at all. A config error that used to be one line on a terminal
would then end the process in silence -- the window simply never
appears -- so `cgm.main` hands the reasons it stops for to this instead.

tkinter is imported at module scope, so like `cgm.desk.window` this is
imported lazily.
"""

from __future__ import annotations

import tkinter as tk
from tkinter import messagebox

TITLE = "vr-cgm-overlay"


def show_error(message: str) -> None:
    """Show `message` and wait for it to be dismissed."""
    # A root of its own, hidden, since this can run before there is a
    # window or after it has gone. The box needs a parent to sit on.
    root = tk.Tk()
    root.withdraw()
    try:
        messagebox.showerror(TITLE, message, parent=root)
    finally:
        root.destroy()
