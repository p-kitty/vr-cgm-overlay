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

**Every section is offered, `[vr]` included.** It was left out at
first, on the grounds that placement can only be judged with the headset
on. That is true and it is not a reason to withhold it: pressing Save
here is the same loop as saving the file, because both go back through
the watcher and land within the second. What was actually missing was a
widget for three numbers, and that is `Vector3Var`. The tab says how the
loop goes; docs/placement.md says what the numbers mean.

**A section too tall for the window is split across tabs.** A notebook
is as tall as its tallest page, and `[vr]` has fourteen settings where
the next largest has seven -- so one section was setting the height of
the whole window and leaving the other seven tabs half empty. `GROUPS`
carves the long one into `vr`, `vr orbit` and `vr gaze`, none of them
taller than the tabs around it. It names keys rather than hiding them,
and the section's own tab takes whatever is left over, so a setting
added to `[vr]` still appears without anybody editing this file. The
labels keep the section's name in front for the same reason the rows are
labelled `rearm_margin_mgdl`: there is no `[orbit]` section to go
looking for.

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

# Tabs carved out of one section, in the order they follow it. Only
# `[vr]` needs it -- see the module docstring for why -- and only the
# groups are named: whatever is left over stays on the section's own tab,
# so a setting added later appears there rather than going missing.
GROUPS = {
    "vr": (
        ("vr orbit", ("orbit", "orbit_radius_m", "orbit_limit_deg", "arm_guide")),
        (
            "vr gaze",
            ("gaze_fade", "gaze_full_deg", "gaze_fade_deg", "gaze_min_alpha"),
        ),
    ),
}

# A line at the top of one tab, where the tab needs something said about
# it that no single row does. Keyed by tab rather than by section, so a
# carved one can say what it is for -- and, for the two modes, that the
# rows under it do nothing until the switch at the top is on.
NOTES = {
    "vr": (
        "Placement is judged with the headset on: change a number, press "
        "Save, and watch the face move. Nudging it in config.toml works "
        "the same way. See docs/placement.md for what the numbers mean."
    ),
    "vr orbit": (
        "Off, the face is bolted to the controller. On, it rides round the "
        "modelled centreline of your forearm -- and offset and rotation_deg "
        "on the vr tab change meaning. arm_guide draws that line while you "
        "tune it; turn it off when you are done."
    ),
    "vr gaze": (
        "Dims the face while you are not looking at it. The other three do "
        "nothing while gaze_fade is off, and a reading under "
        "thresholds.low_mgdl is never faded whatever they say."
    ),
}

# Settings whose value is one of a short list. A free text box for these
# is a way of typing a value the loader then refuses.
CHOICES = {
    "display.unit": ("mgdl", "mmol"),
    "vr.hand": ("left", "right"),
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
    "vr.hand": "which controller to follow; the off hand works best",
    "vr.width_m": "how wide the card is, in metres",
    "vr.offset": "metres: X right, Y up off the back of the hand, Z to the elbow",
    "vr.rotation_deg": "degrees, applied X then Y then Z",
    "vr.orbit": "ride around the forearm instead of being bolted to the controller",
    "vr.orbit_radius_m": "how far off the arm's centreline the face floats",
    "vr.orbit_limit_deg": "how far round the arm it may travel, up to 180",
    "vr.arm_guide": "draw the arm orbit mode is aiming at, while you tune it",
    "vr.gaze_fade": "dim the face while you are not looking at it",
    "vr.gaze_full_deg": "within this of the centre of view: full opacity",
    "vr.gaze_fade_deg": "past this: gaze_min_alpha, fading in between",
    "vr.gaze_min_alpha": "what is left when you look away; never below 0.1",
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
    """The sections the window offers, in the order it offers them.

    All of them, in the file's order; the module docstring says why
    `[vr]` is among them.
    """
    return list(config_mod.FIELD_TYPES)


def tabs() -> list[tuple[str, str, tuple[str, ...]]]:
    """Every tab as (label, section, the keys it holds), left to right.

    One per section, each followed by whatever `GROUPS` carves out of it.
    The section's own tab gets the leftovers, computed rather than
    listed, which is what keeps a new setting from falling between the
    two.
    """
    out: list[tuple[str, str, tuple[str, ...]]] = []
    for section in sections():
        groups = GROUPS.get(section, ())
        taken = {key for _label, keys in groups for key in keys}
        rest = tuple(
            key for key in config_mod.FIELD_TYPES[section] if key not in taken
        )
        out.append((section, section, rest))
        out.extend((label, section, keys) for label, keys in groups)
    return out


def _check_groups(groups_by_section=None) -> None:
    """Refuse at import a group that names a setting that is not there.

    A typo here is quiet in both directions: the key stays on the
    section's own tab as though nothing had been asked, and the carved
    tab goes looking for a field the dataclass does not have. The second
    is an AttributeError while the window is being built, which takes the
    face down with it, so it is worth catching at import instead.
    """
    if groups_by_section is None:
        groups_by_section = GROUPS
    for section, groups in groups_by_section.items():
        if section not in config_mod.FIELD_TYPES:
            raise KeyError(f"{section} is grouped but is not a config section")
        seen: set[str] = set()
        for label, keys in groups:
            for key in keys:
                if key not in config_mod.FIELD_TYPES[section]:
                    raise KeyError(
                        f"the {label} tab names {section}.{key}, which does not exist"
                    )
                if key in seen:
                    raise KeyError(f"{section}.{key} is on two tabs")
                seen.add(key)


_check_groups()


# The kinds this form has a widget for: a checkbox, a dropdown, a text
# box, and three text boxes for the one setting that is a vector.
SHOWN_KINDS = (str, float, bool, config_mod.VECTOR3)


def _check_shown_kinds(field_types: dict[str, dict[str, type]] | None = None) -> None:
    """Refuse at import a setting there is no widget for.

    `cgm.core.config` does the same thing one layer down, and for the
    same reason. Without it a new kind gets whatever widget the last
    branch happens to be, holding text nothing can convert back, and the
    failure is somebody pressing Save.
    """
    if field_types is None:
        field_types = {name: config_mod.FIELD_TYPES[name] for name in sections()}
    for section, kinds in field_types.items():
        for key, kind in kinds.items():
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


class Vector3Var:
    """Three boxes that answer as one setting.

    `offset` and `rotation_deg` are the only compound values there are,
    and a single box holding `0.0, -0.02, 0.12` would be a text format
    to parse and to complain about. Three boxes instead, and this stands
    in for a `tk.Variable` in the two ways the window uses one: `get`
    hands back the three strings, which `config.parse` already accepts
    because a TOML array arrives as a list too, and `trace_add` marks the
    window edited from any of them.
    """

    def __init__(self, value) -> None:
        self.parts = [tk.StringVar(value=spell(part)) for part in value]

    def get(self) -> list[str]:
        return [part.get() for part in self.parts]

    def trace_add(self, mode: str, callback) -> None:
        for part in self.parts:
            part.trace_add(mode, callback)


def apply_values(cfg: config_mod.Config, values: dict[tuple[str, str], object]) -> None:
    """Put the widgets' values onto `cfg`, converting each as declared.

    Split out of the window because it is the whole of what pressing
    Save means, and because it can then be exercised without starting
    Tk. Raises ValueError naming the setting when a box holds something
    its field cannot be.

    Whatever is not in `values` is left alone. The window always hands
    over every box, so that is a property of this function rather than
    of pressing Save.
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
        self._vars: dict[tuple[str, str], tk.Variable | Vector3Var] = {}

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
        for label, section, keys in tabs():
            frame = ttk.Frame(notebook, padding=12)
            notebook.add(frame, text=label)
            self._fill(frame, label, section, keys)

        self._status = ttk.Label(self._top, text="", wraplength=560)
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

    def _fill(
        self, frame: ttk.Frame, label: str, section: str, keys: tuple[str, ...]
    ) -> None:
        held = getattr(self._cfg, section)
        first = 0
        note = NOTES.get(label)
        if note:
            # Above the rows rather than at the bottom of the window:
            # what it says is about this tab, and a line under the tabs
            # reads as being about whichever one is open.
            ttk.Label(
                frame, text=note, foreground="gray40", wraplength=520
            ).grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, 10))
            first = 1

        for offset, key in enumerate(keys):
            kind = config_mod.FIELD_TYPES[section][key]
            row = first + offset
            name = f"{section}.{key}"
            value = getattr(held, key)

            if kind == config_mod.VECTOR3:
                variable: tk.Variable | Vector3Var = Vector3Var(value)
                ttk.Label(frame, text=key).grid(
                    row=row, column=0, sticky="w", pady=3
                )
                boxes = ttk.Frame(frame)
                for part in variable.parts:
                    ttk.Entry(boxes, textvariable=part, width=8).pack(
                        side="left", padx=(0, 4)
                    )
                boxes.grid(row=row, column=1, sticky="w", padx=(12, 0), pady=3)
                self._hint(frame, row, name)
                self._vars[(section, key)] = variable
                continue

            if kind is bool:
                # The name goes on the box rather than in the column
                # beside it. Focus draws a dotted ring around a
                # checkbutton's label, and around an empty label that is
                # a small box of dashes floating next to the tick --
                # which reads as damage rather than as focus. It also
                # makes the whole word clickable, which is how a
                # checkbox is expected to behave.
                variable = tk.BooleanVar(value=value)
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

        Every box is written, touched or not: after a Save the file holds
        what the window shows. So an edit made in a text editor while
        this was open is overwritten by the value the window opened
        with. Editing the same file two ways at once is not a case this
        is built for; close the window, edit, and open it again.

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
