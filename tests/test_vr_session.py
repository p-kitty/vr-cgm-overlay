"""The overlay's thread, driven by an overlay that is not one.

`VrSession` is the half of `cgm.vr` that has no SteamVR in it. It is
handed an overlay rather than making one, so what it does with it --
when it draws, when it stops, when it opens another -- can be asserted
at a desk. The overlay itself still needs a headset, and nothing here
claims otherwise.

Four things are worth the trouble, and each one shipped as a bug in
some other project:

- the overlay is handed back. A process that dies with it still
  registered leaves SteamVR holding the key, and the next run cannot
  create it.
- a frame is uploaded when it changes and not otherwise. Every upload
  is a chance for the compositor to show a torn frame.
- SteamVR going away ends the session and not the thread. That is the
  whole point of a session: the headset comes off and the window is
  still there.
- `restart()` opens a new overlay, because `hand` is only read when one
  is created.

The fake reports a high refresh rate so the loop turns quickly; the real
thing runs at 72 to 144Hz.
"""

from __future__ import annotations

import threading
import time
import unittest

from cgm.vr.session import VrSession

# Fast enough that a test waiting on a few passes waits milliseconds.
FAKE_HZ = 500.0
TIMEOUT = 5.0


def wait_for(predicate, timeout: float = TIMEOUT) -> bool:
    """Poll until the thread has done something, or give up."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.005)
    return predicate()


class FakeOverlay:
    """Records what the session did to it.

    `quit_after` makes it claim SteamVR is shutting down once the loop
    has been round that many times, which is the only way in without a
    headset.
    """

    def __init__(self, *, attached: bool = True, quit_after: int | None = None) -> None:
        self.attached = attached
        self.quit_after = quit_after
        self.passes = 0
        self.images: list = []
        self.alerts: list[bool] = []
        self.applied: list = []
        self.pulses = 0
        self.closed = 0

    def display_hz(self, fallback: float) -> float:
        return FAKE_HZ

    def should_quit(self) -> bool:
        self.passes += 1
        return self.quit_after is not None and self.passes > self.quit_after

    def update_attachment(self) -> bool:
        return self.attached

    def set_alert(self, active: bool) -> None:
        self.alerts.append(active)

    def set_image(self, image) -> None:
        self.images.append(image)

    def pulse(self) -> None:
        self.pulses += 1

    def close(self) -> None:
        self.closed += 1


class Overlays:
    """The `open_overlay` a session is given, and a record of the ones it got."""

    def __init__(self, **kwargs) -> None:
        self._kwargs = kwargs
        self.opened: list[FakeOverlay] = []

    def __call__(self) -> FakeOverlay:
        overlay = FakeOverlay(**self._kwargs)
        self.opened.append(overlay)
        return overlay

    def wait_for_one(self, count: int = 1) -> FakeOverlay | None:
        if not wait_for(lambda: len(self.opened) >= count):
            return None
        return self.opened[count - 1]


def record_settings(overlay: FakeOverlay, settings) -> None:
    overlay.applied.append(settings)


def session(overlays: Overlays, **kwargs) -> VrSession:
    # A tenth of the real pause between sessions: the reason for it is
    # SteamVR's shutdown, which is not happening here.
    kwargs.setdefault("reopen_sec", 0.1)
    return VrSession(overlays, record_settings, **kwargs)


class Drawing(unittest.TestCase):
    def test_it_opens_an_overlay_and_draws_the_frame(self):
        overlays = Overlays()
        with session(overlays) as vr:
            vr.frame = ("first", False)
            overlay = overlays.wait_for_one()
            self.assertIsNotNone(overlay, "the thread never opened an overlay")
            self.assertTrue(wait_for(lambda: overlay.images == ["first"]))

    def test_it_draws_a_frame_once(self):
        # The face changes about once a minute and the loop turns
        # hundreds of times a second. Every upload is a chance for the
        # compositor to show a torn frame.
        overlays = Overlays()
        with session(overlays) as vr:
            vr.frame = ("only", False)
            overlay = overlays.wait_for_one()
            self.assertTrue(wait_for(lambda: overlay.images == ["only"]))
            # Long enough for many passes at FAKE_HZ.
            time.sleep(0.05)
            self.assertEqual(overlay.images, ["only"])

    def test_a_new_frame_is_drawn(self):
        overlays = Overlays()
        with session(overlays) as vr:
            vr.frame = ("first", False)
            overlay = overlays.wait_for_one()
            self.assertTrue(wait_for(lambda: overlay.images == ["first"]))
            vr.frame = ("second", True)
            self.assertTrue(wait_for(lambda: overlay.images == ["first", "second"]))
            # The alert flag rides with the image: it decides whether the
            # gaze fade is allowed to dim the face.
            self.assertEqual(overlay.alerts, [False, True])

    def test_nothing_is_drawn_while_the_controller_is_away(self):
        overlays = Overlays(attached=False)
        with session(overlays) as vr:
            vr.frame = ("waiting", False)
            overlay = overlays.wait_for_one()
            self.assertTrue(wait_for(lambda: overlay.passes > 5))
            self.assertEqual(overlay.images, [])

    def test_the_face_comes_back_with_the_controller(self):
        # The frame that could not be drawn is not counted as drawn, so
        # the face is there again as soon as there is something to put it
        # on -- without waiting for the next fetch.
        overlays = Overlays(attached=False)
        with session(overlays) as vr:
            vr.frame = ("held", False)
            overlay = overlays.wait_for_one()
            self.assertTrue(wait_for(lambda: overlay.passes > 5))
            overlay.attached = True
            self.assertTrue(wait_for(lambda: overlay.images == ["held"]))

    def test_it_draws_nothing_before_there_is_a_frame(self):
        overlays = Overlays()
        with session(overlays) as vr:
            overlay = overlays.wait_for_one()
            self.assertTrue(wait_for(lambda: overlay.passes > 5))
            self.assertEqual(overlay.images, [])
            self.assertIsNone(vr.frame)


class Attachment(unittest.TestCase):
    """The one thing that travels back out of the thread.

    A window is otherwise identical whether SteamVR is running or not,
    so without this the only way to know the face is on a controller is
    the log.
    """

    def test_it_says_when_a_controller_has_the_face(self):
        overlays = Overlays()
        vr = session(overlays)
        # Before the thread runs, which is also what the window shows
        # for the second before SteamVR answers.
        self.assertFalse(vr.attached)
        with vr:
            overlay = overlays.wait_for_one()
            self.assertTrue(wait_for(lambda: vr.attached))
            overlay.attached = False
            self.assertTrue(wait_for(lambda: not vr.attached))

    def test_a_session_that_ends_lets_go(self):
        # SteamVR going away leaves the window up, so it has to stop
        # claiming a controller is holding the face. The pause before
        # the next session is stretched here so the gap is a state to
        # look at rather than a moment to catch.
        overlays = Overlays(quit_after=2)
        with session(overlays, reopen_sec=5.0) as vr:
            self.assertIsNotNone(overlays.wait_for_one())
            self.assertTrue(wait_for(lambda: vr.attached), "never attached")
            self.assertTrue(wait_for(lambda: not vr.attached), "still attached")

    def test_stopping_lets_go(self):
        overlays = Overlays()
        vr = session(overlays)
        vr.start()
        self.assertIsNotNone(overlays.wait_for_one())
        self.assertTrue(wait_for(lambda: vr.attached))
        vr.stop()
        self.assertFalse(vr.attached)


class Settings(unittest.TestCase):
    def test_settings_are_pushed_once_per_change(self):
        # Compared by identity: a reload rebinds the whole config, so a
        # new object means new settings and the same object means there
        # is nothing to push. Pushing every pass would be hundreds of
        # OpenVR calls a second saying nothing.
        overlays = Overlays()
        with session(overlays) as vr:
            vr.settings = "one"
            overlay = overlays.wait_for_one()
            self.assertTrue(wait_for(lambda: overlay.applied == ["one"]))
            time.sleep(0.05)
            self.assertEqual(overlay.applied, ["one"])
            vr.settings = "two"
            self.assertTrue(wait_for(lambda: overlay.applied == ["one", "two"]))

    def test_nothing_is_pushed_before_there_are_settings(self):
        overlays = Overlays()
        with session(overlays) as vr:
            overlay = overlays.wait_for_one()
            self.assertTrue(wait_for(lambda: overlay.passes > 5))
            self.assertEqual(overlay.applied, [])
            self.assertIsNone(vr.settings)


class Lifecycle(unittest.TestCase):
    def test_stopping_hands_the_overlay_back(self):
        # SteamVR keeps the overlay key otherwise, and the next run
        # cannot create it. This is why stop() joins and Fetcher's does
        # not.
        overlays = Overlays()
        vr = session(overlays)
        vr.start()
        overlay = overlays.wait_for_one()
        self.assertIsNotNone(overlay)
        vr.stop()
        self.assertEqual(overlay.closed, 1)
        self.assertFalse(vr._thread.is_alive())

    def test_the_context_manager_stops_it(self):
        overlays = Overlays()
        with session(overlays) as vr:
            self.assertIsNotNone(overlays.wait_for_one())
        self.assertFalse(vr._thread.is_alive())
        self.assertEqual(overlays.opened[0].closed, 1)

    def test_the_thread_is_a_daemon(self):
        # Whatever the thread is doing must not keep the process alive
        # after the window has been closed.
        vr = session(Overlays())
        self.assertTrue(vr._thread.daemon)

    def test_steamvr_quitting_ends_the_session_and_not_the_thread(self):
        # The headset comes off, SteamVR shuts down, and the window is
        # still there. The thread goes back to waiting for one.
        overlays = Overlays(quit_after=2)
        with session(overlays) as vr:
            self.assertTrue(wait_for(lambda: len(overlays.opened) >= 2))
            self.assertTrue(vr._thread.is_alive())
            # Each one was handed back on the way out.
            self.assertTrue(wait_for(lambda: overlays.opened[0].closed == 1))

    def test_restart_opens_another_overlay(self):
        # What `hand` now costs: the role is read when the overlay is
        # created, so changing it reopens rather than restarting the
        # application.
        overlays = Overlays()
        with session(overlays) as vr:
            first = overlays.wait_for_one()
            self.assertTrue(wait_for(lambda: first.passes > 2))
            vr.restart()
            self.assertTrue(wait_for(lambda: len(overlays.opened) == 2))
            self.assertEqual(first.closed, 1)
            self.assertTrue(wait_for(lambda: overlays.opened[1].passes > 2))

    def test_a_reopened_session_draws_again(self):
        overlays = Overlays()
        with session(overlays) as vr:
            vr.settings = "before"
            vr.frame = ("before", False)
            first = overlays.wait_for_one()
            self.assertTrue(wait_for(lambda: first.images == ["before"]))
            vr.restart()
            second = overlays.wait_for_one(2)
            self.assertIsNotNone(second)
            # The state was handed to the session, not to the overlay,
            # so the new one is caught up without another fetch.
            self.assertTrue(wait_for(lambda: second.images == ["before"]))
            self.assertTrue(wait_for(lambda: second.applied == ["before"]))

    def test_a_failing_overlay_takes_only_the_vr_half_down(self):
        # The window is the resident half and has done nothing wrong.
        def explode():
            raise RuntimeError("no headset")

        vr = VrSession(explode, record_settings, reopen_sec=0.1)
        with self.assertLogs("cgm.vr.session", level="ERROR"):
            vr.start()
            self.assertTrue(wait_for(lambda: not vr._thread.is_alive()))
        vr.stop()


class Haptics(unittest.TestCase):
    def test_a_pulse_reaches_the_controller(self):
        # Asked for from the foreground and made on the thread, so every
        # OpenVR call stays on one thread.
        overlays = Overlays()
        with session(overlays) as vr:
            overlay = overlays.wait_for_one()
            vr.pulse()
            self.assertTrue(wait_for(lambda: overlay.pulses == 1))
            time.sleep(0.05)
            self.assertEqual(overlay.pulses, 1)

    def test_a_pulse_asked_for_early_is_not_lost(self):
        # The alert can fire on the first draw, which is before the
        # thread has an overlay to buzz.
        overlays = Overlays()
        vr = session(overlays)
        vr.pulse()
        with vr:
            overlay = overlays.wait_for_one()
            self.assertTrue(wait_for(lambda: overlay.pulses == 1))


class Threading(unittest.TestCase):
    def test_the_overlay_is_only_ever_touched_by_one_thread(self):
        # OpenVR is not something to call from two threads at once, so
        # everything the foreground asks for is a rebinding or an event
        # and the calls are all made below.
        overlays = Overlays()
        seen: set[int] = set()

        def record(overlay, settings) -> None:
            seen.add(threading.get_ident())
            record_settings(overlay, settings)

        vr = VrSession(overlays, record, reopen_sec=0.1)
        with vr:
            vr.settings = "one"
            overlay = overlays.wait_for_one()
            self.assertTrue(wait_for(lambda: overlay.applied == ["one"]))
        self.assertEqual(len(seen), 1)
        self.assertNotIn(threading.get_ident(), seen)


if __name__ == "__main__":
    unittest.main()
