"""Placement presets: where the face sits, kept one file per answer.

A watch face on the back of the hand and a watch face on the forearm
are not the same placement with one number changed. They disagree about
`offset`, about `rotation_deg`, and about whether the elbow model means
anything at all, so keeping both in `[vr]` means keeping one of them in
a text file somewhere and pasting it back. Worse, the two share key
names whose meaning depends on the other keys, so a half-applied swap
is a placement that is wrong in a way neither of them ever was.

So a preset is a file. `config.toml` says which one is live:

    [vr]
    preset = "wrist"

and `presets/wrist.toml` holds the placement keys, and only those:

    [vr]
    offset = [0.0, 0.0, 0.20]
    rotation_deg = [180.0, 70.0, -20.0]
    elbow_model = true

Files rather than a `[vr.presets.wrist]` table under `[vr]`, because
`cgm.core.config` reads and writes every setting by walking two levels
-- section, then key -- and that one walk is what load, save and the
key check all share. A third level would be a third case in each of
them, including in the comment-preserving save, which is the part of
this codebase it would be worst to complicate. A preset file is just a
`[vr]` section, so none of that has to change.

**What belongs in a preset is where the face goes; what belongs in
config.toml is everything that stays true whichever answer you pick.**
Your arm is the same arm, so `arm_length_m` and `wrist_m` are not
per-preset. Which hand you hold the controller in does not change with
the placement either, and neither does the gaze fade. `PRESET_KEYS`
below is that line drawn, and it is the only thing here worth arguing
about.

Empty `preset` means no preset file is read at all, which is exactly
what every config did before this existed.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

import tomlkit

log = logging.getLogger(__name__)

DIR_NAME = "presets"
SUFFIX = ".toml"

# The section a preset file holds. One, and always this one: a preset is
# a placement, and nothing outside [vr] places anything.
SECTION = "vr"

# The key in [vr] that names the live preset. It is not a placement: it
# says which file the placement comes from, so nothing downstream of
# the config -- not the overlay, not the window -- is handed it. The
# settings window gives it a control of its own rather than a row, for
# the reason in `cgm.desk.settings`.
SELECTOR = "preset"

# The keys a preset owns. Everything else under [vr] stays in
# config.toml and is shared by every preset.
#
#   - offset and rotation_deg are the placement itself.
#   - orbit and its two numbers, because riding round the arm is right
#     for a face on the forearm and pointless for one on the hand.
#   - width_m and flip_vertical, because a face read at a different
#     place on the arm is read at a different size and angle.
#
# Deliberately NOT here: hand, opacity, arm_guide and the gaze
# settings. Those are your hardware and your eyes, and they do not
# change when you decide where on your arm to put a number.
#
# Every name here has to be a real `[vr]` setting. `cgm.core.config`
# checks that at import, because a key that is nothing would be written
# to the preset file, read back, and rejected by the key check -- at
# startup, from a file nobody had touched.
PRESET_KEYS = frozenset(
    {
        "offset",
        "rotation_deg",
        "orbit",
        "orbit_radius_m",
        "orbit_limit_deg",
        "flip_vertical",
        "width_m",
    }
)

# What a preset may be called. It becomes a filename, so the characters
# that mean something to a filesystem are refused rather than escaped:
# a preset named "../config" would otherwise be a way to write outside
# the presets directory from a text file.
NAME_PATTERN = re.compile(r"\A[A-Za-z0-9][A-Za-z0-9._-]*\Z")
NAME_MAX = 64

HEADER = """# A placement preset. config.toml chooses it with `preset = "{name}"`,
# and everything in it overrides the same key in config.toml's [vr]
# section while it is chosen.
#
# Only the keys that decide where the face goes belong here. Your body
# and your hardware -- hand, wrist_m, arm_length_m, the shoulder
# numbers, opacity, arm_guide and the gaze settings -- stay in
# config.toml, shared by every preset.
"""


def directory(config_path: Path) -> Path:
    """Where presets live: beside whatever config.toml is in use.

    Beside it rather than at a fixed path, so `--config` and the frozen
    build carry their presets with them instead of reaching back into a
    checkout that may not be there.
    """
    return Path(config_path).parent / DIR_NAME


def path_for(config_path: Path, name: str) -> Path:
    """The file a preset name refers to. Raises on a name that cannot be one."""
    check_name(name)
    return directory(config_path) / f"{name}{SUFFIX}"


def check_name(name: str) -> None:
    """Refuse a preset name that would not make a safe filename.

    `vr.preset` comes out of a text file that a person edits, so it is
    input. A name is letters, digits, dot, dash and underscore, starting
    with a letter or digit. That rules out an empty name, a leading dot,
    a path separator and `..`, which is the one that matters: the name
    becomes a path, and a preset called `../config` would write over the
    file holding the password.
    """
    if not name:
        raise ValueError("vr.preset is empty; leave the key out to use no preset")
    if len(name) > NAME_MAX:
        raise ValueError(f"vr.preset is too long, over {NAME_MAX} characters: {name!r}")
    if not NAME_PATTERN.match(name):
        raise ValueError(
            f"vr.preset must be letters, digits, dot, dash or underscore, "
            f"starting with a letter or digit: {name!r}"
        )


def available(config_path: Path) -> list[str]:
    """Every preset that exists, sorted, for the settings window to offer.

    A file whose name could not be typed into `vr.preset` is skipped
    rather than offered: choosing it would only produce the error
    `check_name` already raises.
    """
    folder = directory(config_path)
    if not folder.is_dir():
        return []
    names = []
    for entry in folder.glob(f"*{SUFFIX}"):
        name = entry.name[: -len(SUFFIX)]
        if NAME_PATTERN.match(name) and len(name) <= NAME_MAX:
            names.append(name)
    return sorted(names)


def read(path: Path, allowed: frozenset[str]) -> dict:
    """The `[vr]` table out of a preset file, checked but not converted.

    `allowed` is the set of keys a preset may hold, handed in rather
    than imported, so this module does not have to reach back into
    `cgm.core.config` and make the import circular.

    Held to the same standard as config.toml: a key nothing reads is an
    error rather than a shrug. The reasoning is the one in
    `config._check_keys`, and it is sharper here, because a key that
    belongs in config.toml but was typed into a preset file looks
    exactly like one that is working.
    """
    if not path.is_file():
        raise FileNotFoundError(
            f"{path} not found; vr.preset names a preset that does not exist"
        )

    document = tomlkit.parse(path.read_text(encoding="utf-8"))
    body = {}
    for name, table in document.items():
        if name != SECTION:
            raise ValueError(
                f"{path.name} has a [{name}] section; a preset holds [{SECTION}] "
                "and nothing else"
            )
        body = dict(table)

    for key in body:
        if key in allowed:
            continue
        raise ValueError(
            f"{path.name}: {key} is not a preset setting. A preset holds "
            + ", ".join(sorted(allowed))
            + f"; everything else under [{SECTION}] stays in config.toml"
        )
    return body


def write(path: Path, values: dict, written) -> None:
    """Put `values` into the preset file, keeping the comments it has.

    `written` converts one value into what TOML should hold, and is
    handed in for the same reason `allowed` is above. It is called with
    the new value and whatever the file already had under that key, so
    a value that has not really changed keeps its existing spelling.

    Every key in `values` is written. A preset is the complete answer to
    where the face goes, so one left out would be silently inherited
    from config.toml and read as the preset having been applied.
    """
    path.parent.mkdir(parents=True, exist_ok=True)

    if path.is_file():
        document = tomlkit.parse(path.read_text(encoding="utf-8"))
    else:
        document = tomlkit.parse(HEADER.format(name=path.name[: -len(SUFFIX)]))

    table = document.get(SECTION)
    if table is None:
        table = tomlkit.table()
        document[SECTION] = table

    for key, value in values.items():
        existing = table[key] if key in table else None
        table[key] = written(value, existing)

    # Beside the real file, so the move is a rename inside one
    # directory, which is the case os.replace makes atomic. Same
    # reasoning as config.save, minus the password.
    scratch = path.with_name(path.name + ".new")
    scratch.write_text(tomlkit.dumps(document), encoding="utf-8")
    scratch.replace(path)
