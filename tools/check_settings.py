"""Open the settings window the way a user does, and press Save for them.

`tests/test_settings.py` covers everything between the boxes and the
file, but it deliberately does not start Tk -- a suite that opened
windows on whoever ran it would be a nuisance, and the same rule keeps
`tests/test_desk.py` away from a root. So the half that only exists once
Tk is running has nothing over it: the right-click that opens the thing,
whether a row is built for every setting, whether the variables are
really attached to the widgets, whether Save reaches the file, and
whether a rejected value says so on the window instead of taking the
face down with it.

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
import tkinter as tk
from pathlib import Path

from cgm.core import config as config_mod
from cgm.desk import settings as settings_mod
from cgm.desk.window import FaceWindow
from cgm.main import build_renderer

ROOT = Path(__file__).resolve().parents[1]

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


def check(path: Path, face: FaceWindow, show: bool) -> None:
    cfg = config_mod.load(path)
    face.set_image(
        build_renderer(cfg, with_graph=False, rounded=False).render_message("SETTINGS")
    )

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

    window = settings_mod.SettingsWindow(opened[0], path)
    face._root.update()
    rows = window._vars

    # One widget per offered setting, and none for the section that is
    # deliberately missing.
    expected = {
        (section, key)
        for section in settings_mod.sections()
        for key in config_mod.FIELD_TYPES[section]
    }
    assert not expected - set(rows), f"no widget for {sorted(expected - set(rows))}"
    assert not set(rows) - expected, f"a widget for {sorted(set(rows) - expected)}"
    assert not any(section == "vr" for section, _ in rows), "[vr] is offered"
    say("a widget per setting", f"{len(expected)} rows, {len(settings_mod.sections())} tabs")

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

    # Placement was never on screen, so it has to come through untouched.
    assert "[vr]" in text, "the section the window does not show was dropped"
    assert saved.vr.offset == before_offset, saved.vr.offset
    say("[vr] survives a save", f"offset still {saved.vr.offset}")

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

    if show:
        rows[("polling", "interval_sec")].set("60")
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
