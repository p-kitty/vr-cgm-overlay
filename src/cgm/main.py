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

Only the loop and the wiring are here. The fetch schedule and the config
watcher live in `cgm.core`, because neither has anything to do with VR
and a second frontend should not have to import this file to get them.
"""

from __future__ import annotations

import argparse
import contextlib
import ctypes
import logging
import sys
import time
from pathlib import Path

# cgm.core.config imports tomllib unguarded, and the dependency floors in
# pyproject.toml assume 3.14. Say what is wrong in a sentence rather than
# letting an import blow up further down.
if sys.version_info < (3, 14):
    raise SystemExit(
        "vr-cgm-overlay needs Python 3.14 or newer; this is "
        f"{sys.version.split()[0]}."
    )

from cgm.core import config as config_mod  # noqa: E402
from cgm.core.librelink import (  # noqa: E402
    AuthError,
    LibreLinkError,
    LibreLinkUp,
)
from cgm.core.poller import Poller  # noqa: E402
from cgm.core.watcher import ConfigWatcher  # noqa: E402
from cgm.face.renderer import (  # noqa: E402
    Theme,
    TrendTuning,
    WatchFaceRenderer,
)

log = logging.getLogger("vrcgm")

DRAW_INTERVAL_SEC = 1.0
# Used only when the headset will not say what it refreshes at.
FALLBACK_TRACK_HZ = 90.0

# Settings a reload cannot apply: the overlay picks its controller role
# once, and the API client is built with the account. Keyed by the name
# in config.toml, because that is the file the message sends you to.
RESTART_ONLY = {
    "display.hand": "vr.hand",
    "account.email": "account.email",
    "account.password": "account.password",
    "account.patient_id": "account.patient_id",
    "account.region": "account.region",
    "account.api_version": "account.api_version",
}


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


def _setting(cfg: config_mod.Config, path: str):
    """Read a dotted path like `vr.hand` off the sectioned config."""
    value = cfg
    for part in path.split("."):
        value = getattr(value, part)
    return value


def build_theme(cfg: config_mod.Config) -> Theme:
    return Theme(
        low_mgdl=cfg.thresholds.low_mgdl,
        high_mgdl=cfg.thresholds.high_mgdl,
        very_high_mgdl=cfg.thresholds.very_high_mgdl,
    )


def build_trend(cfg: config_mod.Config) -> TrendTuning:
    return TrendTuning(
        local=cfg.trend.local,
        window_min=cfg.trend.window_min,
        fast_mgdl_min=cfg.trend.fast_mgdl_min,
    )


def build_renderer(cfg: config_mod.Config) -> WatchFaceRenderer:
    return WatchFaceRenderer(
        theme=build_theme(cfg), unit=cfg.display.unit, trend=build_trend(cfg)
    )


def apply_config(
    cfg: config_mod.Config,
    previous: config_mod.Config,
    overlay,
    poller: Poller,
) -> None:
    """Push a reloaded config onto the running overlay and poller."""
    overlay.set_placement(cfg.vr.offset, cfg.vr.rotation_deg)
    overlay.set_orbit(cfg.vr.orbit, cfg.vr.orbit_radius_m, cfg.vr.orbit_limit_deg)
    overlay.set_gaze(
        cfg.vr.gaze_fade,
        cfg.vr.gaze_full_deg,
        cfg.vr.gaze_fade_deg,
        cfg.vr.gaze_min_alpha,
    )
    overlay.set_arm_guide(cfg.vr.arm_guide)
    overlay.set_width(cfg.vr.width_m)
    overlay.set_opacity(cfg.vr.opacity)
    overlay.set_flip_vertical(cfg.vr.flip_vertical)
    poller.set_interval(cfg.polling.interval_sec)
    poller.set_trend(build_trend(cfg))

    changed = [
        name
        for name, path in RESTART_ONLY.items()
        if _setting(cfg, path) != _setting(previous, path)
    ]
    if changed:
        log.warning("restart to apply: %s", ", ".join(changed))


def run(cfg: config_mod.Config, config_path: Path) -> int:
    # openvr is only needed for the VR path. Importing it lazily lets
    # --dry-run work on a machine without SteamVR installed -- and puts
    # this line where no headless check can reach it, which is how it
    # once shipped still naming the module by its pre-package name.
    # tests/test_imports.py walks the AST for exactly that.
    from cgm.vr.overlay import WristOverlay

    client = LibreLinkUp(
        cfg.account.email,
        cfg.account.password,
        patient_id=cfg.account.patient_id,
        region=cfg.account.region,
        version=cfg.account.api_version,
    )
    renderer = build_renderer(cfg)
    poller = Poller(client, cfg.polling.interval_sec, build_trend(cfg))
    watcher = ConfigWatcher(config_path)

    with fine_timer(), WristOverlay(
        hand=cfg.vr.hand,
        width_m=cfg.vr.width_m,
        offset=cfg.vr.offset,
        rotation_deg=cfg.vr.rotation_deg,
        opacity=cfg.vr.opacity,
        flip_vertical=cfg.vr.flip_vertical,
        orbit=cfg.vr.orbit,
        orbit_radius_m=cfg.vr.orbit_radius_m,
        orbit_limit_deg=cfg.vr.orbit_limit_deg,
        arm_guide=cfg.vr.arm_guide,
        gaze_fade=cfg.vr.gaze_fade,
        gaze_full_deg=cfg.vr.gaze_full_deg,
        gaze_fade_deg=cfg.vr.gaze_fade_deg,
        gaze_min_alpha=cfg.vr.gaze_min_alpha,
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
                    renderer = build_renderer(edited)
                    cfg = edited
                    log.info("reloaded %s", config_path)

                if not attached:
                    # Nothing to attach to while the controller sleeps.
                    # update_attachment re-attaches when it comes back.
                    time.sleep(DRAW_INTERVAL_SEC)
                    continue

                if poller.reading is not None:
                    image = renderer.render(
                        poller.reading, stale_after_min=cfg.display.stale_after_min
                    )
                    # Buzz only on the transition into a low, not while it
                    # lasts: a repeating alert is intolerable mid-game.
                    is_low = poller.reading.value_mgdl < cfg.thresholds.low_mgdl
                    if cfg.polling.alert_on_low and is_low and not was_low:
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
        cfg.account.email,
        cfg.account.password,
        patient_id=cfg.account.patient_id,
        region=cfg.account.region,
        version=cfg.account.api_version,
    )
    renderer = build_renderer(cfg)

    reading = client.get_latest()
    print(
        f"glucose: {reading.display_value(cfg.display.unit)} "
        f"{'mmol/L' if cfg.display.unit == 'mmol' else 'mg/dL'} {reading.arrow}  "
        f"({reading.age_minutes():.1f} min old)"
    )
    # The graph endpoint's history is the one part of the response no
    # test can check, because only the live API says what shape it
    # arrives in. Print what came back so a dry run can confirm it.
    trend = build_trend(cfg)
    slope = trend.slope_for(reading)
    if slope is not None:
        detail = f"trend {slope:+.2f} mg/dL/min over {trend.window_min:.0f} min"
    elif not trend.local:
        detail = f"local trend off, using the API arrow {reading.arrow}"
    else:
        detail = f"too few to fit, falling back to the API arrow {reading.arrow}"
    print(f"history: {len(reading.history)} points; {detail}")
    renderer.render(reading, stale_after_min=cfg.display.stale_after_min).save(out)
    print(f"preview image: {out}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="SteamVR overlay showing blood glucose on your wrist in VR"
    )
    parser.add_argument(
        "--config",
        type=Path,
        # The checkout this package was installed from, which is where
        # config.toml sits next to config.example.toml. Resolved from the
        # module rather than the working directory so the command works
        # from anywhere, as it did when this file lived in src/.
        default=Path(__file__).resolve().parents[2] / "config.toml",
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
