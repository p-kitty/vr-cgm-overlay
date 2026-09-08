"""Resident entry point.

Three rates run against each other:
  - fetch (60s default): hits the API, on a thread of its own. The sensor
    updates about once a minute, so going faster buys nothing.
  - draw (1s): renders the face and refreshes the age readout.
  - track (the headset's own refresh rate): keeps the controller attachment
    current and, in orbit mode, turns the face towards the head. Anything
    much slower shows as the face stepping around the arm rather than
    sliding, and Windows will not schedule it there without being asked.

A failed fetch keeps the last reading on screen. Its age keeps climbing,
so it stays obvious the value is old; going silent mid-session would be
the dangerous failure.

The draw loop also watches config.toml, so placement can be tuned with
the headset still on.

**Both frontends run in one process.** They used to be two commands, and
therefore two processes, which meant two `Poller`s against an API that
asks not to be hammered and two `LowAlert`s, so a low announced itself
twice with neither half knowing the other had already said it. There is
one of each now, here, and the frontends are presentation only:
`cgm.desk.window` takes an image and shows it in a window, and
`cgm.vr.session` takes an image and puts it on a controller.

Who owns the loop follows from that. Tk does whenever there is a window,
because its mainloop is not something you drive from outside; the overlay
goes on a thread (`cgm.vr.session`) because tracking needs the headset's
rate and Tk's finest tick is 11ms. With no window there is no mainloop to
hand the work to, and `_drive` is that loop instead.

The VR half is a *session* rather than the program. SteamVR going away
ends it and leaves the process running, so the headset can go on and come
off again; and `hand`, read once when the overlay is created, reopens the
session rather than asking for a restart.

Only the loops and the wiring are here. The fetch schedule, the config
watcher and the tracking thread live in `cgm.core` and `cgm.vr`, because
a frontend should not have to import this file to get them.
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

from cgm.core import alert as alert_mod  # noqa: E402
from cgm.core import config as config_mod  # noqa: E402
from cgm.core.alert import LowAlert  # noqa: E402
from cgm.core.console import force_utf8_output  # noqa: E402
from cgm.core.librelink import (  # noqa: E402
    AuthError,
    LibreLinkError,
    LibreLinkUp,
)
from cgm.core.fetcher import Fetcher  # noqa: E402
from cgm.core.poller import Poller  # noqa: E402
from cgm.core.watcher import ConfigWatcher  # noqa: E402
from cgm.face.graph import GraphTuning  # noqa: E402
from cgm.face.renderer import (  # noqa: E402
    Theme,
    TrendTuning,
    WatchFaceRenderer,
    face_image,
    unit_label,
)

log = logging.getLogger("vrcgm")

DRAW_INTERVAL_SEC = 1.0

# Settings a reload cannot apply, keyed by the name in config.toml,
# because that is the file the message sends you to.
#
# The account and nothing else: the API client is built from it once, at
# startup, whichever frontends are running.
#
# `hand` used to be here too, because the overlay picked its controller
# role when the process started. It picks it when the *session* starts
# now, and a session can be reopened, so editing it costs a second rather
# than a restart. See `cgm.vr.session.VrSession.restart`.
RESTART_ONLY = {
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
        fast_mgdl_min=cfg.trend.fast_mgdl_min,
    )


def build_alert(cfg: config_mod.Config) -> LowAlert:
    return LowAlert(
        cfg.thresholds.low_mgdl,
        rearm_mgdl=cfg.polling.rearm_margin_mgdl,
        repeat_min=cfg.polling.repeat_every_min,
    )


def fire_alert(cfg: config_mod.Config, session=None) -> None:
    """Announce a low on every channel that is running.

    Sound is shared, because it is the one channel that reaches you
    without looking at your wrist and works the same either side. The
    buzz belongs to the overlay: a window has no controller to buzz, so
    `session` is None when there is no VR half.

    Announced once, whatever is on screen. Deciding *when* is
    `LowAlert`'s, and there is one of those in the process, so the window
    and the headset cannot disagree about whether you have been told.

    Both are supplements. The face is the alert.
    """
    if cfg.polling.alert_sound:
        alert_mod.play(cfg.polling.sound_path)
    if session is not None and cfg.polling.alert_haptic:
        session.pulse()


def build_graph(cfg: config_mod.Config) -> GraphTuning:
    return GraphTuning(
        window_min=cfg.graph.window_min,
        axis_high_mgdl=cfg.graph.axis_high_mgdl,
    )


def build_renderer(
    cfg: config_mod.Config, *, with_graph: bool = False, rounded: bool = True
) -> WatchFaceRenderer:
    """The renderer for one frontend, with or without the sparkline.

    Whether there is a graph is the caller's to say, because it is the
    one thing about the face the two frontends do not agree on: a
    window is read at a desk and the overlay is glanced at mid-game.
    `graph.in_window` and `graph.in_vr` are what they pass in. The face
    itself has no opinion; it draws whichever card it was built for.

    The corners are the other such thing, and are not configurable at
    all: the arc pays for a compositor, so it belongs to whatever keeps
    an alpha channel -- the overlay and the PNG -- while the Tk window,
    which flattens the card onto an opaque backdrop, asks for square.
    """
    return WatchFaceRenderer(
        theme=build_theme(cfg),
        unit=cfg.display.unit,
        trend=build_trend(cfg),
        graph=build_graph(cfg) if with_graph else None,
        rounded=rounded,
    )


def warn_restart_only(
    cfg: config_mod.Config, previous: config_mod.Config, settings: dict[str, str]
) -> list[str]:
    """Name the edited settings this frontend cannot pick up, and say so.

    Silently doing nothing is the worst of the three possible
    behaviours: the file says one thing, the screen shows another, and
    nothing connects them.
    """
    changed = [
        name
        for name, path in settings.items()
        if _setting(cfg, path) != _setting(previous, path)
    ]
    if changed:
        log.warning("restart to apply: %s", ", ".join(changed))
    return changed


def build_overlay(overlay_class, cfg: config_mod.Config):
    """Create the overlay for one VR session.

    `hand` is read here and nowhere else, which is what makes it a
    reopen rather than a restart: `VrSession` calls this again when the
    session is asked to start over.
    """
    return overlay_class(
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
    )


def apply_vr(overlay, cfg: config_mod.Config) -> None:
    """Push a reloaded config onto the running overlay.

    Handed to `VrSession` and called on its thread, which is where every
    OpenVR call belongs. Everything `hand` needs is in `build_overlay`
    instead, because it cannot be changed on an overlay that exists.
    """
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


def _window_title(reading, error: str | None, unit: str) -> str:
    """What the taskbar shows, so a covered window still reports.

    The number and its unit only. Not the trend arrow: the API's arrow
    and the one on the face disagree by design whenever the local fit is
    in use, and a title bar is the wrong place to explain which is
    which.
    """
    if reading is None:
        return f"{error or 'waiting'} - vr-cgm-overlay"
    return f"{reading.display_value(unit)} {unit_label(unit)} - vr-cgm-overlay"


def _drive(tick) -> None:
    """Call `tick` once a second until Ctrl-C, when there is no window.

    Tk owns the loop whenever a window is up, and the fetch and the
    tracking are both on threads, so this exists for the one arrangement
    where nothing else would turn the handle: `--vr` on its own.

    monotonic rather than perf_counter: this paces a second, not a frame,
    and the fine clock is only wanted where 15.6ms is too coarse to tell.
    """
    next_tick = time.monotonic()
    while True:
        tick()
        next_tick += DRAW_INTERVAL_SEC
        delay = next_tick - time.monotonic()
        if delay > 0:
            time.sleep(delay)
        else:
            next_tick = time.monotonic()  # fell behind; do not chase it


def run(
    cfg: config_mod.Config,
    config_path: Path,
    *,
    with_window: bool = True,
    with_vr: bool = True,
) -> int:
    """Fetch, draw, and show the face wherever it was asked for.

    One `Poller`, one `LowAlert`, one `ConfigWatcher` -- and two
    renderers, because the sparkline and the corners are the two things
    the frontends disagree about: a window is read at a desk and flattens
    the card onto an opaque backdrop, and the overlay is glanced at
    mid-game with a game showing through it.

    Neither frontend is required. `--window` is this with `with_vr`
    off, `--vr` is it with `with_window` off, and the default is both.
    """
    overlay_class = session_class = None
    if with_vr:
        # openvr is only needed for the VR path, and `cgm.vr.overlay` is
        # the only module that reaches for it. Importing it here rather
        # than at the top lets --dry-run and --window work on a machine
        # with no SteamVR installed -- and puts these lines where no
        # headless check can reach them, which is how one once shipped
        # still naming the module by its pre-package name.
        # tests/test_imports.py walks the AST for exactly that.
        try:
            from cgm.vr.overlay import WristOverlay
            from cgm.vr.session import VrSession
        except ImportError as exc:
            # Asked for the overlay and nothing else, so there is no
            # half left to carry on with.
            if not with_window:
                print(
                    f"--vr needs the SteamVR bindings ({exc}); "
                    'pip install -e ".[vr]"',
                    file=sys.stderr,
                )
                return 2
            # Now that the default is both frontends, a desktop-only
            # install lands here on an ordinary `vr-cgm-overlay`. That is
            # a missing extra, not a fault: say which one and carry on
            # with the half that does work.
            log.warning('no overlay (%s); pip install -e ".[vr]" adds it', exc)
            with_vr = False
        else:
            overlay_class, session_class = WristOverlay, VrSession

    client = LibreLinkUp(
        cfg.account.email,
        cfg.account.password,
        patient_id=cfg.account.patient_id,
        region=cfg.account.region,
        version=cfg.account.api_version,
    )
    poller = Poller(client, cfg.polling.interval_sec, build_trend(cfg))
    watcher = ConfigWatcher(config_path)
    alert = build_alert(cfg)

    session = None
    window = None
    vr_face = None
    window_face = None

    # An ExitStack rather than a nest of `with`: which of these there are
    # is decided at runtime, and the alternative is the same body written
    # out once per combination.
    with contextlib.ExitStack() as stack:
        # The fetch goes on a thread whichever frontend is up. It used to
        # be inline in the VR loop, which the compositor made survivable,
        # but a Tk window blocked for fifteen seconds on a socket is one
        # Windows retitles "Not Responding".
        stack.enter_context(Fetcher(poller))

        if with_vr:
            stack.enter_context(fine_timer())
            vr_face = build_renderer(cfg, with_graph=cfg.graph.in_vr)
            # `session.settings` is the config the VR half is running on,
            # so the overlay is rebuilt from it and there is no second
            # copy to keep in step.
            session = session_class(
                lambda: build_overlay(overlay_class, session.settings), apply_vr
            )
            session.settings = cfg
            session.frame = (vr_face.render_message("CONNECTING"), False)
            # Started last, so the first thing the thread finds is the
            # config and the frame above rather than None.
            stack.enter_context(session)

        if with_window:
            # tkinter is a stdlib module some builds of Python leave out,
            # and PIL.ImageTk needs it in turn, so this import is lazy
            # for the same reason the overlay's is.
            from cgm.desk.settings import SettingsWindow
            from cgm.desk.window import FaceWindow

            window_face = build_renderer(
                cfg, with_graph=cfg.graph.in_window, rounded=False
            )
            window = stack.enter_context(
                FaceWindow(
                    scale=cfg.window.scale, always_on_top=cfg.window.always_on_top
                )
            )
            window.set_image(window_face.render_message("CONNECTING"))

            # The settings window writes config.toml and stops there, so
            # nothing below has to know it exists: the edit comes back
            # round through the watcher like any other. One at a time --
            # a second copy of the same file, opened over the first,
            # would be two answers to the same question.
            settings = None

            def open_settings(master) -> None:
                nonlocal settings
                if settings is not None and settings.alive():
                    settings.lift()
                    return
                try:
                    settings = SettingsWindow(master, config_path)
                except (OSError, ValueError) as exc:
                    # The file is unreadable, which the running process
                    # has survived by keeping what it already loaded.
                    # Say so rather than taking the window down with it.
                    log.error("cannot open the settings: %s", exc)

            window.on_menu(open_settings)
            log.info("right-click the face for settings")

        def tick() -> None:
            nonlocal cfg, vr_face, window_face

            edited = watcher.poll()
            if edited is not None:
                warn_restart_only(edited, cfg, RESTART_ONLY)
                poller.set_interval(edited.polling.interval_sec)
                poller.set_trend(build_trend(edited))
                alert.set_tuning(
                    edited.thresholds.low_mgdl,
                    edited.polling.rearm_margin_mgdl,
                    edited.polling.repeat_every_min,
                )
                if session is not None:
                    # The whole config in one assignment; the thread
                    # applies it on its next pass. Handed over before the
                    # reopen below, so the session that opens next reads
                    # the edit rather than what it replaced.
                    session.settings = edited
                    if edited.vr.hand != cfg.vr.hand:
                        log.info("following the %s controller", edited.vr.hand)
                        session.restart()
                    # A graph turned on or off changes the texture's
                    # height, which set_image handles by rebuilding its
                    # buffer. The overlay is sized by width, so the card
                    # keeps its width in metres and grows downwards.
                    vr_face = build_renderer(edited, with_graph=edited.graph.in_vr)
                if window is not None:
                    window.set_scale(edited.window.scale)
                    window.set_always_on_top(edited.window.always_on_top)
                    # The window takes its size from the image, so
                    # turning the graph on here resizes it on the next
                    # frame the same way a scale change does.
                    window_face = build_renderer(
                        edited, with_graph=edited.graph.in_window, rounded=False
                    )
                cfg = edited
                log.info("reloaded %s", config_path)

            # One read each. The fetch thread replaces both with a single
            # assignment, and `error` is only consulted when there is no
            # reading, so a pair caught mid-swap still describes a state
            # the poller was really in.
            reading = poller.reading
            error = poller.error

            if window is not None:
                window.set_image(
                    face_image(
                        window_face,
                        reading,
                        error,
                        stale_after_min=cfg.display.stale_after_min,
                    )
                )
                window.set_title(_window_title(reading, error, cfg.display.unit))

            if session is not None:
                # No reading has ever arrived: there is no low on the
                # face to protect from the gaze fade. A failed fetch does
                # not land here, because the last reading stays up.
                is_low = (
                    reading is not None
                    and reading.value_mgdl < cfg.thresholds.low_mgdl
                )
                session.frame = (
                    face_image(
                        vr_face,
                        reading,
                        error,
                        stale_after_min=cfg.display.stale_after_min,
                    ),
                    is_low,
                )

            # Once for the process, not once per frontend. When to
            # announce is LowAlert's decision -- once on the way in, and
            # not again until the reading has climbed clear of the
            # threshold.
            value = reading.value_mgdl if reading is not None else None
            if cfg.polling.alert_on_low and alert.update(value, time.monotonic()):
                fire_alert(cfg, session)

        if window is not None:
            # Tk owns the loop, so the work is handed to it rather than
            # the other way round. Closing the window ends the process,
            # overlay and all: it is the resident half, and quitting it
            # is how you quit.
            window.run(tick, interval_ms=int(DRAW_INTERVAL_SEC * 1000))
        else:
            _drive(tick)

    return 0


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
    # `in_window`, because this is the desktop face written to a file
    # rather than anything on a controller -- and because the line it
    # prints below is about the history, which the graph is a picture of.
    # Rounded all the same: the PNG keeps its alpha channel and nothing
    # flattens it, so the corners cost nothing here.
    renderer = build_renderer(cfg, with_graph=cfg.graph.in_window)

    reading = client.get_latest()
    print(
        f"glucose: {reading.display_value(cfg.display.unit)} "
        f"{unit_label(cfg.display.unit)} {reading.arrow}  "
        f"({reading.age_minutes():.1f} min old)"
    )
    # The graph endpoint's history is the one part of the response no
    # test can check, because only the live API says what shape it
    # arrives in. Print what came back so a dry run can confirm it.
    trend = build_trend(cfg)
    shape = trend.shape_for(reading)
    if shape.source != "api":
        detail = f"trend {shape.describe()}"
    elif not trend.local:
        detail = f"local trend off, using the API arrow {reading.arrow}"
    else:
        detail = f"too little history, falling back to the API arrow {reading.arrow}"
    print(f"history: {len(reading.history)} points; {detail}")
    renderer.render(reading, stale_after_min=cfg.display.stale_after_min).save(out)
    print(f"preview image: {out}")
    return 0


def main(argv: list[str] | None = None) -> int:
    # Before argparse, before the first print: everything below this
    # line can put a trend arrow on stdout or in the log, and a
    # redirected stream would otherwise be encoding it as cp932.
    force_utf8_output()

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
    # Each of these says "run something narrower than the default", and
    # the default is both frontends at once, so asking for two of them
    # is a question with no answer.
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--dry-run",
        action="store_true",
        help="fetch one live reading and write a PNG, without using SteamVR",
    )
    mode.add_argument(
        "--window",
        action="store_true",
        help="desktop window only; do not touch SteamVR",
    )
    mode.add_argument(
        "--vr",
        action="store_true",
        help="overlay only; no desktop window",
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
        # Both unless one of them was ruled out. The window is the half
        # that is meant to stay up and the overlay is the one you want as
        # well, when the headset goes on, so wanting only one of them is
        # the case that has to be asked for.
        return run(
            cfg,
            args.config,
            with_window=not args.vr,
            with_vr=not args.window,
        )
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
