"""Configuration loading.

config.toml holds the LibreLinkUp password, which grants access to health
data, so it must stay out of the repository (.gitignore already excludes
it).
"""

from __future__ import annotations

import logging
import tomllib
from dataclasses import dataclass
from pathlib import Path

from librelink import GRAPH_RESOLUTION_MIN, MIN_FIT_POINTS

log = logging.getLogger(__name__)

# The lowest display.gaze_min_alpha that may be asked for. A face that
# faded to nothing would look exactly like the process having died, which
# is the failure this whole thing exists to avoid, so the floor is a rule
# rather than a default: it has to survive someone turning the dial down.
GAZE_ALPHA_FLOOR = 0.1


@dataclass
class Config:
    email: str = ""
    password: str = ""
    patient_id: str | None = None
    region: str | None = None
    api_version: str = "4.16.0"

    unit: str = "mgdl"
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
    stale_after_min: float = 10.0

    low_mgdl: float = 70.0
    high_mgdl: float = 180.0
    very_high_mgdl: float = 240.0

    trend_local: bool = True
    trend_window_min: float = 60.0
    trend_fast_mgdl_min: float = 2.0

    poll_interval_sec: float = 60.0
    alert_on_low: bool = True


def load(path: Path) -> Config:
    """Read the config file, falling back to defaults for absent keys."""
    if not path.exists():
        raise FileNotFoundError(f"{path} not found; copy config.example.toml to create it")

    with path.open("rb") as fh:
        raw = tomllib.load(fh)

    account = raw.get("account", {})
    display = raw.get("display", {})
    thresholds = raw.get("thresholds", {})
    trend = raw.get("trend", {})
    polling = raw.get("polling", {})

    cfg = Config()
    cfg.email = account.get("email", "")
    cfg.password = account.get("password", "")
    cfg.patient_id = account.get("patient_id") or None
    cfg.region = account.get("region") or None
    cfg.api_version = account.get("api_version", cfg.api_version)

    cfg.unit = display.get("unit", cfg.unit)
    cfg.hand = display.get("hand", cfg.hand)
    cfg.width_m = float(display.get("width_m", cfg.width_m))
    cfg.offset = tuple(display.get("offset", cfg.offset))  # type: ignore[assignment]
    cfg.rotation_deg = tuple(  # type: ignore[assignment]
        display.get("rotation_deg", cfg.rotation_deg)
    )
    cfg.opacity = float(display.get("opacity", cfg.opacity))
    cfg.flip_vertical = bool(display.get("flip_vertical", cfg.flip_vertical))
    cfg.orbit = bool(display.get("orbit", cfg.orbit))
    cfg.orbit_radius_m = float(display.get("orbit_radius_m", cfg.orbit_radius_m))
    cfg.orbit_limit_deg = float(display.get("orbit_limit_deg", cfg.orbit_limit_deg))
    cfg.arm_guide = bool(display.get("arm_guide", cfg.arm_guide))
    cfg.gaze_fade = bool(display.get("gaze_fade", cfg.gaze_fade))
    cfg.gaze_full_deg = float(display.get("gaze_full_deg", cfg.gaze_full_deg))
    cfg.gaze_fade_deg = float(display.get("gaze_fade_deg", cfg.gaze_fade_deg))
    cfg.gaze_min_alpha = float(display.get("gaze_min_alpha", cfg.gaze_min_alpha))
    cfg.stale_after_min = float(display.get("stale_after_min", cfg.stale_after_min))

    cfg.low_mgdl = float(thresholds.get("low_mgdl", cfg.low_mgdl))
    cfg.high_mgdl = float(thresholds.get("high_mgdl", cfg.high_mgdl))
    cfg.very_high_mgdl = float(thresholds.get("very_high_mgdl", cfg.very_high_mgdl))

    cfg.trend_local = bool(trend.get("local", cfg.trend_local))
    cfg.trend_window_min = float(trend.get("window_min", cfg.trend_window_min))
    cfg.trend_fast_mgdl_min = float(
        trend.get("fast_mgdl_min", cfg.trend_fast_mgdl_min)
    )

    cfg.poll_interval_sec = float(polling.get("interval_sec", cfg.poll_interval_sec))
    cfg.alert_on_low = bool(polling.get("alert_on_low", cfg.alert_on_low))

    _validate(cfg)
    return cfg


def _validate(cfg: Config) -> None:
    """Catch contradictory settings at startup.

    Noticing them after the headset is on is a nuisance to fix.
    """
    if not cfg.email:
        raise ValueError("account.email is empty")
    if not cfg.password:
        raise ValueError("account.password is empty")
    if cfg.unit not in ("mgdl", "mmol"):
        raise ValueError(f"display.unit must be mgdl or mmol: {cfg.unit!r}")
    if cfg.hand not in ("left", "right"):
        raise ValueError(f"display.hand must be left or right: {cfg.hand!r}")
    if len(cfg.offset) != 3 or len(cfg.rotation_deg) != 3:
        raise ValueError("display.offset and rotation_deg must have three elements")
    if cfg.orbit_radius_m <= 0:
        raise ValueError(
            f"display.orbit_radius_m must be positive: {cfg.orbit_radius_m}"
        )
    # 180 is a full half turn either way, which is the whole circle.
    if not (0 < cfg.orbit_limit_deg <= 180):
        raise ValueError(
            f"display.orbit_limit_deg must be in (0, 180]: {cfg.orbit_limit_deg}"
        )

    # 180 is the whole hemisphere behind you, so a fade that only ever
    # reaches its floor when the face is directly at your back is legal,
    # if pointless. full == fade is not: it would step rather than fade.
    if not (0 <= cfg.gaze_full_deg < cfg.gaze_fade_deg <= 180):
        raise ValueError(
            "display.gaze_full_deg and gaze_fade_deg must satisfy "
            f"0 <= full < fade <= 180: {cfg.gaze_full_deg} / {cfg.gaze_fade_deg}"
        )
    if not (GAZE_ALPHA_FLOOR <= cfg.gaze_min_alpha <= 1):
        raise ValueError(
            f"display.gaze_min_alpha must be between {GAZE_ALPHA_FLOOR} and 1: "
            f"{cfg.gaze_min_alpha}. A face that fades to nothing looks exactly "
            "like the process having died, which is the failure this exists to "
            "avoid, so it always leaves something on screen"
        )

    if not (cfg.low_mgdl < cfg.high_mgdl < cfg.very_high_mgdl):
        raise ValueError(
            "thresholds must satisfy low < high < very_high: "
            f"{cfg.low_mgdl} / {cfg.high_mgdl} / {cfg.very_high_mgdl}"
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
    if cfg.trend_window_min < window_floor:
        raise ValueError(
            f"trend.window_min must be at least {window_floor:.0f}; the API "
            f"sends one point every ~{GRAPH_RESOLUTION_MIN:.0f} minutes, so a "
            f"shorter window will not reliably hold the {MIN_FIT_POINTS} "
            f"needed to fit a slope: {cfg.trend_window_min}"
        )
    # It divides the slope, so zero is a crash on the first reading
    # rather than a wrong angle.
    if cfg.trend_fast_mgdl_min <= 0:
        raise ValueError(
            "trend.fast_mgdl_min must be positive: "
            f"{cfg.trend_fast_mgdl_min}"
        )

    # Polling harder than the official app risks being rate limited or cut
    # off. The sensor itself only updates about once a minute, so a shorter
    # interval cannot return anything new anyway.
    if cfg.poll_interval_sec < 30:
        raise ValueError(
            "polling.interval_sec must not go below 30; the sensor updates "
            "about once a minute, so polling faster returns nothing new and "
            "only risks the account being blocked"
        )
