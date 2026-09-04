"""Resident entry point.

Two rates run against each other:
  - fetch (60s default): hits the API. The sensor updates about once a
    minute, so going faster buys nothing.
  - draw (1s): refreshes the age readout and keeps controller tracking up.

A failed fetch keeps the last reading on screen. Its age keeps climbing,
so it stays obvious the value is old; going silent mid-session would be
the dangerous failure.
"""

from __future__ import annotations

import argparse
import logging
import random
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import config as config_mod
from librelink import AuthError, LibreLinkError, LibreLinkUp
from renderer import Theme, WatchFaceRenderer

log = logging.getLogger("vrcgm")

DRAW_INTERVAL_SEC = 1.0
MAX_BACKOFF_SEC = 600.0


class Poller:
    """Owns the fetch schedule and its backoff.

    Repeated failures back off exponentially: hammering the API through a
    network outage or a service problem does not speed up recovery and
    only raises the odds of being cut off.
    """

    def __init__(self, client: LibreLinkUp, interval: float) -> None:
        self._client = client
        self._interval = interval
        self._next_at = 0.0
        self._failures = 0

        self.reading = None
        self.error: str | None = None

    def due(self, now: float) -> bool:
        return now >= self._next_at

    def poll(self, now: float) -> bool:
        """Attempt one fetch. True when a new reading arrived."""
        got_new = False
        try:
            self.reading = self._client.get_latest()
            self.error = None
            self._failures = 0
            got_new = True
            log.info(
                "fetched: %.0f mg/dL %s (%.1f min old)",
                self.reading.value_mgdl,
                self.reading.arrow,
                self.reading.age_minutes(),
            )
        except AuthError as exc:
            # Bad credentials or an unaccepted agreement. Retrying will not
            # fix either, so wait a long time.
            self.error = "AUTH ERROR"
            self._failures = max(self._failures, 6)
            log.error("authentication error: %s", exc)
        except (LibreLinkError, OSError) as exc:
            self.error = "NO CONNECTION"
            self._failures += 1
            log.warning("fetch failed (attempt %d): %s", self._failures, exc)

        # Jitter keeps many clients from landing on the same instant.
        if self._failures:
            delay = min(self._interval * (2 ** self._failures), MAX_BACKOFF_SEC)
        else:
            delay = self._interval
        self._next_at = now + delay + random.uniform(0, 3)
        return got_new


def build_theme(cfg: config_mod.Config) -> Theme:
    return Theme(
        low_mgdl=cfg.low_mgdl,
        high_mgdl=cfg.high_mgdl,
        very_high_mgdl=cfg.very_high_mgdl,
    )


def run(cfg: config_mod.Config) -> int:
    # openvr is only needed for the VR path. Importing it lazily lets
    # --dry-run work on a machine without SteamVR installed.
    from overlay import WristOverlay

    client = LibreLinkUp(
        cfg.email,
        cfg.password,
        patient_id=cfg.patient_id,
        region=cfg.region,
        version=cfg.api_version,
    )
    renderer = WatchFaceRenderer(theme=build_theme(cfg), unit=cfg.unit)
    poller = Poller(client, cfg.poll_interval_sec)

    with WristOverlay(
        hand=cfg.hand,
        width_m=cfg.width_m,
        offset=cfg.offset,
        rotation_deg=cfg.rotation_deg,
        opacity=cfg.opacity,
        flip_vertical=cfg.flip_vertical,
    ) as overlay:
        overlay.set_image(renderer.render_message("CONNECTING"))

        last_draw = 0.0
        was_low = False

        while True:
            now = time.monotonic()

            if overlay.should_quit():
                return 0

            if poller.due(now):
                poller.poll(now)
                last_draw = 0.0  # show a new reading immediately

            if now - last_draw >= DRAW_INTERVAL_SEC:
                last_draw = now
                attached = overlay.update_attachment()

                if not attached:
                    # Nothing to attach to while the controller sleeps.
                    # update_attachment re-attaches when it comes back.
                    time.sleep(DRAW_INTERVAL_SEC)
                    continue

                if poller.reading is not None:
                    image = renderer.render(
                        poller.reading, stale_after_min=cfg.stale_after_min
                    )
                    # Buzz only on the transition into a low, not while it
                    # lasts: a repeating alert is intolerable mid-game.
                    is_low = poller.reading.value_mgdl < cfg.low_mgdl
                    if cfg.alert_on_low and is_low and not was_low:
                        overlay.pulse()
                    was_low = is_low
                else:
                    image = renderer.render_message(
                        poller.error or "WAITING", detail="no reading yet"
                    )

                overlay.set_image(image)

            time.sleep(0.05)


def dry_run(cfg: config_mod.Config, out: Path) -> int:
    """Fetch one live reading and write it to an image, without VR.

    Lets credentials and rendering be checked before SteamVR is involved.
    """
    client = LibreLinkUp(
        cfg.email,
        cfg.password,
        patient_id=cfg.patient_id,
        region=cfg.region,
        version=cfg.api_version,
    )
    renderer = WatchFaceRenderer(theme=build_theme(cfg), unit=cfg.unit)

    reading = client.get_latest()
    print(
        f"glucose: {reading.display_value(cfg.unit)} "
        f"{'mmol/L' if cfg.unit == 'mmol' else 'mg/dL'} {reading.arrow}  "
        f"({reading.age_minutes():.1f} min old)"
    )
    renderer.render(reading, stale_after_min=cfg.stale_after_min).save(out)
    print(f"preview image: {out}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="SteamVR overlay showing blood glucose on your wrist in VR"
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=Path(__file__).parent.parent / "config.toml",
        help="path to the config file",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="fetch one live reading and write a PNG, without using SteamVR",
    )
    parser.add_argument(
        "--out", type=Path, default=Path("preview.png"), help="output path for --dry-run"
    )
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    try:
        cfg = config_mod.load(args.config)
    except (FileNotFoundError, ValueError) as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 2

    try:
        if args.dry_run:
            return dry_run(cfg, args.out)
        return run(cfg)
    except KeyboardInterrupt:
        log.info("shutting down")
        return 0
    except AuthError as exc:
        print(f"authentication error: {exc}", file=sys.stderr)
        return 1
    except LibreLinkError as exc:
        print(f"LibreLinkUp error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
