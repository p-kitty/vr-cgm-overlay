"""Configuration loading.

config.toml holds the LibreLinkUp password, which grants access to health
data, so it must stay out of the repository (.gitignore already excludes
it).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

try:  # Python 3.11+
    import tomllib
except ModuleNotFoundError:  # 3.10 and earlier
    import tomli as tomllib  # type: ignore[no-redef]

log = logging.getLogger(__name__)


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
    stale_after_min: float = 10.0

    urgent_low_mgdl: float = 54.0
    low_mgdl: float = 70.0
    high_mgdl: float = 180.0

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
    cfg.stale_after_min = float(display.get("stale_after_min", cfg.stale_after_min))

    cfg.urgent_low_mgdl = float(thresholds.get("urgent_low_mgdl", cfg.urgent_low_mgdl))
    cfg.low_mgdl = float(thresholds.get("low_mgdl", cfg.low_mgdl))
    cfg.high_mgdl = float(thresholds.get("high_mgdl", cfg.high_mgdl))

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

    if not (cfg.urgent_low_mgdl < cfg.low_mgdl < cfg.high_mgdl):
        raise ValueError(
            "thresholds must satisfy urgent_low < low < high: "
            f"{cfg.urgent_low_mgdl} / {cfg.low_mgdl} / {cfg.high_mgdl}"
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
