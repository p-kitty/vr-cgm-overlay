"""The fetch schedule running on a thread of its own.

`Fetcher` exists so a GUI never blocks on the network. What is asserted
here is the contract that makes it usable: it only polls when the poller
says it is due, it stops when asked, and it keeps its hands off
everything else.

There is no real poller here and no clock to wait on. A schedule that
had to be waited out in real time would make this suite take minutes,
so the stand-in decides for itself when it is due.
"""

from __future__ import annotations

import threading
import unittest

from cgm.core import fetcher as fetcher_mod
from cgm.core.fetcher import Fetcher


class FakePoller:
    """Answers `due` however the test wants and counts the polls."""

    def __init__(self, *, due: bool = True) -> None:
        self._due = due
        self.polls = 0
        self.times: list[float] = []
        self.polled = threading.Event()

    def due(self, now: float) -> bool:
        return self._due

    def poll(self, now: float) -> bool:
        self.polls += 1
        self.times.append(now)
        self.polled.set()
        return True


class Driving(unittest.TestCase):
    def run_until_polled(self, poller: FakePoller, *, timeout: float = 5.0) -> bool:
        with Fetcher(poller):
            return poller.polled.wait(timeout)

    def test_it_polls_when_the_poller_says_it_is_due(self):
        poller = FakePoller(due=True)
        self.assertTrue(
            self.run_until_polled(poller), "the thread never polled"
        )
        self.assertGreaterEqual(poller.polls, 1)

    def test_it_does_not_poll_when_nothing_is_due(self):
        # The schedule belongs to the poller. This only turns the handle,
        # and must not decide for itself that a fetch is warranted.
        poller = FakePoller(due=False)
        with Fetcher(poller):
            poller.polled.wait(fetcher_mod.TICK_SEC * 3)
        self.assertEqual(poller.polls, 0)

    def test_the_clock_it_passes_moves_forward(self):
        # `due` and `poll` are handed the same clock the poller schedules
        # against, so it has to be one that only increases.
        poller = FakePoller(due=True)
        with Fetcher(poller):
            poller.polled.wait(5.0)
        self.assertEqual(poller.times, sorted(poller.times))

    def test_stopping_ends_the_thread(self):
        poller = FakePoller(due=True)
        fetcher = Fetcher(poller)
        fetcher.start()
        self.assertTrue(poller.polled.wait(5.0))
        fetcher.stop()
        # stop() does not join -- a fetch in flight is a socket read with
        # a timeout on it -- but with nothing in flight the thread is
        # gone within a tick.
        fetcher._thread.join(timeout=5.0)
        self.assertFalse(fetcher._thread.is_alive())

    def test_the_thread_is_a_daemon(self):
        # A window closed on a stalled fetch must not keep the process
        # alive waiting for a socket nobody is reading any more.
        fetcher = Fetcher(FakePoller())
        self.assertTrue(fetcher._thread.daemon)

    def test_the_context_manager_stops_it(self):
        poller = FakePoller(due=True)
        with Fetcher(poller) as fetcher:
            self.assertTrue(poller.polled.wait(5.0))
        fetcher._thread.join(timeout=5.0)
        self.assertFalse(fetcher._thread.is_alive())


if __name__ == "__main__":
    unittest.main()
