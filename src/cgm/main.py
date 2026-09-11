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
from cgm.core import startup  # noqa: E402
from cgm.core.alert import LowAlert  # noqa: E402
from cgm.core.console import force_utf8_output  # noqa: E402
from cgm.core.librelink import (  # noqa: E402
    AuthError,
    LibreLinkError,
    LibreLinkUp,
)
from cgm.core.fetcher import Fetcher  # noqa: E402
from cgm.core.instance import claim  # noqa: E402
from cgm.core.logfile import LOG_FORMAT, log_to_file, log_uncaught  # noqa: E402
from cgm.core.poller import Poller  # noqa: E402
from cgm.core.state import STATE_NAME, load_position, save_position  # noqa: E402
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

# Settings a reload cannot apply, named as config.toml spells them,
# because that is the file the message sends you to.
#
# The account and nothing else: the API client is built from it once, at
# startup, whichever frontends are running. Read off the section rather
# than listed, so a key added to `[account]` is restart-only without
# anybody remembering to say so, and no name here can be misspelled.
#
# `hand` used to be here too, because the overlay picked its controller
# role when the process started. It picks it when the *session* starts
# now, and a session can be reopened, so editing it costs a second rather
# than a restart. See `cgm.vr.session.VrSession.restart`.
RESTART_ONLY = tuple(f"account.{key}" for key in config_mod.FIELD_TYPES["account"])


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


def _setting(cfg: config_mod.Config, name: str):
    """Read a `section.key` name like `vr.hand` off the sectioned config."""
    section, key = name.split(".")
    return getattr(getattr(cfg, section), key)


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


def alert_tuning(cfg: config_mod.Config) -> dict[str, float]:
    """What `LowAlert` is tuned with: once to build it, again on a reload.

    A reload retunes the one alert rather than building another, because
    whether it is armed has to survive the edit -- see
    `LowAlert.set_tuning`.
    """
    return {
        "low_mgdl": cfg.thresholds.low_mgdl,
        "rearm_mgdl": cfg.polling.rearm_margin_mgdl,
        "repeat_min": cfg.polling.repeat_every_min,
    }


def build_client(cfg: config_mod.Config) -> LibreLinkUp:
    return LibreLinkUp(
        cfg.account.email,
        cfg.account.password,
        patient_id=cfg.account.patient_id,
        region=cfg.account.region,
        version=cfg.account.api_version,
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
    cfg: config_mod.Config,
    previous: config_mod.Config,
    settings: tuple[str, ...] = RESTART_ONLY,
) -> list[str]:
    """Name the edited settings a reload cannot pick up, and say so.

    Silently doing nothing is the worst of the three possible
    behaviours: the file says one thing, the screen shows another, and
    nothing connects them.
    """
    changed = [
        name for name in settings if _setting(cfg, name) != _setting(previous, name)
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


def settings_opener(config_path: Path):
    """What the gear on the window calls: one settings window at a time.

    The settings window writes config.toml and stops there, so nothing
    else has to know it exists: the edit comes back round through the
    watcher like any other. One at a time -- a second copy of the same
    file, opened over the first, would be two answers to the same
    question.
    """
    # Lazy for the same reason `cgm.desk.window` is: tkinter.
    from cgm.desk.settings import SettingsWindow

    settings = None

    def open_settings(master) -> None:
        nonlocal settings
        if settings is not None and settings.alive():
            settings.lift()
            return
        try:
            settings = SettingsWindow(master, config_path)
        except (OSError, ValueError) as exc:
            # The file is unreadable, which the running process has
            # survived by keeping what it already loaded. Say so rather
            # than taking the window down with it.
            log.error("cannot open the settings: %s", exc)

    return open_settings


class Tick:
    """The work done once a second: take an edit, draw, announce a low.

    `run` builds the poller, the frontends and the rest, and hands one of
    these to whichever loop owns the process. Everything that ties them
    together happens in here -- which frontend gets which face, what a
    reload reaches, when the one alert fires -- and nothing in here
    touches Tk or OpenVR directly, so tests/test_tick.py drives it with
    stand-ins for all of them.

    `window` and `session` are None for the half that is not running.

    **A call never raises.** Whichever loop owns the process calls this
    once a second and would stop at the first exception: Tk's `after`
    chain is only rescheduled once `tick` returns, so the window would
    freeze on its last frame -- number, colour and age readout all
    stuck -- and `_drive` would take the overlay down with the process.
    A face that stops updating while still showing a number is the
    failure everything here exists to avoid, so a pass that fails is
    logged and the next one runs as usual.
    """

    def __init__(
        self,
        cfg: config_mod.Config,
        config_path: Path,
        *,
        poller,
        watcher,
        alert: LowAlert,
        window=None,
        session=None,
    ) -> None:
        self.cfg = cfg
        self._path = config_path
        self._poller = poller
        self._watcher = watcher
        self._alert = alert
        self._window = window
        self._session = session
        self._window_face: WatchFaceRenderer | None = None
        self._vr_face: WatchFaceRenderer | None = None
        # Whether the last pass raised, so a fault that repeats every
        # second logs its traceback once rather than 3600 times an hour.
        self._failing = False
        self._build_faces(cfg)

    def _build_faces(self, cfg: config_mod.Config) -> None:
        """One renderer per frontend that is up, built from `cfg`.

        Rebuilt on every reload rather than retuned. A graph turned on or
        off changes the card's height: the window takes its size from the
        image and resizes on the next frame, and the overlay's set_image
        rebuilds its buffer -- it is sized by width, so the card keeps
        its width in metres and grows downwards.
        """
        if self._session is not None:
            self._vr_face = build_renderer(cfg, with_graph=cfg.graph.in_vr)
        if self._window is not None:
            self._window_face = build_renderer(
                cfg, with_graph=cfg.graph.in_window, rounded=False
            )

    def show_message(self, message: str) -> None:
        """Put a message card on every frontend, before there is a reading."""
        if self._session is not None:
            self._session.frame = (self._vr_face.render_message(message), False)
        if self._window is not None:
            self._window.set_image(self._window_face.render_message(message))

    def __call__(self) -> None:
        # Exception, not BaseException: Ctrl-C has to reach the loop, which
        # is how both of them are stopped.
        try:
            self._step()
        except Exception:
            if not self._failing:
                log.exception("the draw loop failed; carrying on")
            self._failing = True
        else:
            if self._failing:
                log.info("the draw loop has recovered")
            self._failing = False

    def _step(self) -> None:
        edited = self._watcher.poll()
        if edited is not None:
            self.reload(edited)

        # One read each. The fetch thread replaces both with a single
        # assignment, and `error` is only consulted when there is no
        # reading, so a pair caught mid-swap still describes a state the
        # poller was really in.
        reading = self._poller.reading
        error = self._poller.error
        self._draw(reading, error)
        self._announce(reading)

    def reload(self, edited: config_mod.Config) -> None:
        """Hand an edited config to everything that can take it live."""
        cfg = self.cfg
        warn_restart_only(edited, cfg)
        self._poller.set_interval(edited.polling.interval_sec)
        self._poller.set_trend(build_trend(edited))
        self._alert.set_tuning(**alert_tuning(edited))
        if self._session is not None:
            # The whole config in one assignment; the thread applies it
            # on its next pass. Handed over before the reopen below, so
            # the session that opens next reads the edit rather than what
            # it replaced.
            self._session.settings = edited
            if edited.vr.hand != cfg.vr.hand:
                log.info("following the %s controller", edited.vr.hand)
                self._session.restart()
        if self._window is not None:
            self._window.set_scale(edited.window.scale)
            self._window.set_always_on_top(edited.window.always_on_top)
        self._build_faces(edited)
        self.cfg = edited
        log.info("reloaded %s", self._path)

    def _draw(self, reading, error: str | None) -> None:
        cfg = self.cfg
        if self._window is not None:
            self._window.set_image(
                face_image(
                    self._window_face,
                    reading,
                    error,
                    stale_after_min=cfg.display.stale_after_min,
                )
            )
            self._window.set_title(_window_title(reading, error, cfg.display.unit))
            # The one thing that travels back from the VR half. The window
            # is otherwise identical whether SteamVR is running or not, so
            # without this the only way to know the face is on a
            # controller is the log.
            self._window.set_vr(self._session is not None and self._session.attached)

        if self._session is not None:
            # No reading has ever arrived: there is no low on the face to
            # protect from the gaze fade. A failed fetch does not land
            # here, because the last reading stays up.
            is_low = (
                reading is not None and reading.value_mgdl < cfg.thresholds.low_mgdl
            )
            self._session.frame = (
                face_image(
                    self._vr_face,
                    reading,
                    error,
                    stale_after_min=cfg.display.stale_after_min,
                ),
                is_low,
            )

    def _announce(self, reading) -> None:
        """Once for the process, not once per frontend.

        When to announce is LowAlert's decision -- once on the way in,
        and not again until the reading has climbed clear of the
        threshold.
        """
        if not self.cfg.polling.alert_on_low:
            return
        value = reading.value_mgdl if reading is not None else None
        if self._alert.update(value, time.monotonic()):
            fire_alert(self.cfg, self._session)


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
                report(
                    f"--vr needs the SteamVR bindings ({exc}); "
                    'pip install -e ".[vr]"'
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

    poller = Poller(build_client(cfg), cfg.polling.interval_sec, build_trend(cfg))

    # An ExitStack rather than a nest of `with`: which of these there are
    # is decided at runtime, and the alternative is the same body written
    # out once per combination.
    with contextlib.ExitStack() as stack:
        # The fetch goes on a thread whichever frontend is up. It used to
        # be inline in the VR loop, which the compositor made survivable,
        # but a Tk window blocked for fifteen seconds on a socket is one
        # Windows retitles "Not Responding".
        stack.enter_context(Fetcher(poller))

        session = None
        if with_vr:
            stack.enter_context(fine_timer())
            # `session.settings` is the config the VR half is running on,
            # so the overlay is rebuilt from it and there is no second
            # copy to keep in step.
            session = session_class(
                lambda: build_overlay(overlay_class, session.settings), apply_vr
            )
            session.settings = cfg

        window = None
        if with_window:
            # tkinter is a stdlib module some builds of Python leave out,
            # and PIL.ImageTk needs it in turn, so this import is lazy
            # for the same reason the overlay's is.
            from cgm.desk.window import FaceWindow

            # Beside the config, like the log: one checkout, one place
            # to look for everything it keeps.
            state_path = config_path.parent / STATE_NAME
            window = stack.enter_context(
                FaceWindow(
                    scale=cfg.window.scale,
                    always_on_top=cfg.window.always_on_top,
                    position=load_position(state_path),
                )
            )
            window.on_moved(lambda position: save_position(state_path, position))
            window.on_menu(settings_opener(config_path))
            log.info("click the gear on the face for settings")

        tick = Tick(
            cfg,
            config_path,
            poller=poller,
            watcher=ConfigWatcher(config_path),
            alert=LowAlert(**alert_tuning(cfg)),
            window=window,
            session=session,
        )
        tick.show_message("CONNECTING")
        if session is not None:
            # Started last, so the first thing the thread finds is the
            # config and the frame above rather than None.
            stack.enter_context(session)

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
    client = build_client(cfg)
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
    lag = reading.history_lag_minutes()
    behind = "" if lag is None else f", the newest {lag:.1f} min behind the reading"
    print(f"history: {len(reading.history)} points{behind}; {detail}")
    renderer.render(reading, stale_after_min=cfg.display.stale_after_min).save(out)
    print(f"preview image: {out}")
    return 0


def report(message: str) -> None:
    """Say why the process is stopping, where somebody will see it.

    Printed, as it always was. Started with Windows this runs under
    pythonw, where stderr is None and the print goes nowhere, so it is
    shown in a dialog as well -- otherwise a config error at sign-in is
    a window that never appears and nothing to say why.
    """
    print(message, file=sys.stderr)
    if sys.stderr is not None:
        return
    try:
        # Lazy for the same reason `cgm.desk.window` is: tkinter.
        from cgm.desk.dialog import show_error
    except ImportError:
        return
    show_error(message)


def install_startup(config_path: Path) -> int:
    """Register this config to start at sign-in, and say what was set."""
    try:
        link = startup.install(config_path)
    except OSError as exc:
        report(f"cannot start with Windows: {exc}")
        return 1
    print(f"starts when you sign in to Windows, with {config_path.resolve()}")
    print(f"shortcut: {link}")
    print(
        "turn it off with --uninstall-startup, by deleting the shortcut, "
        "or under Startup apps in Task Manager"
    )
    return 0


def uninstall_startup() -> int:
    """Take the registration away, if there is one."""
    try:
        removed = startup.uninstall()
    except OSError as exc:
        report(f"cannot change starting with Windows: {exc}")
        return 1
    if removed:
        print("no longer starts when you sign in to Windows")
    else:
        print("was not set to start when you sign in to Windows")
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
    mode.add_argument(
        "--install-startup",
        action="store_true",
        help="start the window when you sign in to Windows, with no console",
    )
    mode.add_argument(
        "--uninstall-startup",
        action="store_true",
        help="stop starting when you sign in to Windows",
    )
    parser.add_argument(
        "--out", type=Path, default=Path("preview.png"), help="output path for --dry-run"
    )
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args(argv)

    level = logging.DEBUG if args.verbose else logging.INFO
    if sys.stderr is None:
        # pythonw, which is how a sign-in starts this: there is no console
        # to write to, and a handler on None fails on every line. The log
        # file a run adds below is the only place the log goes.
        logging.getLogger().setLevel(level)
    else:
        logging.basicConfig(level=level, format=LOG_FORMAT, datefmt="%H:%M:%S")

    # Before the config is read: taking a registration away should not
    # depend on the file it was registered with still being valid.
    if args.uninstall_startup:
        return uninstall_startup()

    try:
        cfg = config_mod.load(args.config)
    except (FileNotFoundError, ValueError) as exc:
        report(f"config error: {exc}")
        return 2

    try:
        if args.dry_run:
            return dry_run(cfg, args.out)
        if args.install_startup:
            # After the load, so a config the app would refuse is never
            # what a sign-in starts from.
            return install_startup(args.config)

        # Held until main returns, which is when the process ends.
        lock = claim(args.config)
        if lock is None:
            report("vr-cgm-overlay is already running with this config.")
            return 1

        # Both unless one of them was ruled out. The window is the half
        # that is meant to stay up and the overlay is the one you want as
        # well, when the headset goes on, so wanting only one of them is
        # the case that has to be asked for.
        #
        # The log file goes beside the config it is a run of, and only
        # here: see `cgm.core.logfile` for why --dry-run keeps none. After
        # the lock, so a second copy never opens the same file.
        log_to_file(args.config.parent / "logs")
        log_uncaught()
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
        report(f"authentication error: {exc}")
        return 1
    except LibreLinkError as exc:
        report(f"LibreLinkUp error: {exc}")
        return 1
    except Exception as exc:
        # Anything else that ends the run. The traceback goes through
        # the log, and so into the file; the reason goes wherever it will
        # be seen, which with no console is a dialog.
        log.critical("stopped", exc_info=True)
        report(f"vr-cgm-overlay stopped: {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
