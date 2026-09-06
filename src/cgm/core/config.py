"""Configuration loading.

config.toml holds the LibreLinkUp password, which grants access to health
data, so it must stay out of the repository (.gitignore already excludes
it).

The Python side is split into sections so a frontend can be handed the
part that concerns it: everything under `Vr` needs a headset, everything
under `Window` needs a screen, everything else needs neither. **The file
format is only partly split to match.** `hand`, `offset` and the rest of
the VR keys stay in `[display]` where they have always been, because
moving them would break every existing config.toml to gain nothing a
user can see. `[window]` is a section of its own because it is new, so
there is no existing file for it to break.
"""

from __future__ import annotations

import difflib
import logging
import tomllib
from dataclasses import dataclass, field, fields
from pathlib import Path

from cgm.core.librelink import GRAPH_RESOLUTION_MIN, MIN_FIT_POINTS

log = logging.getLogger(__name__)

# The lowest display.gaze_min_alpha that may be asked for. A face that
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
    patient_id: str | None = None
    region: str | None = None
    api_version: str = "4.16.0"


@dataclass
class Display:
    """The part of [display] that any frontend has to answer."""

    unit: str = "mgdl"
    stale_after_min: float = 10.0


@dataclass
class Vr:
    """The rest of [display]: where the face sits on a tracked controller.

    Read from `[display]` like the two above, because that is where these
    keys have always lived. Only the Python side is separated, so a
    frontend with no headset never has to carry them.
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
class Thresholds:
    """[thresholds]. Always mg/dL, whatever the display unit is."""

    low_mgdl: float = 70.0
    high_mgdl: float = 180.0
    very_high_mgdl: float = 240.0


@dataclass
class Trend:
    """[trend]. How the arrow's angle is arrived at."""

    local: bool = True
    window_min: float = 60.0
    fast_mgdl_min: float = 2.0


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
    rearm_margin_mgdl: float = 5.0
    # 0 is off: the alert fires once, on the way in.
    repeat_every_min: float = 0.0


@dataclass
class Config:
    account: Account = field(default_factory=Account)
    display: Display = field(default_factory=Display)
    vr: Vr = field(default_factory=Vr)
    window: Window = field(default_factory=Window)
    thresholds: Thresholds = field(default_factory=Thresholds)
    trend: Trend = field(default_factory=Trend)
    polling: Polling = field(default_factory=Polling)


def _keys_of(*classes: type) -> frozenset[str]:
    return frozenset(f.name for cls in classes for f in fields(cls))


# What each section of the file is allowed to contain. Read off the
# dataclasses rather than written out a second time: every field above is
# loaded under its own name, so a key is recognised exactly when some
# dataclass has a field called that, and adding a setting cannot forget
# to register it here. `[display]` is the one section that fills two
# dataclasses -- see the module docstring for why it stays one section.
SECTION_KEYS: dict[str, frozenset[str]] = {
    "display": _keys_of(Display, Vr),
    "window": _keys_of(Window),
    "thresholds": _keys_of(Thresholds),
    "trend": _keys_of(Trend),
    "polling": _keys_of(Polling),
}

# [account] takes whatever it is given. It is the section people paste
# into from elsewhere -- another client's config, a support thread -- and
# an extra key there costs nothing, where a rejected one would stop the
# app over something harmless.
PERMISSIVE_SECTIONS = frozenset({"account"})


def _homes(key: str) -> list[str]:
    """Which sections do recognise `key`. Empty when none do."""
    return sorted(name for name, keys in SECTION_KEYS.items() if key in keys)


def _did_you_mean(word: str, candidates, *, section: bool = False) -> str:
    """The nearest thing that would have worked, when there is one.

    A misspelling is the common case and the one hardest to see by
    rereading, so the message names the key it was probably meant to be
    rather than only saying no.
    """
    near = difflib.get_close_matches(word, sorted(candidates), n=1, cutoff=0.7)
    if not near:
        return ""
    return f"; did you mean [{near[0]}]?" if section else f"; did you mean {near[0]}?"


def _check_keys(raw: dict) -> None:
    """Refuse a file that contains anything nothing reads.

    Every setting below is read with `.get(key, default)`, which cannot
    tell a key that is absent from one that is misspelled or filed under
    the wrong section. Both then do nothing, silently, and the only
    evidence is a setting that appears not to work -- which reads as a
    broken feature rather than a typo. `[thresholds]` is why this is an
    error and not a warning: someone raising `low_mgdl` to match their
    own low would otherwise find out when an alert did not fire.

    Raising here also covers the live reload for free, since
    `ConfigWatcher.poll` already keeps the running config when a re-read
    raises. The cost is that a config.toml written against a newer commit
    stops an older checkout from starting -- see README.md.
    """
    problems: list[str] = []
    for name, body in raw.items():
        if not isinstance(body, dict):
            # A key above the first [section] header, so nothing ever
            # looks for it. TOML puts it at the top level; we do not.
            homes = _homes(name)
            where = (
                f"; it belongs under [{homes[0]}]"
                if homes
                else _did_you_mean(name, set().union(*SECTION_KEYS.values()))
            )
            problems.append(f"{name} sits outside any section{where}")
            continue
        if name in PERMISSIVE_SECTIONS:
            continue
        allowed = SECTION_KEYS.get(name)
        if allowed is None:
            known = set(SECTION_KEYS) | PERMISSIVE_SECTIONS
            problems.append(
                f"[{name}] is not a section"
                + _did_you_mean(name, known, section=True)
            )
            continue
        for key in body:
            if key in allowed:
                continue
            homes = _homes(key)
            where = (
                f"; it belongs under [{homes[0]}]"
                if homes
                else _did_you_mean(key, allowed)
            )
            problems.append(f"{name}.{key} is not a setting{where}")

    if problems:
        raise ValueError(
            "config.toml holds settings nothing reads, so they would do "
            "nothing without saying so:\n  "
            + "\n  ".join(problems)
        )


def load(path: Path) -> Config:
    """Read the config file, falling back to defaults for absent keys.

    Absent is not the same as unrecognised: a key nothing reads is an
    error, not a default. See `_check_keys`.
    """
    if not path.exists():
        raise FileNotFoundError(f"{path} not found; copy config.example.toml to create it")

    with path.open("rb") as fh:
        raw = tomllib.load(fh)

    # Before anything is read, while the file's own keys still exist.
    # Past this point the unrecognised ones have already been dropped.
    _check_keys(raw)

    account = raw.get("account", {})
    display = raw.get("display", {})
    window = raw.get("window", {})
    thresholds = raw.get("thresholds", {})
    trend = raw.get("trend", {})
    polling = raw.get("polling", {})

    cfg = Config()
    acc = cfg.account
    acc.email = account.get("email", "")
    acc.password = account.get("password", "")
    acc.patient_id = account.get("patient_id") or None
    acc.region = account.get("region") or None
    acc.api_version = account.get("api_version", acc.api_version)

    cfg.display.unit = display.get("unit", cfg.display.unit)
    cfg.display.stale_after_min = float(
        display.get("stale_after_min", cfg.display.stale_after_min)
    )

    # Same [display] table, second half: the keys only the VR frontend
    # reads. One section in the file, two on the Python side.
    vr = cfg.vr
    vr.hand = display.get("hand", vr.hand)
    vr.width_m = float(display.get("width_m", vr.width_m))
    vr.offset = tuple(display.get("offset", vr.offset))  # type: ignore[assignment]
    vr.rotation_deg = tuple(  # type: ignore[assignment]
        display.get("rotation_deg", vr.rotation_deg)
    )
    vr.opacity = float(display.get("opacity", vr.opacity))
    vr.flip_vertical = bool(display.get("flip_vertical", vr.flip_vertical))
    vr.orbit = bool(display.get("orbit", vr.orbit))
    vr.orbit_radius_m = float(display.get("orbit_radius_m", vr.orbit_radius_m))
    vr.orbit_limit_deg = float(display.get("orbit_limit_deg", vr.orbit_limit_deg))
    vr.arm_guide = bool(display.get("arm_guide", vr.arm_guide))
    vr.gaze_fade = bool(display.get("gaze_fade", vr.gaze_fade))
    vr.gaze_full_deg = float(display.get("gaze_full_deg", vr.gaze_full_deg))
    vr.gaze_fade_deg = float(display.get("gaze_fade_deg", vr.gaze_fade_deg))
    vr.gaze_min_alpha = float(display.get("gaze_min_alpha", vr.gaze_min_alpha))

    win = cfg.window
    win.scale = float(window.get("scale", win.scale))
    win.always_on_top = bool(window.get("always_on_top", win.always_on_top))

    th = cfg.thresholds
    th.low_mgdl = float(thresholds.get("low_mgdl", th.low_mgdl))
    th.high_mgdl = float(thresholds.get("high_mgdl", th.high_mgdl))
    th.very_high_mgdl = float(thresholds.get("very_high_mgdl", th.very_high_mgdl))

    cfg.trend.local = bool(trend.get("local", cfg.trend.local))
    cfg.trend.window_min = float(trend.get("window_min", cfg.trend.window_min))
    cfg.trend.fast_mgdl_min = float(
        trend.get("fast_mgdl_min", cfg.trend.fast_mgdl_min)
    )

    cfg.polling.interval_sec = float(
        polling.get("interval_sec", cfg.polling.interval_sec)
    )
    pol = cfg.polling
    pol.alert_on_low = bool(polling.get("alert_on_low", pol.alert_on_low))
    pol.alert_haptic = bool(polling.get("alert_haptic", pol.alert_haptic))
    pol.alert_sound = bool(polling.get("alert_sound", pol.alert_sound))
    pol.sound_path = str(polling.get("sound_path", pol.sound_path))
    pol.rearm_margin_mgdl = float(
        polling.get("rearm_margin_mgdl", pol.rearm_margin_mgdl)
    )
    pol.repeat_every_min = float(
        polling.get("repeat_every_min", pol.repeat_every_min)
    )

    _validate(cfg)
    return cfg


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
        raise ValueError(f"display.hand must be left or right: {cfg.vr.hand!r}")
    if len(cfg.vr.offset) != 3 or len(cfg.vr.rotation_deg) != 3:
        raise ValueError("display.offset and rotation_deg must have three elements")
    if cfg.vr.orbit_radius_m <= 0:
        raise ValueError(
            f"display.orbit_radius_m must be positive: {cfg.vr.orbit_radius_m}"
        )
    # 180 is a full half turn either way, which is the whole circle.
    if not (0 < cfg.vr.orbit_limit_deg <= 180):
        raise ValueError(
            f"display.orbit_limit_deg must be in (0, 180]: {cfg.vr.orbit_limit_deg}"
        )

    # 180 is the whole hemisphere behind you, so a fade that only ever
    # reaches its floor when the face is directly at your back is legal,
    # if pointless. full == fade is not: it would step rather than fade.
    if not (0 <= cfg.vr.gaze_full_deg < cfg.vr.gaze_fade_deg <= 180):
        raise ValueError(
            "display.gaze_full_deg and gaze_fade_deg must satisfy "
            f"0 <= full < fade <= 180: {cfg.vr.gaze_full_deg} / {cfg.vr.gaze_fade_deg}"
        )
    if not (GAZE_ALPHA_FLOOR <= cfg.vr.gaze_min_alpha <= 1):
        raise ValueError(
            f"display.gaze_min_alpha must be between {GAZE_ALPHA_FLOOR} and 1: "
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

    # These are checked whether or not the fit is switched on. `local`
    # is flipped from inside the headset like everything else here, and
    # a setting that is only rejected at the moment it starts being used
    # is rejected at the worst possible moment.
    #
    # The history arrives at one point every GRAPH_RESOLUTION_MIN, so a
    # window has to be long enough for MIN_FIT_POINTS of them to land in
    # it. Shorter and the arrow silently falls back to the API's five
    # buckets forever, rather than failing where it was set -- which is
    # exactly what a window of 15 did before this floor existed.
    window_floor = MIN_FIT_POINTS * GRAPH_RESOLUTION_MIN
    if cfg.trend.window_min < window_floor:
        raise ValueError(
            f"trend.window_min must be at least {window_floor:.0f}; the API "
            f"sends one point every ~{GRAPH_RESOLUTION_MIN:.0f} minutes, so a "
            f"shorter window will not reliably hold the {MIN_FIT_POINTS} "
            f"needed to fit a slope: {cfg.trend.window_min}"
        )
    # It divides the slope, so zero is a crash on the first reading
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
