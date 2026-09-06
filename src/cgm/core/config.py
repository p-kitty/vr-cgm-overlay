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

import logging
import tomllib
from dataclasses import dataclass, field
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
    """[polling]. How often the API is asked, and what a low does."""

    interval_sec: float = 60.0
    alert_on_low: bool = True


@dataclass
class Config:
    account: Account = field(default_factory=Account)
    display: Display = field(default_factory=Display)
    vr: Vr = field(default_factory=Vr)
    window: Window = field(default_factory=Window)
    thresholds: Thresholds = field(default_factory=Thresholds)
    trend: Trend = field(default_factory=Trend)
    polling: Polling = field(default_factory=Polling)


def load(path: Path) -> Config:
    """Read the config file, falling back to defaults for absent keys."""
    if not path.exists():
        raise FileNotFoundError(f"{path} not found; copy config.example.toml to create it")

    with path.open("rb") as fh:
        raw = tomllib.load(fh)

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
    cfg.polling.alert_on_low = bool(
        polling.get("alert_on_low", cfg.polling.alert_on_low)
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

    # Polling harder than the official app risks being rate limited or cut
    # off. The sensor itself only updates about once a minute, so a shorter
    # interval cannot return anything new anyway.
    if cfg.polling.interval_sec < 30:
        raise ValueError(
            "polling.interval_sec must not go below 30; the sensor updates "
            "about once a minute, so polling faster returns nothing new and "
            "only risks the account being blocked"
        )
