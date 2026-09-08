"""Running the SteamVR overlay on a thread of its own.

The window is the resident frontend and the overlay is the one you want
*as well*, when the headset goes on. Both in one process means one of
them cannot own the loop, and it cannot be this one: tkinter's mainloop
is not something you drive from outside, and tracking cannot ride Tk's
`after` either. Tracking has to run at the headset's rate -- 72Hz on a
Quest 3, 144 on an Index -- and the finest tick available to the window
is 11ms, which would then be contending with repaint. So the overlay
gets a thread and the window keeps the loop.

This is `cgm.core.fetcher` seen from the other side. There a frontend
that owned its loop handed the *fetch* to a thread; here it hands the
*overlay* to one. The sharing discipline is the same and for the same
reason: every handoff between the two is one rebinding of one
attribute, so a reader sees the old value or the new one and never a
half-built anything. That is why there is no lock. State that took two
assignments to update would need one.

Two things about the shape.

**Nothing here reads the config or renders anything.** It is handed an
`open_overlay` that makes one and an `apply` that pushes settings onto
it, both from `cgm.main`, which is where the wiring lives. What that
buys is a thread that can be driven by a fake overlay with no headset
and no SteamVR in the room -- see `tests/test_vr_session.py`. The
overlay itself still needs a device.

**The session is not the process.** SteamVR asking us to quit ends the
session and no more: the thread goes back to waiting for SteamVR, and
the window never notices. That is also how `hand` stops being a
restart-the-application setting -- `restart()` ends one session and
opens the next, which is the only place the controller role is read.
"""

from __future__ import annotations

import logging
import threading
import time

log = logging.getLogger(__name__)

# Used only when the headset will not say what it refreshes at.
FALLBACK_TRACK_HZ = 90.0

# How long to wait before opening the next session. SteamVR shutting
# down can leave a moment where `openvr.init` still succeeds, so a
# session that ended on a quit event could otherwise reopen straight
# into another one.
REOPEN_SEC = 1.0

# How long `stop()` waits for the thread. The overlay has to be
# destroyed on the way out -- SteamVR keeps the key otherwise and the
# next run cannot create it -- and the loop notices `stop` within one
# tick, so this is generous. A thread still inside `open_overlay` is
# waiting for SteamVR with nothing registered, so leaving that one to
# the interpreter costs nothing.
STOP_JOIN_SEC = 2.0


class VrSession:
    """Drives one overlay from a background thread.

    The thread owns the overlay: it is the only thing that touches it,
    which keeps every OpenVR call on one thread. The foreground hands
    work over by assignment and asks for the rest through events:

        session.settings = cfg           # applied via `apply`
        session.frame = (image, is_low)  # drawn when it changes
        session.pulse()                  # buzz on the next pass
        session.restart()                # reopen, picking `hand` up again

    `settings` is passed straight to `apply` and never read here, so
    what it is is `cgm.main`'s business. It is compared by identity: a
    reload rebinds the whole config, so a new object means new settings
    and the same object means there is nothing to push.

    The thread is a daemon, but `stop()` does join it, briefly. That is
    the one place this differs from `Fetcher`, and the reason is that
    the overlay has to be handed back: a process that dies with it
    still registered leaves SteamVR holding the key.
    """

    def __init__(
        self,
        open_overlay,
        apply,
        *,
        fallback_hz: float = FALLBACK_TRACK_HZ,
        reopen_sec: float = REOPEN_SEC,
    ) -> None:
        self._open = open_overlay
        self._apply = apply
        self._fallback_hz = fallback_hz
        self._reopen_sec = reopen_sec

        self._stop = threading.Event()
        self._reopen = threading.Event()
        self._pulse = threading.Event()
        self._thread = threading.Thread(target=self._loop, name="cgm-vr", daemon=True)

        # Handed over by the foreground, one rebinding each. None means
        # "nothing yet", which is what both look like before the first
        # draw.
        self.settings = None
        self.frame: tuple | None = None

        # Handed back the other way, and the only thing that goes that
        # direction: True while there is a controller with the face on
        # it. False covers SteamVR not running, the session between
        # overlays, and a controller asleep -- all of which look the same
        # from outside, which is what a window has to say about it.
        self.attached = False

    # -- lifecycle ----------------------------------------------------------

    def __enter__(self) -> "VrSession":
        self.start()
        return self

    def __exit__(self, *exc) -> None:
        self.stop()

    def start(self) -> None:
        self._thread.start()

    def stop(self, timeout: float = STOP_JOIN_SEC) -> None:
        """Ask the thread to finish, and wait a moment for the overlay."""
        self._stop.set()
        self._thread.join(timeout)
        if self._thread.is_alive():
            # Waiting for SteamVR, almost certainly, in which case there
            # is no overlay to destroy. Say so rather than hang.
            log.debug("the VR thread did not finish within %.1fs", timeout)

    def restart(self) -> None:
        """End the current session and open another.

        For the settings that are read once, when the overlay is created
        -- `hand` is the only one. It used to mean restarting the
        application; it now means restarting a thread.
        """
        self._reopen.set()

    def pulse(self) -> None:
        """Buzz the controller on the next pass of the loop.

        A request rather than the call itself, because the call is the
        thread's to make.
        """
        self._pulse.set()

    # -- the thread ---------------------------------------------------------

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self._session()
            except Exception:
                # The window is the resident half and has done nothing
                # wrong. Take the VR half down, loudly, and leave the
                # rest of the process running.
                log.exception("the VR session has stopped")
                return
            # wait(), not sleep(): stop() then returns immediately
            # rather than after the rest of the pause.
            if self._stop.wait(self._reopen_sec):
                return

    def _session(self) -> None:
        """One overlay, from opening it to handing it back.

        Returns when SteamVR asks us to quit, when `restart()` is
        called, or when the process is stopping. Only the last of those
        ends the thread.
        """
        self._reopen.clear()
        overlay = self._open()
        try:
            # Pace against the headset rather than a rate picked here.
            # Only the orbit angle is computed in this loop -- the
            # compositor applies the live controller pose to it every
            # frame regardless -- so matching is about not stepping and
            # not burning work, rather than about being in phase.
            interval = 1.0 / overlay.display_hz(self._fallback_hz)
            log.info("tracking at %.0fHz", 1.0 / interval)

            applied = None
            drawn = None
            # perf_counter, not monotonic: on Windows monotonic comes
            # off GetTickCount64 and only moves in 15.6ms steps, which
            # is coarser than the interval being paced here.
            next_tick = time.perf_counter()

            while not self._stop.is_set() and not self._reopen.is_set():
                if overlay.should_quit():
                    return

                settings = self.settings
                if settings is not None and settings is not applied:
                    self._apply(overlay, settings)
                    applied = settings

                # On every pass, not just when there is a new frame. In
                # orbit mode this is what turns the face towards the
                # head, and at the draw rate it would lag a head turn
                # badly.
                attached = self.attached = overlay.update_attachment()

                frame = self.frame
                # Nothing to draw on while the controller sleeps, and
                # `drawn` is deliberately left alone there, so the face
                # comes straight back when update_attachment re-attaches.
                if attached and frame is not None and frame is not drawn:
                    image, alert = frame
                    # The announcement is momentary; this lasts. A gaze
                    # fade must not dim the one state the colour is
                    # there to shout.
                    overlay.set_alert(alert)
                    overlay.set_image(image)
                    drawn = frame

                if self._pulse.is_set():
                    self._pulse.clear()
                    overlay.pulse()

                # Sleep to the next tick rather than for a fixed span,
                # so the work above does not stretch the interval it was
                # meant to keep.
                next_tick += interval
                delay = next_tick - time.perf_counter()
                if delay > 0:
                    time.sleep(delay)
                else:
                    next_tick = time.perf_counter()  # fell behind; do not chase
        finally:
            # Whatever ended the session, there is nothing on a
            # controller from here until the next one is open.
            self.attached = False
            overlay.close()
