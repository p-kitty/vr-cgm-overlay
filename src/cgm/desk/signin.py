"""The window a first run opens: the LibreLinkUp email and password.

Everything it decides is in `cgm.core.firstrun`; this is the two boxes
and the button in front of it. See that module for why a run with no
account asks rather than stopping.

The sign-in is tried on a thread. It is a login and a fetch, fifteen
seconds each at worst, and a window that stops repainting for that long
is one Windows retitles "Not Responding" -- which is the moment somebody
decides the download is broken. The thread only ever hands back a
string; every widget is touched from Tk's own loop.

tkinter is imported at module scope, so `cgm.main` imports this lazily,
the same way it does `cgm.desk.window`.
"""

from __future__ import annotations

import logging
import threading
import tkinter as tk
from pathlib import Path
from tkinter import ttk

from cgm.core import firstrun
from cgm.desk.icon import give_icon

log = logging.getLogger("vrcgm")

TITLE = "vr-cgm-overlay - sign in"

INTRO = (
    "Sign in with the LibreLinkUp account that follows the sensor -- the "
    "watching app, not LibreLink. Open LibreLinkUp on a phone first: if it "
    "shows a number, this will too."
)

# The path on a line of its own: wrapped into the sentence, a long
# %APPDATA% path breaks wherever the width runs out, mid-name.
STORED = (
    "Kept as plain text on this computer and sent to LibreLinkUp only, in\n{path}"
)

# How often the loop looks for the thread's answer. Short enough that
# the answer appears to arrive when it does.
POLL_MS = 100


class SignInWindow:
    """One root, open until the account works or the window is closed."""

    def __init__(
        self,
        path: Path,
        *,
        example: Path,
        try_account=firstrun.try_account,
    ) -> None:
        self._path = path
        self._example = example
        self._try = try_account
        self._answer: list[str | None] = []
        self.signed_in = False

        self._root = tk.Tk()
        self._root.title(TITLE)
        give_icon(self._root)
        self._root.resizable(False, False)

        body = ttk.Frame(self._root, padding=16)
        body.pack(fill="both", expand=True)
        ttk.Label(body, text=INTRO, wraplength=380).grid(
            row=0, column=0, columnspan=2, sticky="w", pady=(0, 12)
        )

        self.email = tk.StringVar(value=firstrun.saved_email(path))
        self.password = tk.StringVar()
        ttk.Label(body, text="Email").grid(row=1, column=0, sticky="w", pady=3)
        email_box = ttk.Entry(body, textvariable=self.email, width=34)
        email_box.grid(row=1, column=1, sticky="w", padx=(12, 0), pady=3)
        ttk.Label(body, text="Password").grid(row=2, column=0, sticky="w", pady=3)
        password_box = ttk.Entry(body, textvariable=self.password, width=34, show="•")
        password_box.grid(row=2, column=1, sticky="w", padx=(12, 0), pady=3)

        ttk.Label(
            body,
            text=STORED.format(path=path),
            foreground="gray40",
            wraplength=380,
        ).grid(row=3, column=0, columnspan=2, sticky="w", pady=(10, 0))

        self._status = ttk.Label(body, text="", wraplength=380, foreground="#b00020")
        self._status.grid(row=4, column=0, columnspan=2, sticky="w", pady=(10, 0))

        buttons = ttk.Frame(body)
        buttons.grid(row=5, column=0, columnspan=2, sticky="e", pady=(12, 0))
        ttk.Button(buttons, text="Quit", command=self.close).pack(side="right")
        self._button = ttk.Button(buttons, text="Sign in", command=self.submit)
        self._button.pack(side="right", padx=(0, 8))

        self._root.bind("<Return>", lambda _event: self.submit())
        self._root.protocol("WM_DELETE_WINDOW", self.close)
        (password_box if self.email.get() else email_box).focus_set()

        # Centred on the screen: there is no face yet to open beside.
        self._root.update_idletasks()
        width, height = self._root.winfo_reqwidth(), self._root.winfo_reqheight()
        left = (self._root.winfo_screenwidth() - width) // 2
        top = (self._root.winfo_screenheight() - height) // 3
        self._root.geometry(f"+{left}+{top}")

    # -- signing in ---------------------------------------------------------

    def submit(self) -> None:
        """Try the account on a thread, unless a try is already running."""
        if str(self._button.cget("state")) == "disabled":
            return
        email, password = self.email.get(), self.password.get()
        self._button.configure(state="disabled")
        self._say("Signing in...", error=False)
        self._answer.clear()

        def attempt() -> None:
            self._answer.append(self._try(email, password))

        threading.Thread(target=attempt, name="signin", daemon=True).start()
        self._root.after(POLL_MS, self._wait, email, password)

    def _wait(self, email: str, password: str) -> None:
        if not self._answer:
            self._root.after(POLL_MS, self._wait, email, password)
            return
        problem = self._answer[0]
        if problem is None:
            try:
                firstrun.write_account(
                    self._path, email, password, example=self._example
                )
            except (OSError, ValueError) as exc:
                problem = f"Signed in, but could not save {self._path}: {exc}"
        if problem is not None:
            log.warning("first sign-in failed: %s", problem)
            self._button.configure(state="normal")
            self._say(problem, error=True)
            return
        log.info("signed in; account saved to %s", self._path)
        self.signed_in = True
        self.close()

    def _say(self, text: str, *, error: bool) -> None:
        self._status.configure(text=text, foreground="#b00020" if error else "gray40")

    # -- lifecycle ----------------------------------------------------------

    def run(self) -> bool:
        """Show it until it closes. True when the account was saved."""
        self._root.mainloop()
        return self.signed_in

    def close(self) -> None:
        try:
            self._root.destroy()
        except tk.TclError:  # already gone
            pass


def ask_account(path: Path, *, example: Path) -> bool:
    """Open the sign-in window. True once config.toml holds a working account."""
    return SignInWindow(path, example=example).run()
