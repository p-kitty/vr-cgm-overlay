"""Reading config.toml, and writing it back.

config.toml holds the LibreLinkUp password, which grants access to health
data, so it must stay out of the repository (.gitignore already excludes
it).

**A section here is a section in the file.** One dataclass per
`[table]`, one field per key, one annotation per type. A frontend can
then be handed the part that concerns it -- everything under `Vr` needs
a headset, everything under `Window` needs a screen, everything else
needs neither -- and, more to the point, both directions can walk the
same shape.

`load` and `save` are that walk. Adding a setting is adding a field:
reading it, writing it and recognising its name all follow from the
declaration, and there is no second list of keys to keep in step. There
was one until this -- ninety lines of `.get(key, default)` -- and the
symptom of forgetting a line in it was a setting that silently stayed
at its default, which reads as a broken feature rather than a bug. That
is now unwritable, and it is why the annotations are restricted to four
kinds: see `FIELD_KINDS`.

`[vr]` was part of `[display]` until this, on the grounds that moving
those keys would break every existing config.toml to gain nothing a user
could see. Both halves of that expired: there is one such file and this
change edits it, and the sections do become visible as soon as something
other than a text editor offers them. A `hand` left behind in
`[display]` is not silently ignored either -- `_check_keys` names the
section it should be in.
"""

from __future__ import annotations

import difflib
import logging
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import get_type_hints

import tomlkit

from cgm.core.alert import REARM_MGDL, REPEAT_MIN
from cgm.core.librelink import API_VERSION
from cgm.face.graph import AXIS_FLOOR_MGDL, TICK_MAJOR_MIN, GraphTuning
from cgm.face.renderer import STALE_AFTER_MIN, Theme, TrendTuning

log = logging.getLogger(__name__)

# Where a default below is also the default of the thing it tunes, it is
# read off that thing rather than written out again. The face, the alert
# and the client each have to work on their own -- tools/preview.py
# draws with no config at all -- so each needs a default, and two copies
# of 70 mg/dL is how a preview ends up drawn against a threshold the app
# stopped using. The `[vr]` ones are written here because the overlay
# needs openvr to import and takes every value from here anyway.

# The lowest vr.gaze_min_alpha that may be asked for. A face that
# faded to nothing would look exactly like the process having died, which
# is the failure this whole thing exists to avoid, so the floor is a rule
# rather than a default: it has to survive someone turning the dial down.
GAZE_ALPHA_FLOOR = 0.1

# What `window.scale` may be set to. The watch face is rendered once at
# 512x256 and resampled to the window, so scaling down loses detail and
# scaling up cannot invent it.
WINDOW_SCALE_MIN = 0.25
WINDOW_SCALE_MAX = 4.0


@dataclass
class Account:
    """[account]. All of it is restart-only: the client is built once."""

    email: str = ""
    password: str = ""
    # Empty rather than None. "" is what the file holds when these are
    # left blank, both readers already ask them for truthiness --
    # `REGION_URLS.get(region or "")` and `if self._patient_id:` -- and
    # nothing has ever read either one as None. Holding one type means
    # the annotation is the whole story about how the field is read and
    # written, which is what lets both directions be walked.
    patient_id: str = ""
    region: str = ""
    api_version: str = API_VERSION


@dataclass
class Display:
    """[display]. What is true of the face wherever it is being shown.

    Two keys, and both of them are about the reading rather than about
    the screen: which unit it is spelled in, and when it is old enough to
    go grey. Everything that was here about a controller is in `Vr` now.
    """

    unit: str = "mgdl"
    stale_after_min: float = STALE_AFTER_MIN


@dataclass
class Vr:
    """[vr]. Where the face sits on a tracked controller, and when it is lit.

    None of it means anything without a headset, which is why it is a
    section of its own: a frontend with no controller never has to carry
    it, and `--window` can leave every key here alone.
    """

    hand: str = "left"
    width_m: float = 0.14
    offset: tuple[float, float, float] = (0.0, 0.02, 0.10)
    rotation_deg: tuple[float, float, float] = (-40.0, 0.0, 0.0)
    opacity: float = 1.0
    flip_vertical: bool = False
    orbit: bool = False
    orbit_radius_m: float = 0.06
    orbit_limit_deg: float = 120.0
    arm_guide: bool = False
    gaze_fade: bool = False
    gaze_full_deg: float = 20.0
    gaze_fade_deg: float = 45.0
    gaze_min_alpha: float = 0.25


@dataclass
class Window:
    """[window]. How the desktop frontend's window is sized and stacked.

    Read only by `--window`; the overlay ignores this section, the same
    way a window ignores every key under `Vr`.
    """

    scale: float = 1.0
    always_on_top: bool = True


@dataclass
class Graph:
    """[graph]. The history sparkline under the number.

    `in_window` and `in_vr` are separate on purpose. The two frontends
    are looked at differently: a window is read at a desk, where a few
    hours of history is worth the space it takes, and the overlay is
    glanced at mid-game, where the whole design goal is the number in
    half a second. So which of them draws a graph is a per-frontend
    choice rather than one switch that has to be right for both, and
    the defaults say what each is for.

    Everything else here is shared: if both are on, both draw the same
    graph, the same way.
    """

    in_window: bool = True
    in_vr: bool = False
    # How far back to draw. 0 is "all of it": every point the response
    # carried, with the X axis spanning the oldest to the newest rather
    # than a fixed length. Eight hours by default -- long enough to hold
    # a night, short enough that the points are not touching.
    window_min: float = GraphTuning.window_min
    # A minimum, not a ceiling: the axis grows past this only far enough
    # to keep a reading on the chart. The bottom of the axis is not here
    # because it is not settable -- see AXIS_FLOOR_MGDL. See
    # cgm.face.graph for why neither end fits itself to the data.
    axis_high_mgdl: float = GraphTuning.axis_high_mgdl


@dataclass
class Thresholds:
    """[thresholds]. Always mg/dL, whatever the display unit is."""

    low_mgdl: float = Theme.low_mgdl
    high_mgdl: float = Theme.high_mgdl
    very_high_mgdl: float = Theme.very_high_mgdl


@dataclass
class Trend:
    """[trend]. How the arrow's angle is arrived at.

    `window_min` used to be here, and is gone: the arrow draws the last
    half hour point by point now, so the span was no longer something a
    reader could point at. What is left of it is the fallback's window,
    which is a constant in `cgm.core.librelink`.
    """

    local: bool = TrendTuning.local
    fast_mgdl_min: float = TrendTuning.fast_mgdl_min


@dataclass
class Polling:
    """[polling]. How often the API is asked, and what a low does.

    `alert_on_low` is the master switch it has always been; what
    changed is that there is now more than one way to answer it, so the
    channels are named separately underneath. Both default on, which
    keeps an existing config.toml doing what it did and adds the sound
    -- the channel that works on the stack where the buzz does not.
    """

    interval_sec: float = 60.0
    alert_on_low: bool = True
    alert_haptic: bool = True
    alert_sound: bool = True
    sound_path: str = ""
    # How far above low_mgdl a reading has to climb before the next dip
    # counts as a new low. 0 restores the bare threshold test.
    rearm_margin_mgdl: float = REARM_MGDL
    # 0 is off: the alert fires once, on the way in.
    repeat_every_min: float = REPEAT_MIN


@dataclass
class Config:
    account: Account = field(default_factory=Account)
    display: Display = field(default_factory=Display)
    vr: Vr = field(default_factory=Vr)
    window: Window = field(default_factory=Window)
    graph: Graph = field(default_factory=Graph)
    thresholds: Thresholds = field(default_factory=Thresholds)
    trend: Trend = field(default_factory=Trend)
    polling: Polling = field(default_factory=Polling)


# Every setting there is: section, key, and the type it is held as.
# Read off the dataclasses above rather than written out a second time,
# and read with get_type_hints because `from __future__ import
# annotations` leaves every annotation up there as a string.
#
# This is the only such table. Loading, saving and key checking all walk
# it, so the thing that used to be possible -- a field declared and then
# forgotten by one of the three -- has nowhere left to happen.
SECTIONS: dict[str, type] = get_type_hints(Config)
FIELD_TYPES: dict[str, dict[str, type]] = {
    name: get_type_hints(cls) for name, cls in SECTIONS.items()
}

# Three numbers: `offset` and `rotation_deg`, which are the only settings
# here that are not a single value. TOML has no tuples, so they are held
# as one and written as an array.
VECTOR3 = tuple[float, float, float]

# What a setting may be annotated as. Short on purpose. Generic code is
# harder to read than the explicit lines it replaces, and it only stays
# worth it while the number of cases stays small -- each of these has one
# obvious spelling in TOML and one obvious way back out of it. A fifth
# kind is a decision, not an accident, which is what the check below is
# for.
FIELD_KINDS = (str, float, bool, VECTOR3)


def _check_field_kinds(field_types: dict[str, dict[str, type]] | None = None) -> None:
    """Refuse an unsupported annotation at import rather than at load.

    Run below, on the way in. A setting the walk cannot read would
    otherwise load as whatever TOML happened to hand over and save as
    something else, and be found by whoever hit it in the headset rather
    than by whoever added it.
    """
    if field_types is None:
        field_types = FIELD_TYPES
    allowed = ", ".join(str(getattr(k, "__name__", k)) for k in FIELD_KINDS)
    for section, kinds in field_types.items():
        for key, kind in kinds.items():
            if kind not in FIELD_KINDS:
                raise TypeError(
                    f"{section}.{key} is annotated {kind!r}, which config.toml "
                    f"cannot be read or written for; use one of {allowed}"
                )


_check_field_kinds()


# What each section of the file is allowed to contain: the same table
# with the types dropped. A key is recognised exactly when some dataclass
# has a field called that.
SECTION_KEYS: dict[str, frozenset[str]] = {
    name: frozenset(kinds) for name, kinds in FIELD_TYPES.items()
}

# [account] is in that table like everything else. It was briefly exempt,
# on the grounds that it gets pasted in from other clients and an extra
# key there is harmless -- but once an exempt section had to warn anyway
# to be any use, the exemption bought nothing but a second rule. One rule
# is also the direction that can be undone: relaxing a section later
# accepts files that used to be refused, where tightening one later
# refuses files that used to work, at a commit with nothing to do with
# the setting that suddenly stops the app.


def _homes(key: str) -> list[str]:
    """Which sections do recognise `key`. Empty when none do."""
    return sorted(name for name, keys in SECTION_KEYS.items() if key in keys)


def _sections(names: list[str]) -> str:
    """Name every section a key would have been read in.

    No setting currently lives in two of them -- `window_min` did, in
    [trend] and [graph], until the arrow stopped having a window -- but
    the message is still written for the case. "How far back" is
    exactly the kind of key that gets added to a second section, and
    naming only the first would send half the people who misfiled it to
    the wrong place, which is worse than the silence this replaced.
    """
    if len(names) == 1:
        return f"[{names[0]}]"
    return ", ".join(f"[{n}]" for n in names[:-1]) + f" or [{names[-1]}]"


def _nearest(word: str, candidates) -> str | None:
    """The nearest thing that would have worked, or None.

    A misspelling is the common mistake and the hardest to see by
    rereading, so where there is a near miss the message names it.
    """
    near = difflib.get_close_matches(word, sorted(candidates), n=1, cutoff=0.7)
    return near[0] if near else None


def _listed(names) -> str:
    return ", ".join(sorted(names))


def _check_keys(raw: dict) -> None:
    """Refuse a file that contains anything nothing reads.

    `load` reads only the keys a dataclass declares, so a key that is
    misspelled or filed under the wrong section is never looked for, and
    looks exactly like one that is absent. It then does nothing,
    silently, and the only evidence is a setting that appears not to
    work -- which reads as a broken feature rather than a typo.
    `[thresholds]` is why this is an error and not a warning: someone
    raising `low_mgdl` to match their own low would otherwise find out
    when an alert did not fire.

    Every rejection carries something to act on. A near miss is named, a
    misfiled key is sent to its section, and a key that resembles nothing
    -- invented, or carried over from another client -- gets the list of
    what the section does take, because that one has no other clue in it
    and "not a setting" alone leaves the reader to go and find the list.

    Every section is treated the same way, `[account]` included: an
    `api_verison` that kept the default silently would otherwise surface
    as a login the API rejects, hours later, with nothing pointing back
    at the file.

    Raising here also covers the live reload for free, since
    `ConfigWatcher.poll` already keeps the running config when a re-read
    raises. The cost is that a config.toml written against a newer commit
    stops an older checkout from starting -- see docs/configuration.md.
    """
    problems: list[str] = []
    for name, body in raw.items():
        if not isinstance(body, dict):
            # A key above the first [section] header, so nothing ever
            # looks for it. TOML puts it at the top level; we do not.
            homes = _homes(name)
            near = _nearest(name, set().union(*SECTION_KEYS.values()))
            if homes:
                problems.append(
                    f"{name} sits outside any section; it belongs under "
                    f"{_sections(homes)}"
                )
            elif near:
                problems.append(
                    f"{name} sits outside any section; did you mean {near}, "
                    f"under {_sections(_homes(near))}?"
                )
            else:
                problems.append(
                    f"{name} sits outside any section, and is not a setting in "
                    "any of them"
                )
            continue

        allowed = SECTION_KEYS.get(name)
        if allowed is None:
            near = _nearest(name, SECTION_KEYS)
            hint = (
                f"did you mean [{near}]?"
                if near
                else "the sections are " + _listed(f"[{s}]" for s in SECTION_KEYS)
            )
            problems.append(f"[{name}] is not a section; {hint}")
            continue

        for key in body:
            if key in allowed:
                continue
            homes = _homes(key)
            near = _nearest(key, allowed)
            if homes:
                hint = f"it belongs under {_sections(homes)}"
            elif near:
                hint = f"did you mean {near}?"
            else:
                hint = f"[{name}] takes {_listed(allowed)}"
            problems.append(f"{name}.{key} is not a setting; {hint}")

    if problems:
        raise ValueError(
            "config.toml holds settings nothing reads, so they would do "
            "nothing without saying so:\n  "
            + "\n  ".join(problems)
        )


def _kind_name(kind: type) -> str:
    """What to call a type in a message aimed at whoever edits the file."""
    return {
        str: "string",
        float: "number",
        bool: "true or false",
        VECTOR3: "list of three numbers",
    }[kind]


def _as(kind: type, value):
    """One TOML value, as the type its field is declared to hold.

    `bool` before `float`, because bool is a subclass of int and the
    other order would turn `true` into 1.0.
    """
    if kind is bool:
        # `bool("false")` is True, and every other non-empty string is
        # too, so a quoted `"false"` in the file would turn the setting
        # on -- the wrong answer, given without a word. Spell it the way
        # TOML does or say so.
        if isinstance(value, str):
            spelling = value.strip().lower()
            if spelling not in ("true", "false"):
                raise ValueError(f"{value!r} is neither true nor false")
            return spelling == "true"
        return bool(value)
    if kind is float:
        return float(value)
    if kind is str:
        return str(value)
    # VECTOR3. A string is iterable and would come apart into
    # characters, which is a worse error than being refused.
    if isinstance(value, str):
        raise TypeError(f"{value!r} is a string, not three numbers")
    return tuple(float(v) for v in value)


def _read(section: str, key: str, kind: type, value):
    """`_as`, with the key named when the file holds something else.

    Unnamed, the failure is `could not convert string to float: 'left'`,
    which says nothing about which line to go and fix.
    """
    try:
        return _as(kind, value)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"{section}.{key} must be a {_kind_name(kind)}: {value!r}"
        ) from exc


def parse(section: str, key: str, value):
    """One value as the named setting is declared to hold it.

    The way in for anything that has a setting by name and a value that
    is not the right type yet -- a settings window, whose values come out
    of text boxes as strings. It converts exactly as `load` does,
    including the message when the conversion fails, so a number typed
    into the wrong box is refused the same way whether it arrived through
    a widget or through the file.
    """
    return _read(section, key, FIELD_TYPES[section][key], value)


def _written(value, existing):
    """The value as it should appear in the file.

    Two adjustments, both about not changing a line beyond what was
    asked. TOML has no tuple, so VECTOR3 goes out as the array it came
    in as. And a key the file spelled as a whole number stays one:
    `low_mgdl = 70` edited to 75 should read `75`, since every number
    here is held as a float and the trailing `.0` is churn.
    """
    if isinstance(value, tuple):
        return list(value)
    if (
        isinstance(value, float)
        and value.is_integer()
        # bool is a subclass of int, and `true` is not a whole number.
        and isinstance(existing, int)
        and not isinstance(existing, bool)
    ):
        return int(value)
    return value


def load(path: Path) -> Config:
    """Read the config file, falling back to defaults for absent keys.

    Absent is not the same as unrecognised: a key nothing reads is an
    error, not a default. See `_check_keys`.

    The walk is the point. Every setting is read because it is declared,
    so the failure this used to have -- a field added to a dataclass and
    not to the ninety lines of `.get` that were here, which showed up as
    a setting stuck at its default -- cannot be written any more.
    """
    if not path.exists():
        raise FileNotFoundError(f"{path} not found; copy config.example.toml to create it")

    with path.open("rb") as fh:
        raw = tomllib.load(fh)

    # Before anything is read, while the file's own keys still exist.
    # Past this point the unrecognised ones have already been dropped.
    _check_keys(raw)

    cfg = Config()
    for section, kinds in FIELD_TYPES.items():
        body = raw.get(section, {})
        held = getattr(cfg, section)
        for key, kind in kinds.items():
            if key in body:
                setattr(held, key, _read(section, key, kind, body[key]))

    _validate(cfg)
    return cfg


def save(cfg: Config, path: Path) -> None:
    """Write `cfg` back to `path`, keeping the file that is already there.

    tomlkit rather than a fresh dump, because the comments in
    config.toml are the only explanation of most of these settings that
    anyone editing by hand will ever see. Rewriting the file from the
    values alone would throw all of it away, once, silently.

    **Only what actually differs is touched.** A key the file already
    holds is compared through the same conversion `load` uses, so
    `interval_sec = 60` stays as it is rather than being churned to
    `60.0` on every save; a key the file does not hold is added only
    when it is not the default, so saving does not paste all fifty
    settings into a file that was holding six.

    The write goes through a temporary file, because the thing being
    overwritten is the only copy of a password.

    **Validated first, and nothing is written if it fails.** A config
    the loader would refuse is a file the app will not start from, and
    writing one would turn a mistyped threshold into a process that
    comes up dead the next morning. The caller gets the same ValueError
    a load would have raised, with nothing changed on disk.
    """
    _validate(cfg)

    document = (
        tomlkit.parse(path.read_text(encoding="utf-8"))
        if path.exists()
        else tomlkit.document()
    )
    defaults = Config()

    for section, kinds in FIELD_TYPES.items():
        table = document.get(section)
        held = getattr(cfg, section)
        default = getattr(defaults, section)
        for key, kind in kinds.items():
            value = getattr(held, key)
            existing = table[key] if table is not None and key in table else None
            if existing is not None:
                try:
                    if _as(kind, existing) == value:
                        continue
                except (TypeError, ValueError):
                    pass  # unreadable, so it is about to be replaced
            elif value == getattr(default, key):
                # Absent, and what would be written is what absent
                # already means. Adding it only makes the file longer.
                continue
            if table is None:
                table = tomlkit.table()
                document[section] = table
            table[key] = _written(value, existing)

    # Written beside the real file so the move is a rename within one
    # directory, which is the case os.replace makes atomic.
    scratch = path.with_name(path.name + ".new")
    scratch.write_text(tomlkit.dumps(document), encoding="utf-8")
    scratch.replace(path)


def _validate(cfg: Config) -> None:
    """Catch contradictory settings at startup.

    Noticing them after the headset is on is a nuisance to fix.
    """
    if not cfg.account.email:
        raise ValueError("account.email is empty")
    if not cfg.account.password:
        raise ValueError("account.password is empty")
    if cfg.display.unit not in ("mgdl", "mmol"):
        raise ValueError(f"display.unit must be mgdl or mmol: {cfg.display.unit!r}")
    if cfg.vr.hand not in ("left", "right"):
        raise ValueError(f"vr.hand must be left or right: {cfg.vr.hand!r}")
    if len(cfg.vr.offset) != 3 or len(cfg.vr.rotation_deg) != 3:
        raise ValueError("vr.offset and vr.rotation_deg must have three elements")
    if cfg.vr.orbit_radius_m <= 0:
        raise ValueError(
            f"vr.orbit_radius_m must be positive: {cfg.vr.orbit_radius_m}"
        )
    # 180 is a full half turn either way, which is the whole circle.
    if not (0 < cfg.vr.orbit_limit_deg <= 180):
        raise ValueError(
            f"vr.orbit_limit_deg must be in (0, 180]: {cfg.vr.orbit_limit_deg}"
        )

    # 180 is the whole hemisphere behind you, so a fade that only ever
    # reaches its floor when the face is directly at your back is legal,
    # if pointless. full == fade is not: it would step rather than fade.
    if not (0 <= cfg.vr.gaze_full_deg < cfg.vr.gaze_fade_deg <= 180):
        raise ValueError(
            "vr.gaze_full_deg and gaze_fade_deg must satisfy "
            f"0 <= full < fade <= 180: {cfg.vr.gaze_full_deg} / {cfg.vr.gaze_fade_deg}"
        )
    if not (GAZE_ALPHA_FLOOR <= cfg.vr.gaze_min_alpha <= 1):
        raise ValueError(
            f"vr.gaze_min_alpha must be between {GAZE_ALPHA_FLOOR} and 1: "
            f"{cfg.vr.gaze_min_alpha}. A face that fades to nothing looks exactly "
            "like the process having died, which is the failure this exists to "
            "avoid, so it always leaves something on screen"
        )

    # The face is drawn at one fixed size and scaled on the way to the
    # window, so this is a resampling factor rather than a layout knob.
    # Below the floor the digits stop being glanceable, which is the
    # entire point of them; above the ceiling it is upscaling a 512px
    # image and going soft. Both are checked even without --window, so a
    # typo is caught at startup rather than by the frontend that happens
    # to read it.
    if not (WINDOW_SCALE_MIN <= cfg.window.scale <= WINDOW_SCALE_MAX):
        raise ValueError(
            f"window.scale must be between {WINDOW_SCALE_MIN} and "
            f"{WINDOW_SCALE_MAX}: {cfg.window.scale}"
        )

    th = cfg.thresholds
    if not (th.low_mgdl < th.high_mgdl < th.very_high_mgdl):
        raise ValueError(
            "thresholds must satisfy low < high < very_high: "
            f"{th.low_mgdl} / {th.high_mgdl} / {th.very_high_mgdl}"
        )

    # Checked whether or not either frontend is drawing a graph, for the
    # reason the trend settings below are: these reload with everything
    # else, and a setting only rejected at the moment it starts being
    # used is rejected at the worst possible moment.
    gr = cfg.graph
    # The band showing the target range is the whole reason the trace
    # can be read without an axis drawn beside it. An axis that does not
    # contain the range clips the band against an edge, where it stops
    # looking like a band and starts looking like the graph having a
    # floor or a ceiling. The top only ever grows from the configured
    # value, so checking that is checking the smallest axis there can
    # be; the bottom never moves at all.
    if not (AXIS_FLOOR_MGDL <= th.low_mgdl and th.high_mgdl <= gr.axis_high_mgdl):
        raise ValueError(
            "the graph axis must contain the target range: "
            f"axis {AXIS_FLOOR_MGDL:.0f}-{gr.axis_high_mgdl} does not hold "
            f"thresholds {th.low_mgdl}-{th.high_mgdl}. The bottom of the "
            "graph is fixed, so a low_mgdl under it means raising "
            "thresholds.low_mgdl rather than lowering the axis"
        )
    # 0 asks for all the history there is, so there is no length to
    # check. Any other value is one, and the time axis is labelled at
    # fixed points on the local clock rather than at whatever divides
    # the window, so a window shorter than that step can land between
    # two of them and come out with no label at all. A negative is not a
    # third meaning; it lands here too.
    #
    # This also covers the older reason, which still holds and is no
    # longer the binding one: history arrives one point every fifteen
    # minutes, so a window has to be at least thirty to hold the two
    # that make a line.
    graph_floor = TICK_MAJOR_MIN
    if gr.window_min and gr.window_min < graph_floor:
        raise ValueError(
            f"graph.window_min must be 0, for all the history there is, or "
            f"at least {graph_floor:.0f}; the time axis is labelled every "
            f"{TICK_MAJOR_MIN / 60:.0f} hours on the wall clock, so a shorter "
            f"window can fall between two labels and be drawn with none: "
            f"{gr.window_min}"
        )

    # Checked whether or not the fit is switched on. `local` is flipped
    # from inside the headset like everything else here, and a setting
    # that is only rejected at the moment it starts being used is
    # rejected at the worst possible moment.
    #
    # It divides the rate, so zero is a crash on the first reading
    # rather than a wrong angle.
    if cfg.trend.fast_mgdl_min <= 0:
        raise ValueError(
            "trend.fast_mgdl_min must be positive: "
            f"{cfg.trend.fast_mgdl_min}"
        )

    # A negative margin would re-arm the alert below the threshold, so
    # a reading sitting just under low_mgdl would announce itself over
    # and over. Zero is allowed and means the bare threshold test.
    if cfg.polling.rearm_margin_mgdl < 0:
        raise ValueError(
            "polling.rearm_margin_mgdl must not be negative: "
            f"{cfg.polling.rearm_margin_mgdl}"
        )
    # Off, or slower than the fetch it reacts to. Anything under a
    # minute would re-announce the same reading, since a new one only
    # arrives about that often.
    if cfg.polling.repeat_every_min and cfg.polling.repeat_every_min < 1:
        raise ValueError(
            "polling.repeat_every_min must be 0 (fire once) or at least 1; "
            "the sensor updates about once a minute, so anything shorter "
            f"would repeat on the same reading: {cfg.polling.repeat_every_min}"
        )
    # Caught here rather than at the moment a low arrives. A typo in
    # this path is otherwise invisible until the one time it matters.
    if cfg.polling.sound_path:
        sound = Path(cfg.polling.sound_path)
        if sound.suffix.lower() != ".wav":
            raise ValueError(
                "polling.sound_path must be a .wav; PlaySound reads nothing "
                f"else, and a decoder is a dependency this does not carry: "
                f"{cfg.polling.sound_path}"
            )
        if not sound.exists():
            raise ValueError(
                f"polling.sound_path does not exist: {cfg.polling.sound_path}"
            )

    # Polling harder than the official app risks being rate limited or cut
    # off. The sensor itself only updates about once a minute, so a shorter
    # interval cannot return anything new anyway.
    if cfg.polling.interval_sec < 30:
        raise ValueError(
            "polling.interval_sec must not go below 30; the sensor updates "
            "about once a minute, so polling faster returns nothing new and "
            "only risks the account being blocked"
        )
