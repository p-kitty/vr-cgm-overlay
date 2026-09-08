"""The settings window: config.toml with widgets in front of it.

Every setting is a line in a text file, and for placement that is right
-- `offset` is found by nudging a number with the headset on and
watching the face move, which no dialog improves on. For a threshold it
is wrong. Nobody should have to alt-tab to a text editor to decide when
a low is a low.

Three things make this small rather than another application.

**It writes the file and stops there.** There is no second path into the
running poller, alert or overlay: it saves `config.toml` and
`ConfigWatcher` picks the edit up within the second, exactly as it picks
up one made in a text editor. So every reload path already in `cgm.main`
keeps working unchanged, and so does every test over them -- and a
setting that needs a restart still says so, through the same warning.

**The form is the config walk.** One row per field of
`cgm.core.config.FIELD_TYPES`, with the widget chosen by the declared
type, so a setting added to a dataclass appears here without anybody
remembering to add it. The row is labelled with the key's own name --
`rearm_margin_mgdl`, not "Re-arm margin" -- on purpose: that is what
`config.toml` calls it and what docs/configuration.md explains, so the
window teaches the file rather than inventing a second vocabulary for
it.

**There is no live apply, and so nothing to debounce.** Every save is a
full re-read of the file, so writing on each keystroke would reload the
config once per character -- and would do it through half-typed values,
where `80` is `8` on the way past and a low threshold of 8 is a real
config for as long as it takes to type the next digit. One Save button
instead. What it costs is that `window.scale` cannot be dragged and
watched, which is a fair price for not being able to save nonsense by
accident.

**`[vr]` is not offered**, and that is the one deliberate hole. Where
the face sits on your arm cannot be judged from a desktop window with
the headset on your head, so live file reload stays the better tool for
it; see docs/placement.md. Everything else here is decided at a desk.

tkinter is imported at module scope, so `cgm.main` imports this module
lazily, the same way it does `cgm.desk.window`.
"""

from __future__ import annotations

import logging
import tkinter as tk
from pathlib import Path
from tkinter import ttk

from cgm.core import config as config_mod

log = logging.getLogger(__name__)

# Sections this window does not offer, with the reason, which is shown
# at the bottom rather than left for somebody to wonder about.
SKIPPED = {
    "vr": (
        "[vr] is left to config.toml: where the face sits on your arm can "
        "only be judged with the headset on. See docs/placement.md."
    )
}

# Settings whose value is one of a short list. A free text box for these
# is a way of typing a value the loader then refuses.
CHOICES = {
    "display.unit": ("mgdl", "mmol"),
}

# Shown as dots. The only one, and the reason the window exists at all
# is not this -- but a password in plain text on a second monitor is
# exactly the kind of thing a glucose overlay should not do.
SECRET = frozenset({"account.password"})

# A few words where the key's own name does not carry it. Deliberately
# not one per setting: docs/configuration.md is the long form, and a
# hint beside every row would be a second copy of it to keep in step.
HINTS = {
    "account.patient_id": "leave empty unless you follow several people",
    "account.region": "leave empty to follow the login redirect",
    "display.stale_after_min": "minutes before the reading goes grey",
    "window.scale": (
        f"{config_mod.WINDOW_SCALE_MIN} to {config_mod.WINDOW_SCALE_MAX}"
    ),
    "graph.window_min": "minutes of history; 0 for all of it",
    "graph.axis_high_mgdl": "top of the axis, mg/dL",
    "thresholds.low_mgdl": "mg/dL, whatever the display unit is",
    "thresholds.high_mgdl": "mg/dL",
    "thresholds.very_high_mgdl": "mg/dL",
    "trend.fast_mgdl_min": "mg/dL per minute drawn as a full arrow",
    "polling.interval_sec": "seconds; never below 30",
    "polling.sound_path": "a .wav, or empty for the Windows alert sound",
    "polling.rearm_margin_mgdl": "how far above low before a new low counts",
    "polling.repeat_every_min": "0 announces once, on the way in",
}


def sections() -> list[str]:
    """The sections the window offers, in the order it offers them."""
    return [name for name in config_mod.FIELD_TYPES if name not in SKIPPED]


# The kinds this form has a widget for: a checkbox, a dropdown, or a
# text box. `tuple[float, float, float]` is the one it does not have,
# and it only exists under `[vr]`, which is not offered.
SHOWN_KINDS = (str, float, bool)


def _check_shown_kinds() -> None:
    """Refuse at import a setting there is no widget for.

    `cgm.core.config` does the same thing one layer down, and for the
    same reason. Without this, a vector added to an offered section gets
    a text box holding `(0.0, 0.02, 0.1)` that nothing can convert back,
    and the failure is somebody pressing Save.
    """
    for section in sections():
        for key, kind in config_mod.FIELD_TYPES[section].items():
            if kind not in SHOWN_KINDS:
                raise TypeError(
                    f"{section}.{key} is annotated {kind!r}, which the settings "
                    "window has no widget for; give it one or skip its section"
                )


_check_shown_kinds()


def spell(value) -> str:
    """A held value as it should appear in a text box.

    Every number here is held as a float, and a box reading `70.0` where
    the file says `70` invites somebody to "fix" it and save a diff that
    changes nothing.
    """
    if isinstance(value, float):
        return str(int(value)) if value.is_integer() else repr(value)
    return str(value)


def apply_values(cfg: config_mod.Config, values: dict[tuple[str, str], object]) -> None:
    """Put the widgets' values onto `cfg`, converting each as declared.

    Split out of the window because it is the whole of what pressing
    Save means, and because it can then be exercised without starting
    Tk. Raises ValueError naming the setting when a box holds something
    its field cannot be.

    Whatever is not in `values` is left alone -- which is how `[vr]`
    survives a save from a window that never showed it.
    """
    for (section, key), value in values.items():
        setattr(
            getattr(cfg, section), key, config_mod.parse(section, key, value)
        )


class SettingsWindow:
    """One Toplevel over one config.toml.

    A Toplevel rather than widgets in the face's own window: `FaceWindow`
    takes its size from the image and forgets its geometry whenever that
    changes, so anything else in that frame would be fighting the sizing
    every time the graph is turned on.
    """

    def __init__(self, master: tk.Misc, path: Path) -> None:
        self._path = path
        # Read from the file rather than taken from the running config:
        # the file is what this is about to write, and it is also where
        # an edit made in a text editor a moment ago will be.
        self._cfg = config_mod.load(path)
        self._vars: dict[tuple[str, str], tk.Variable] = {}

        self._top = tk.Toplevel(master)
        self._top.title(f"settings - {path.name}")
        self._top.resizable(False, False)
        # The face is usually always-on-top; a dialog underneath it is a
        # dialog nobody can find.
        self._top.transient(master)
        try:
            self._top.attributes("-topmost", master.attributes("-topmost"))
        except tk.TclError:  # not every master answers for that
            pass

        notebook = ttk.Notebook(self._top)
        notebook.pack(fill="both", expand=True, padx=10, pady=(10, 0))
        for section in sections():
            frame = ttk.Frame(notebook, padding=12)
            notebook.add(frame, text=section)
            self._fill(frame, section)

        for reason in SKIPPED.values():
            ttk.Label(
                self._top, text=reason, foreground="gray40", wraplength=460
            ).pack(fill="x", padx=12, pady=(8, 0))

        self._status = ttk.Label(self._top, text="", wraplength=460)
        self._status.pack(fill="x", padx=12, pady=(8, 0))

        buttons = ttk.Frame(self._top, padding=(10, 10))
        buttons.pack(fill="x")
        ttk.Button(buttons, text="Close", command=self.close).pack(side="right")
        self._save_button = ttk.Button(
            buttons, text="Save", command=self.save, state="disabled"
        )
        self._save_button.pack(side="right", padx=(0, 8))

        # Watched only after every row is built, so filling the boxes in
        # is not itself an edit. What counts as one is any write to any
        # variable -- not a comparison against the file. Typing a digit
        # and deleting it again offers a Save that writes nothing, which
        # costs nothing; the alternative is comparing 25 values on every
        # keystroke to be right about a greyed-out button.
        for variable in self._vars.values():
            variable.trace_add("write", self._touched)

        self._top.protocol("WM_DELETE_WINDOW", self.close)

    # -- building -----------------------------------------------------------

    def _fill(self, frame: ttk.Frame, section: str) -> None:
        held = getattr(self._cfg, section)
        for row, (key, kind) in enumerate(config_mod.FIELD_TYPES[section].items()):
            name = f"{section}.{key}"
            value = getattr(held, key)

            if kind is bool:
                # The name goes on the box rather than in the column
                # beside it. Focus draws a dotted ring around a
                # checkbutton's label, and around an empty label that is
                # a small box of dashes floating next to the tick --
                # which reads as damage rather than as focus. It also
                # makes the whole word clickable, which is how a
                # checkbox is expected to behave.
                variable: tk.Variable = tk.BooleanVar(value=value)
                widget: tk.Widget = ttk.Checkbutton(
                    frame, text=key, variable=variable
                )
                widget.grid(row=row, column=0, columnspan=2, sticky="w", pady=3)
                self._hint(frame, row, name)
                self._vars[(section, key)] = variable
                continue

            ttk.Label(frame, text=key).grid(row=row, column=0, sticky="w", pady=3)

            if name in CHOICES:
                variable = tk.StringVar(value=spell(value))
                widget = ttk.Combobox(
                    frame,
                    textvariable=variable,
                    values=list(CHOICES[name]),
                    state="readonly",
                    width=18,
                )
            else:
                variable = tk.StringVar(value=spell(value))
                widget = ttk.Entry(
                    frame,
                    textvariable=variable,
                    width=28,
                    show="•" if name in SECRET else "",
                )
            widget.grid(row=row, column=1, sticky="w", padx=(12, 0), pady=3)
            self._hint(frame, row, name)
            self._vars[(section, key)] = variable

    def _hint(self, frame: ttk.Frame, row: int, name: str) -> None:
        hint = HINTS.get(name)
        if hint:
            ttk.Label(frame, text=hint, foreground="gray40").grid(
                row=row, column=2, sticky="w", padx=(12, 0)
            )

    # -- saving -------------------------------------------------------------

    def save(self) -> bool:
        """Write the boxes back to config.toml. True when something was.

        Re-reads the file first, so a change made in a text editor while
        this was open -- to `[vr]`, most likely, since that is what is
        not here -- is not overwritten by the values this window was
        opened with.

        Nothing is written when anything is wrong, and the reason is put
        on the window rather than in the log: whoever typed it is looking
        at this, not at a console.
        """
        try:
            cfg = config_mod.load(self._path)
            apply_values(cfg, {key: var.get() for key, var in self._vars.items()})
            config_mod.save(cfg, self._path)
        except (OSError, ValueError) as exc:
            # Still something unsaved, so Save stays available: the fix
            # is to correct the box and press it again.
            self._say(str(exc))
            return False

        self._offer_save(False)
        self._say(f"saved {self._path.name}")
        log.info("settings saved to %s", self._path)
        return True

    def _touched(self, *_trace) -> None:
        """A box changed, so there is now something to write."""
        self._offer_save(True)

    def _offer_save(self, offer: bool) -> None:
        """Grey the Save button out when pressing it would write nothing.

        A button that does nothing is worse than no button: it says the
        window is unsure whether it has your change.
        """
        if self._top.winfo_exists():
            self._save_button.configure(state="normal" if offer else "disabled")

    def _say(self, text: str) -> None:
        if self._top.winfo_exists():
            self._status.configure(text=text)

    # -- lifecycle ----------------------------------------------------------

    def alive(self) -> bool:
        return bool(self._top.winfo_exists())

    def lift(self) -> None:
        """Bring an already-open window forward instead of opening a second."""
        self._top.lift()
        self._top.focus_force()

    def close(self) -> None:
        if self._top.winfo_exists():
            self._top.destroy()
