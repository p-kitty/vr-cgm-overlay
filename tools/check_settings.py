"""Open the settings window the way a user does, and press Save for them.

`tests/test_settings.py` covers everything between the boxes and the
file, but it deliberately does not start Tk -- a suite that opened
windows on whoever ran it would be a nuisance, and the same rule keeps
`tests/test_desk.py` away from a root. So the half that only exists once
Tk is running has nothing over it: the right-click that opens the thing,
whether a row is built for every setting, whether the variables are
really attached to the widgets, whether Save reaches the file, whether
a rejected value says so on the window instead of taking the face down
with it, and where the window opens -- which only Windows can say, once
both frames exist.

That is what this does, through the production path and nothing else: a
real `FaceWindow`, a real `<Button-3>` on the face, and whatever that
opens. It runs over a throwaway copy of `config.example.toml` -- the
file everybody starts from, and the one with all the comments that are
the reason `save` goes through tomlkit at all. **Nothing here touches
your own config.toml.**

    python tools/check_settings.py           assert and exit
    python tools/check_settings.py --show    leave it open to look at

`--show` is the other half, and the half nothing can automate: whether
it is legible, whether the tabs are in a sensible order, whether the
hints read as help rather than as noise. Same throwaway copy, so
anything saved from it goes nowhere near your settings.

Needs a desktop -- it is a window -- but no headset and no network.
"""

from __future__ import annotations

import sys
import tempfile
import tomllib
import tkinter as tk
from pathlib import Path

from PIL import Image

from cgm.core import config as config_mod
from cgm.core import presets as presets_mod
from cgm.desk import settings as settings_mod
from cgm.desk.window import FaceWindow, compose, work_area
from cgm.face.renderer import STATUS_MARKERS
from cgm.main import build_renderer

ROOT = Path(__file__).resolve().parents[1]

# The window.scale values the corner marks are checked at. Not the
# bottom of the allowed range: below about half size the column they
# stand in is narrower than the blur resampling puts on the stale
# frame's edge, and a face that small has no room to spare anywhere.
MARK_SCALES = (0.5, 1.0, 2.0)

# How far a pixel under a mark may be from the bare card and still count
# as card. Resampling leaves the column a unit or two off flat; a lit
# marker, drawn white here, is well over a hundred away.
MARK_TOLERANCE = 8

# The example is the file everybody starts from and it holds every
# setting. The only thing wrong with it is the blank password, which
# `_validate` refuses.
BLANK_PASSWORD = 'password = ""'
SAMPLE_PASSWORD = 'password = "not-a-real-password"'

# A comment from the middle of the example, looked for afterwards. Any
# line would do; this one sits beside a setting the window edits, which
# is where a careless write would land.
LANDMARK = "# The face is red below low, green up to high"


def sample(directory: Path) -> Path:
    """A loadable copy of config.example.toml, in a temporary directory."""
    path = directory / "config.toml"
    text = (ROOT / "config.example.toml").read_text(encoding="utf-8")
    assert text.count(BLANK_PASSWORD) == 1, "config.example.toml has moved on"
    assert LANDMARK in text, "the landmark comment has gone"
    path.write_text(text.replace(BLANK_PASSWORD, SAMPLE_PASSWORD), encoding="utf-8")
    return path


def say(what: str, detail) -> None:
    print(f"  {what:32} {detail}")


def marks_cover(face: FaceWindow, cfg) -> list[str]:
    """Every place the gear or the VR mark hides part of a status marker.

    The marks are opaque labels, so over a lit edge they cut a dark box
    out of it -- which is how a high used to look, with the VR mark in
    the middle of the top bar and the gear against its end. Each card is
    drawn from its marker alone, one per entry in STATUS_MARKERS, so a
    marker added later is checked without being listed here, and in
    white, so there is no accent a covered pixel could pass for card in.
    """
    renderer = build_renderer(cfg, with_graph=True, rounded=False)
    ground = compose(Image.new("RGBA", (1, 1), renderer.theme.color_bg), 1.0)
    ground = ground.getpixel((0, 0))
    before = face._scale
    found = []
    for scale in MARK_SCALES:
        face.set_scale(scale)
        for status, marker in STATUS_MARKERS.items():
            card, _draw = renderer._new_canvas((255, 255, 255), marker)
            face.set_image(card)
            face._root.update()
            shown = compose(card, scale)
            for mark in (face._gear, face._badge):
                x, y = mark.winfo_x(), mark.winfo_y()
                under = shown.crop((x, y, x + mark.winfo_width(), y + mark.winfo_height()))
                worst = max(
                    max(abs(a - b) for a, b in zip(colour, ground))
                    for _count, colour in under.getcolors(under.width * under.height)
                )
                if worst > MARK_TOLERANCE:
                    found.append(f"{mark.cget('text')!a} on {status} at {scale}")
    face.set_scale(before)
    return found


def frame(top: tk.Misc) -> tuple[int, int, int, int]:
    """A window's outer frame, left, top, right, bottom, as Windows has it."""
    import ctypes
    from ctypes import wintypes

    rect = wintypes.RECT()
    ctypes.windll.user32.GetWindowRect(int(top.wm_frame(), 16), ctypes.byref(rect))
    return rect.left, rect.top, rect.right, rect.bottom


def placement(path: Path, face: FaceWindow) -> None:
    """The settings open beside the face, on its other side near an edge.

    Checked against Windows' own idea of both frames rather than against
    the arithmetic in `_place_beside`, so a wrong guess at the border
    there fails here instead of agreeing with itself.
    """
    root = face._root
    root.update()
    left, top, right, bottom = frame(root)
    area = work_area((left + right) // 2, (top + bottom) // 2)
    assert area is not None, "Windows named no monitor"

    for where, x in (
        ("right of it", area[0] + 50),
        ("left of it, at the right edge", area[2] - (right - left) - 20),
    ):
        root.geometry(f"+{x}+{area[1] + 100}")
        root.update()
        window = settings_mod.SettingsWindow(root, path)
        root.update()
        owner, opened = frame(root), frame(window._top)
        window.close()
        inside = (
            opened[0] >= area[0]
            and opened[1] >= area[1]
            and opened[2] <= area[2]
            and opened[3] <= area[3]
        )
        assert inside, f"{where}: {opened} is off the work area {area}"
        clear = opened[0] >= owner[2] or opened[2] <= owner[0]
        assert clear, f"{where}: {opened} covers the face at {owner}"
        side = "right of it" if opened[0] >= owner[2] else "left of it"
        assert where.startswith(side), f"expected {where}, opened {side}"
        assert opened[1] == owner[1], f"{where}: tops {opened[1]} and {owner[1]}"
        say("opens beside the face", where)


def check(path: Path, face: FaceWindow, show: bool) -> None:
    cfg = config_mod.load(path)
    message = build_renderer(cfg, with_graph=False, rounded=False).render_message(
        "SETTINGS"
    )
    face.set_image(message)

    # The two gestures. `cgm.main` binds them to opening the settings;
    # here they are bound to a list, so what is asserted is that the
    # click arrives and hands over something a Toplevel can be parented
    # to. The gear is the one somebody finds without being told, so it
    # is the one that would be missed if it stopped working.
    opened: list[tk.Misc] = []
    face.on_menu(opened.append)
    face._root.update()
    face._gear.event_generate("<Button-1>", x=2, y=2)
    face._root.update()
    assert opened, "clicking the gear did nothing"
    assert face._gear.winfo_ismapped(), "the gear is not on screen"
    face._label.event_generate("<Button-3>", x=10, y=10)
    face._root.update()
    assert len(opened) == 2, "right-clicking the face did nothing"
    say("both ways in open something", type(opened[0]).__name__)

    # The mark that says the overlay is up. Absent is not an error -- it
    # is SteamVR not running -- so it appears and disappears rather than
    # changing colour.
    face.set_vr(True)
    face._root.update()
    assert face._badge is not None and face._badge.winfo_ismapped(), "no VR mark"
    face.set_vr(False)
    face._root.update()
    assert not face._badge.winfo_ismapped(), "the VR mark stayed up"
    say("the VR mark comes and goes", "shown while a controller has the face")

    face.set_vr(True)
    covered = marks_cover(face, cfg)
    assert not covered, "a corner mark sits on a marker: " + "; ".join(covered)
    face.set_vr(False)
    face.set_image(message)
    say("the marks cover no marker", f"every status, at scales {MARK_SCALES}")

    if sys.platform == "win32":
        placement(path, face)

    window = settings_mod.SettingsWindow(opened[0], path)
    face._root.update()
    rows = window._vars

    # One widget per offered setting, and none for anything that is not
    # a setting.
    # HEADER_KEYS are offered by a control above the notebook rather
    # than a row in it, and deliberately stay out of `_vars`: `save`
    # writes everything in there, and the chooser has to write on its
    # own terms. They are checked below instead.
    expected = {
        (section, key)
        for section in settings_mod.sections()
        for key in config_mod.FIELD_TYPES[section]
        if key not in settings_mod.HEADER_KEYS
    }
    assert not expected - set(rows), f"no widget for {sorted(expected - set(rows))}"
    assert not set(rows) - expected, f"a widget for {sorted(set(rows) - expected)}"
    # The compound one, which is three boxes standing in for a variable.
    assert isinstance(rows[("vr", "offset")], settings_mod.Vector3Var)
    assert len(rows[("vr", "offset")].parts) == 3
    say(
        "a widget per setting",
        f"{len(expected)} rows, {len(settings_mod.tabs())} tabs",
    )

    # No tab taller than the sections nobody had to carve. This is the
    # whole of what the split bought: a notebook is as tall as its
    # tallest page, so one long section was setting the height of every
    # other tab and leaving most of them half empty.
    tallest = max(settings_mod.tabs(), key=lambda tab: len(tab[2]))
    ungrouped = max(
        len(keys)
        for label, _section, keys in settings_mod.tabs()
        if label in config_mod.FIELD_TYPES and label not in settings_mod.GROUPS
    )
    assert len(tallest[2]) <= ungrouped, f"{tallest[0]} is {len(tallest[2])} rows"
    say("no tab is over-long", f"tallest is {tallest[0]}, {len(tallest[2])} rows")

    # Nothing has been touched, so there is nothing to write.
    assert window._save_button.instate(["disabled"]), "Save is offered at rest"

    # The variables are what Save reads, so setting one is setting the
    # widget it is attached to.
    before_offset = cfg.vr.offset
    rows[("thresholds", "low_mgdl")].set("80")
    rows[("polling", "alert_haptic")].set(False)
    assert window._save_button.instate(["!disabled"]), "an edit did not offer Save"
    say("Save follows the edits", "off at rest, on once a box changes")

    assert window.save(), "a good value was refused"
    assert window._save_button.instate(["disabled"]), "Save is still offered after it"
    saved = config_mod.load(path)
    assert saved.thresholds.low_mgdl == 80.0, saved.thresholds.low_mgdl
    assert not saved.polling.alert_haptic
    say("Save reaches the file", "low_mgdl = 80, alert_haptic = false")

    text = path.read_text(encoding="utf-8")
    assert LANDMARK in text, "the example's comments are gone"
    say("and keeps the comments", f"{text.count('#')} lines")

    # Placement was not touched, so it has to come through unchanged --
    # and then be changeable from here, which is the whole reason the
    # section is offered at all.
    assert "[vr]" in text, "the placement section was dropped"
    assert saved.vr.offset == before_offset, saved.vr.offset
    for part, value in zip(rows[("vr", "offset")].parts, ("0.0", "-0.03", "0.11")):
        part.set(value)
    assert window.save(), "placement was refused"
    assert config_mod.load(path).vr.offset == (0.0, -0.03, 0.11)
    say("[vr] is editable from here", "offset nudged to (0.0, -0.03, 0.11)")

    # A refused value stays on the window. Taking the process down for a
    # typo would mean losing the face as well.
    unchanged = path.read_text(encoding="utf-8")
    rows[("polling", "interval_sec")].set("5")
    assert not window.save(), "the polling floor was written"
    assert path.read_text(encoding="utf-8") == unchanged, "it wrote anyway"
    complaint = window._status.cget("text")
    assert "30" in complaint, complaint
    assert window.alive(), "a bad value closed the window"
    assert not face.should_quit(), "a bad value closed the face"
    # There is still something unsaved, so the way to fix it stays lit.
    assert window._save_button.instate(["!disabled"]), "Save went away with the error"
    say("a bad value is refused", complaint.split(";")[0])

    # -- the preset chooser -------------------------------------------------

    # Back to something saveable, so the window is at rest again.
    rows[("polling", "interval_sec")].set("60")
    assert window.save(), "could not get back to a clean window"

    # Nothing unsaved, so the chooser is live and Save is not.
    assert window._preset_box.instate(["readonly"]), "the chooser is dead at rest"
    assert window._save_button.instate(["disabled"])

    # The rule the whole design rests on: `save` writes every box,
    # touched or not, so a window showing one preset's numbers while
    # pointed at another would copy the first over the second. The
    # chooser and Save are therefore never both live.
    rows[("thresholds", "low_mgdl")].set("85")
    assert window._preset_box.instate(["disabled"]), "could switch with edits pending"
    assert window._save_button.instate(["!disabled"])
    assert window.save()
    assert window._preset_box.instate(["readonly"]), "the chooser stayed dead"
    say("chooser and Save exclude", "never both live")

    # Switching writes the choice, and the boxes come back holding what
    # the new preset says rather than what the old one did.
    folder = presets_mod.directory(path)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "wrist.toml").write_text(
        '[vr]\noffset = [0.0, 0.0, 0.22]\norbit = true\n', encoding="utf-8"
    )
    assert "wrist" in presets_mod.available(path), presets_mod.available(path)

    window._preset_var.set("wrist")
    window._switch_preset()
    assert config_mod.load(path).vr.preset == "wrist", "the choice was not written"
    assert config_mod.load(path).vr.offset == (0.0, 0.0, 0.22), "the preset lost"
    shown = [float(part) for part in rows[("vr", "offset")].get()]
    assert shown == [0.0, 0.0, 0.22], f"the boxes still show the old preset: {shown}"
    assert window._save_button.instate(["disabled"]), "reloading counted as an edit"
    say("switching reloads the boxes", f"offset now {tuple(shown)}")

    # And the tabs a preset governs say which one, so the answer is on
    # screen rather than remembered.
    labelled = [
        window._notebook.tab(i, "text")
        for i, (_l, _s, keys) in enumerate(settings_mod.tabs())
        if set(keys) & presets_mod.PRESET_KEYS
    ]
    assert labelled, "no tab holds a preset key"
    assert all("[wrist]" in text for text in labelled), labelled
    untouched = [
        window._notebook.tab(i, "text")
        for i, (_l, _s, keys) in enumerate(settings_mod.tabs())
        if not (set(keys) & presets_mod.PRESET_KEYS)
    ]
    assert not any("[wrist]" in text for text in untouched), untouched
    say("tabs name the preset", ", ".join(labelled))

    # Saving now writes the placement to the preset file and leaves
    # config.toml's [vr] alone, or the two would hold different answers
    # to the same question and the preset would silently keep winning.
    for part, value in zip(rows[("vr", "offset")].parts, ("0.0", "0.01", "0.25")):
        part.set(value)
    assert window.save(), "saving under a preset was refused"
    written = (folder / "wrist.toml").read_text(encoding="utf-8")
    assert "0.25" in written, written
    # Read as TOML rather than searched as text: the example config has
    # other numbers that happen to spell the same.
    own = tomllib.loads(path.read_text(encoding="utf-8"))["vr"].get("offset")
    assert own != [0.0, 0.01, 0.25], f"it went to config.toml too: {own}"
    assert config_mod.load(path).vr.offset == (0.0, 0.01, 0.25)
    say("Save reaches the preset", "wrist.toml, not config.toml")

    # Back to none, which is what every config did before presets.
    window._preset_var.set(settings_mod.NO_PRESET)
    window._switch_preset()
    assert config_mod.load(path).vr.preset == ""
    assert all(
        "[" not in window._notebook.tab(i, "text")
        for i in range(len(settings_mod.tabs()))
    ), "a tab kept the preset name"
    say("back to no preset", "config.toml alone again")

    if show:
        window._say("a throwaway copy; nothing saved here reaches your config.toml")
        print("\n  showing it; close the settings window to finish")
        window._top.protocol("WM_DELETE_WINDOW", face._root.quit)
        face._root.mainloop()


def main(argv: list[str]) -> int:
    show = "--show" in argv[1:]

    with tempfile.TemporaryDirectory() as directory:
        path = sample(Path(directory))
        # always_on_top off: this is a check, not something to have to
        # fight with while reading its output.
        with FaceWindow(scale=0.5, always_on_top=False) as face:
            check(path, face, show)

    print("settings window OK")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
