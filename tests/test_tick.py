"""What one pass of the draw loop does with each frontend.

`cgm.main.Tick` is where the poller, the two frontends and the one alert
meet: which face goes where, what a reload reaches, when a low is
announced and on which channel. It touches Tk and OpenVR only through
the window and the session it is handed, so here it is handed stand-ins
that write down what they were told.

The frontends themselves are covered elsewhere -- `tests/test_desk.py`
and `tools/check_settings.py` for the window, `tests/test_vr_session.py`
for the overlay's thread -- and this does not repeat them.
"""

from __future__ import annotations

import unittest
from datetime import datetime, timezone
from pathlib import Path

from cgm.core.alert import LowAlert
from cgm.core.config import Config
from cgm.core.librelink import Reading
from cgm.face.renderer import GRAPH_HEIGHT, HEIGHT
from cgm.main import Tick, alert_tuning

PATH = Path("config.toml")


def config(**sections) -> Config:
    """A config with the sound off, so a low in a test makes no noise."""
    cfg = Config()
    cfg.polling.alert_sound = False
    for section, values in sections.items():
        for key, value in values.items():
            setattr(getattr(cfg, section), key, value)
    return cfg


def reading(mgdl: float) -> Reading:
    return Reading(mgdl, 3, datetime.now(timezone.utc))


class FakePoller:
    def __init__(self) -> None:
        self.reading = None
        self.error = None
        self.interval = None
        self.trend = None

    def set_interval(self, interval: float) -> None:
        self.interval = interval

    def set_trend(self, trend) -> None:
        self.trend = trend


class FakeWatcher:
    """Hands back an edit once, the way `ConfigWatcher.poll` does."""

    def __init__(self) -> None:
        self.edit = None

    def poll(self):
        edit, self.edit = self.edit, None
        return edit


class FakeWindow:
    def __init__(self) -> None:
        self.image = None
        self.title = None
        self.vr = None
        self.scale = None
        self.on_top = None

    def set_image(self, image) -> None:
        self.image = image

    def set_title(self, text: str) -> None:
        self.title = text

    def set_vr(self, active: bool) -> None:
        self.vr = active

    def set_scale(self, scale: float) -> None:
        self.scale = scale

    def set_always_on_top(self, on_top: bool) -> None:
        self.on_top = on_top


class FakeSession:
    def __init__(self) -> None:
        self.settings = None
        self.frame = None
        self.attached = False
        self.restarts = 0
        self.pulses = 0

    def restart(self) -> None:
        self.restarts += 1

    def pulse(self) -> None:
        self.pulses += 1


class TickTestCase(unittest.TestCase):
    def build(self, cfg: Config | None = None, *, window=True, session=True) -> Tick:
        cfg = cfg or config()
        self.poller = FakePoller()
        self.watcher = FakeWatcher()
        # True for a plain stand-in, or a stand-in of the test's own.
        self.window = (FakeWindow() if window is True else window) or None
        self.session = FakeSession() if session else None
        return Tick(
            cfg,
            PATH,
            poller=self.poller,
            watcher=self.watcher,
            alert=LowAlert(**alert_tuning(cfg)),
            window=self.window,
            session=self.session,
        )


class Drawing(TickTestCase):
    def test_both_frontends_get_a_face(self):
        tick = self.build()
        self.poller.reading = reading(112)
        tick()
        self.assertIsNotNone(self.window.image)
        self.assertIn("112 mg/dL", self.window.title)
        image, is_low = self.session.frame
        self.assertIsNotNone(image)
        self.assertFalse(is_low)

    def test_each_frontend_gets_its_own_graph_setting(self):
        # The window is read at a desk and gets the sparkline by default;
        # the overlay is glanced at and does not.
        tick = self.build()
        self.poller.reading = reading(112)
        tick()
        self.assertEqual(self.window.image.height, HEIGHT + GRAPH_HEIGHT)
        self.assertEqual(self.session.frame[0].height, HEIGHT)

    def test_a_message_card_before_the_first_reading(self):
        tick = self.build()
        tick.show_message("CONNECTING")
        self.assertIsNotNone(self.window.image)
        self.assertFalse(self.session.frame[1])

    def test_the_title_says_what_went_wrong_when_there_is_nothing_to_show(self):
        tick = self.build()
        self.poller.error = "NO CONNECTION"
        tick()
        self.assertIn("NO CONNECTION", self.window.title)

    def test_a_low_pins_the_overlay_against_the_gaze_fade(self):
        tick = self.build()
        self.poller.reading = reading(60)
        tick()
        self.assertTrue(self.session.frame[1])

    def test_the_window_says_whether_the_overlay_is_up(self):
        tick = self.build()
        tick()
        self.assertFalse(self.window.vr)
        self.session.attached = True
        tick()
        self.assertTrue(self.window.vr)

    def test_a_window_alone_says_the_overlay_is_not_up(self):
        tick = self.build(session=False)
        tick()
        self.assertFalse(self.window.vr)

    def test_the_overlay_alone_needs_no_window(self):
        tick = self.build(window=False)
        self.poller.reading = reading(112)
        tick()
        self.assertIsNotNone(self.session.frame)


class Announcing(TickTestCase):
    def test_a_low_buzzes_once_on_the_way_in(self):
        tick = self.build()
        self.poller.reading = reading(60)
        tick()
        tick()
        self.assertEqual(self.session.pulses, 1)

    def test_the_master_switch_silences_every_channel(self):
        tick = self.build(config(polling={"alert_on_low": False}))
        self.poller.reading = reading(60)
        tick()
        self.assertEqual(self.session.pulses, 0)

    def test_the_buzz_can_be_switched_off_on_its_own(self):
        tick = self.build(config(polling={"alert_haptic": False}))
        self.poller.reading = reading(60)
        tick()
        self.assertEqual(self.session.pulses, 0)

    def test_a_window_alone_still_announces_without_a_controller(self):
        # No session to buzz; the sound is the channel, and it is off in
        # these tests. What matters is that nothing reaches for a
        # controller that is not there.
        tick = self.build(session=False)
        self.poller.reading = reading(60)
        tick()


class BrokenWindow(FakeWindow):
    """A window whose drawing raises until it is told to stop."""

    def __init__(self, error: BaseException) -> None:
        super().__init__()
        self.error = error

    def set_image(self, image) -> None:
        if self.error is not None:
            raise self.error
        super().set_image(image)


class Failing(TickTestCase):
    """A pass that raises must not be the last pass.

    Both loops only call again once a call returns, so an exception out
    of here would leave the window frozen on its last frame, age readout
    and all.
    """

    def build_broken(self, error: BaseException) -> Tick:
        tick = self.build(window=BrokenWindow(error))
        self.poller.reading = reading(112)
        return tick

    def test_a_pass_that_raises_does_not_raise_out(self):
        tick = self.build_broken(RuntimeError("drawing broke"))
        with self.assertLogs("vrcgm", level="ERROR"):
            tick()

    def test_a_repeating_fault_logs_its_traceback_once(self):
        tick = self.build_broken(RuntimeError("drawing broke"))
        with self.assertLogs("vrcgm", level="ERROR") as logged:
            tick()
            tick()
            tick()
        self.assertEqual(len(logged.records), 1)
        self.assertIsNotNone(logged.records[0].exc_info)

    def test_the_next_pass_draws_once_the_fault_clears(self):
        tick = self.build_broken(RuntimeError("drawing broke"))
        with self.assertLogs("vrcgm", level="ERROR"):
            tick()
        self.window.error = None
        with self.assertLogs("vrcgm", level="INFO") as logged:
            tick()
        self.assertIsNotNone(self.window.image)
        self.assertIn("recovered", "\n".join(logged.output))

    def test_ctrl_c_still_gets_out(self):
        # It is how both loops are stopped, so it is the one thing that
        # has to pass straight through.
        tick = self.build_broken(KeyboardInterrupt())
        with self.assertRaises(KeyboardInterrupt):
            tick()


class Reloading(TickTestCase):
    def test_an_edit_reaches_the_poller_and_the_alert(self):
        tick = self.build()
        self.watcher.edit = config(
            polling={"interval_sec": 90.0}, thresholds={"low_mgdl": 80.0}
        )
        self.poller.reading = reading(75)
        tick()
        self.assertEqual(self.poller.interval, 90.0)
        self.assertIsNotNone(self.poller.trend)
        # 75 is only low under the edited threshold.
        self.assertEqual(self.session.pulses, 1)
        self.assertTrue(self.session.frame[1])

    def test_the_overlay_is_handed_the_whole_edit(self):
        tick = self.build()
        edit = config(vr={"width_m": 0.2})
        self.watcher.edit = edit
        tick()
        self.assertIs(self.session.settings, edit)
        self.assertIs(tick.cfg, edit)

    def test_a_new_hand_reopens_the_session(self):
        tick = self.build()
        self.watcher.edit = config(vr={"hand": "right"})
        tick()
        self.assertEqual(self.session.restarts, 1)

    def test_the_same_hand_does_not(self):
        tick = self.build()
        self.watcher.edit = config(vr={"width_m": 0.2})
        tick()
        self.assertEqual(self.session.restarts, 0)

    def test_the_window_settings_reach_the_window(self):
        tick = self.build()
        self.watcher.edit = config(window={"scale": 2.0, "always_on_top": False})
        tick()
        self.assertEqual(self.window.scale, 2.0)
        self.assertFalse(self.window.on_top)

    def test_turning_the_graph_on_grows_the_card_on_the_next_frame(self):
        tick = self.build()
        self.poller.reading = reading(112)
        self.watcher.edit = config(graph={"in_vr": True, "in_window": False})
        tick()
        self.assertEqual(self.session.frame[0].height, HEIGHT + GRAPH_HEIGHT)
        self.assertEqual(self.window.image.height, HEIGHT)

    def test_an_account_edit_says_it_needs_a_restart(self):
        tick = self.build()
        self.watcher.edit = config(account={"region": "jp"})
        with self.assertLogs("vrcgm", level="WARNING") as logged:
            tick()
        self.assertIn("account.region", "\n".join(logged.output))


if __name__ == "__main__":
    unittest.main()
