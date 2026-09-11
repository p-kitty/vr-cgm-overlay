"""What the app remembers between runs, as opposed to what it is told.

`config.toml` is settings: written by a person, commented, validated,
and watched, so an edit lands within a second. Where the window was
last dragged to is none of those. It changes every time the window
moves, nobody means to type it, and writing it into config.toml would
put a reload behind every drag and a line in the settings window for a
number that is already set by moving the window. So it is kept apart,
in `state.json` beside the config.

JSON rather than TOML because nothing here is for reading: tomllib
cannot write, and a file only this app writes has no comments to keep.

Nothing in it is worth failing over. A file that is missing, half
written or from some other shape of this app means starting as if it
had never been written, and a file that cannot be saved means the next
run starts where Windows puts it -- both said in the log, neither
stopping anything.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

log = logging.getLogger("vrcgm")

STATE_NAME = "state.json"


def _read(path: Path) -> dict:
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}
    except OSError as exc:
        log.warning("cannot read %s: %s", path, exc)
        return {}
    try:
        state = json.loads(text)
    except ValueError as exc:
        log.warning("ignoring %s, which is not JSON: %s", path, exc)
        return {}
    return state if isinstance(state, dict) else {}


def load_position(path: Path) -> tuple[int, int] | None:
    """Where the window was last left, if that was ever written."""
    window = _read(path).get("window")
    if not isinstance(window, dict):
        return None
    x, y = window.get("x"), window.get("y")
    # bool is an int to Python, and true is not a coordinate.
    if not all(isinstance(v, int) and not isinstance(v, bool) for v in (x, y)):
        return None
    return x, y


def save_position(path: Path, position: tuple[int, int]) -> None:
    """Remember where the window is, keeping anything else in the file.

    Written to a temporary file and moved over the old one, so a sign-out
    that ends the process mid-write leaves the last position rather than
    half of a new one.
    """
    state = _read(path)
    x, y = position
    state["window"] = {"x": x, "y": y}
    temporary = path.with_name(path.name + ".tmp")
    try:
        temporary.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
        os.replace(temporary, path)
    except OSError as exc:
        log.warning("cannot remember the window position in %s: %s", path, exc)
