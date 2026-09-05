"""Resident entry point.

Three rates run against each other:
  - fetch (60s default): hits the API. The sensor updates about once a
    minute, so going faster buys nothing.
  - draw (1s): refreshes the age readout.
  - track (the headset's own refresh rate): keeps the controller attachment
    current and, in orbit mode, turns the face towards the head. Anything
    much slower shows as the face stepping around the arm rather than
    sliding, and Windows will not schedule it there without being asked.

A failed fetch keeps the last reading on screen. Its age keeps climbing,
so it stays obvious the value is old; going silent mid-session would be
the dangerous failure.

The draw loop also watches config.toml, so placement can be tuned with
the headset still on.
"""

from __future__ import annotations

import argparse
import contextlib
import ctypes
import logging
import random
import sys
import time
from pathlib import Path

# config.py imports tomllib unguarded, and the floors in
# requirements.txt assume 3.14. Say what is wrong in a sentence rather
# than letting an import blow up further down.
if sys.version_info < (3, 14):
    raise SystemExit(
        "vr-cgm-overlay needs Python 3.14 or newer; this is "
        f"{sys.version.split()[0]}."
    )

sys.path.insert(0, str(Path(__file__).parent))

import config as config_mod
from librelink import AuthError, LibreLinkError, LibreLinkUp
from renderer import Theme, WatchFaceRenderer

log = logging.getLogger("vrcgm")

DRAW_INTERVAL_SEC = 1.0
# Used only when the headset will not say what it refreshes at.
FALLBACK_TRACK_HZ = 90.0
MAX_BACKOFF_SEC = 600.0

# Settings a reload cannot apply: the overlay picks its controller role
# once, and the API client is built with the account.
RESTART_ONLY = ("hand", "email", "password", "patient_id", "region", "api_version")


@contextlib.contextmanager
def fine_timer():
    """Ask Windows for a 1ms scheduling tick for as long as the loop runs.

    Sleeps are rounded up to the system tick, which is 15.6ms by default.
    That caps the loop near 64Hz however short a sleep it asks for, and the
    old 0.05 sleep was really taking 62ms, so tracking ran at 16Hz and the
    orbit stepped visibly. Measured here: sleep(1/90) takes 15.5ms as
    standard and 11.1ms with the period set.

    It is a system wide setting and costs power, so it is given back on the
    way out. On anything but Windows this does nothing.
    """
    try:
        winmm = ctypes.WinDLL("winmm")
    except (AttributeError, OSError):
        yield
        return

    winmm.timeBeginPeriod(1)
    try:
        yield
    finally:
        winmm.timeEndPeriod(1)


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

    def set_interval(self, interval: float) -> None:
        """Change the fetch interval, effective from the next fetch."""
        self._interval = interval

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


class ConfigWatcher:
    """Re-read config.toml whenever it changes on disk.

    Placement is found by trial and error with the headset on, and there
    is no way to judge a change without wearing it. Restarting for every
    nudge means taking the headset off, so the file is watched instead
    and edits land on the next draw.

    A half-written file parses as garbage, and an edit can be plain
    wrong. Neither may take down a resident process, so a bad read is
    logged and the running config is kept.
    """

    def __init__(self, path: Path) -> None:
        self._path = path
        self._stamp = self._current_stamp()

    def _current_stamp(self) -> tuple[float, int] | None:
        try:
            st = self._path.stat()
        except OSError:
            return None
        # Size as well as mtime: editors can write twice within the
        # timestamp resolution of the filesystem.
        return (st.st_mtime, st.st_size)

    def poll(self) -> config_mod.Config | None:
        """Return the reloaded config once the file changes, else None."""
        stamp = self._current_stamp()
        if stamp is None or stamp == self._stamp:
            return None
        self._stamp = stamp

        try:
            return config_mod.load(self._path)
        except (OSError, ValueError) as exc:
            # TOMLDecodeError is a ValueError, so a partial write lands here.
            log.warning("ignoring the edited config: %s", exc)
            return None


def build_theme(cfg: config_mod.Config) -> Theme:
    return Theme(
        low_mgdl=cfg.low_mgdl,
        high_mgdl=cfg.high_mgdl,
        very_high_mgdl=cfg.very_high_mgdl,
    )


def apply_config(
    cfg: config_mod.Config,
    previous: config_mod.Config,
    overlay,
    poller: Poller,
) -> None:
    """Push a reloaded config onto the running overlay and poller."""
    overlay.set_placement(cfg.offset, cfg.rotation_deg)
    overlay.set_orbit(cfg.orbit, cfg.orbit_radius_m, cfg.orbit_limit_deg)
    overlay.set_gaze(
        cfg.gaze_fade, cfg.gaze_full_deg, cfg.gaze_fade_deg, cfg.gaze_min_alpha
    )
    overlay.set_arm_guide(cfg.arm_guide)
    overlay.set_width(cfg.width_m)
    overlay.set_opacity(cfg.opacity)
    overlay.set_flip_vertical(cfg.flip_vertical)
    poller.set_interval(cfg.poll_interval_sec)

    changed = [k for k in RESTART_ONLY if getattr(cfg, k) != getattr(previous, k)]
    if changed:
        log.warning("restart to apply: %s", ", ".join(changed))


def run(cfg: config_mod.Config, config_path: Path) -> int:
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
    watcher = ConfigWatcher(config_path)

    with fine_timer(), WristOverlay(
        hand=cfg.hand,
        width_m=cfg.width_m,
        offset=cfg.offset,
        rotation_deg=cfg.rotation_deg,
        opacity=cfg.opacity,
        flip_vertical=cfg.flip_vertical,
        orbit=cfg.orbit,
        orbit_radius_m=cfg.orbit_radius_m,
        orbit_limit_deg=cfg.orbit_limit_deg,
        arm_guide=cfg.arm_guide,
        gaze_fade=cfg.gaze_fade,
        gaze_full_deg=cfg.gaze_full_deg,
        gaze_fade_deg=cfg.gaze_fade_deg,
        gaze_min_alpha=cfg.gaze_min_alpha,
    ) as overlay:
        overlay.set_image(renderer.render_message("CONNECTING"))

        # Pace against the headset rather than a rate picked here: 72 on a
        # Quest 3, 144 on an Index. Only the orbit angle is computed in this
        # loop -- the compositor applies the live controller pose to it every
        # frame regardless -- so matching is about not stepping and not
        # burning work, rather than about being in phase with anything.
        track_interval = 1.0 / overlay.display_hz(FALLBACK_TRACK_HZ)
        log.info("tracking at %.0fHz", 1.0 / track_interval)

        last_draw = 0.0
        was_low = False
        attached = False
        # perf_counter, not monotonic: on Windows monotonic comes off
        # GetTickCount64 and only moves in 15.6ms steps, which is coarser
        # than the interval being paced here.
        next_tick = time.perf_counter()

        while True:
            now = time.perf_counter()

            if overlay.should_quit():
                return 0

            # Tracking runs on every pass, not just when redrawing. In orbit
            # mode this is what turns the face towards the head, and at the
            # one second draw rate it would lag a head turn badly.
            attached = overlay.update_attachment()

            if poller.due(now):
                poller.poll(now)
                last_draw = 0.0  # show a new reading immediately

            if now - last_draw >= DRAW_INTERVAL_SEC:
                last_draw = now

                edited = watcher.poll()
                if edited is not None:
                    apply_config(edited, cfg, overlay, poller)
                    renderer = WatchFaceRenderer(
                        theme=build_theme(edited), unit=edited.unit
                    )
                    cfg = edited
                    log.info("reloaded %s", config_path)

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
                    # The buzz is once; this lasts. A gaze fade must not
                    # dim the one state the colour is there to shout.
                    overlay.set_alert(is_low)
                else:
                    image = renderer.render_message(
                        poller.error or "WAITING", detail="no reading yet"
                    )
                    # No reading has ever arrived: there is no low on the
                    # face to protect. A failed fetch does not land here,
                    # because the last reading stays up.
                    overlay.set_alert(False)

                overlay.set_image(image)

            # Sleep to the next tick rather than for a fixed span, so the
            # work above does not stretch the interval it was meant to keep.
            next_tick += track_interval
            delay = next_tick - time.perf_counter()
            if delay > 0:
                time.sleep(delay)
            else:
                next_tick = time.perf_counter()  # fell behind; do not chase it


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
        return run(cfg, args.config)
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
