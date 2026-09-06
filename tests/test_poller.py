"""The fetch schedule and its backoff.

`Poller` takes `now` as an argument and its client as a constructor
argument, so the whole of it runs here in a few microseconds with no
clock and no network.

Backoff is worth pinning down because both of its failure modes are
invisible from the outside: too eager and the account risks being cut
off, which is the one thing the polling interval exists to prevent; too
slow and a recovered network leaves the face showing a stale value for
much longer than it needs to.
"""

from __future__ import annotations

import logging
import unittest
from datetime import datetime, timedelta, timezone

from librelink import AuthError, GlucosePoint, LibreLinkError, Reading
from main import MAX_BACKOFF_SEC, Poller
from renderer import TrendTuning

INTERVAL = 60.0

# Jitter spreads the next fetch over a three second window, so a wait can
# only be asserted as a range. This is that range's width.
JITTER_SEC = 3.0

EPSILON = 0.001


def setUpModule():
    # Poller logs every outcome. Without a handler somewhere on the logger,
    # warnings land on stderr and bury the test output.
    logging.getLogger("vrcgm").addHandler(logging.NullHandler())


def reading(mgdl: float = 100.0, slope: float | None = None) -> Reading:
    taken_at = datetime.now(timezone.utc) - timedelta(minutes=1)
    history = ()
    if slope is not None:
        history = tuple(
            GlucosePoint(taken_at - timedelta(minutes=ago), mgdl - slope * ago)
            for ago in range(20, -1, -1)
        )
    return Reading(
        value_mgdl=mgdl,
        trend=3,
        timestamp_utc=taken_at,
        is_high=False,
        is_low=False,
        history=history,
    )


class FakeClient:
    """A scripted stand-in for LibreLinkUp.

    Each outcome is either a Reading to return or an exception to raise.
    The last outcome repeats, so a test can keep polling without listing
    the same failure over and over.
    """

    def __init__(self, *outcomes):
        self.outcomes = list(outcomes) or [reading()]
        self.calls = 0

    def get_latest(self) -> Reading:
        self.calls += 1
        outcome = self.outcomes[0]
        if len(self.outcomes) > 1:
            self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class PollerTestCase(unittest.TestCase):
    def assertWaits(self, poller: Poller, since: float, delay: float) -> None:
        """Assert the next fetch is one `delay` away, jitter allowed."""
        self.assertFalse(
            poller.due(since + delay - EPSILON),
            f"fetched sooner than {delay}s",
        )
        self.assertTrue(
            poller.due(since + delay + JITTER_SEC + EPSILON),
            f"waited longer than {delay}s plus jitter",
        )


class FirstFetch(PollerTestCase):
    def test_is_due_immediately(self):
        # Nothing on screen yet, so the first fetch must not wait out an
        # interval before the face can show anything.
        poller = Poller(FakeClient(), INTERVAL)
        self.assertTrue(poller.due(0.0))


class SuccessfulFetch(PollerTestCase):
    def setUp(self):
        self.client = FakeClient(reading(112.0))
        self.poller = Poller(self.client, INTERVAL)

    def test_reports_a_new_reading(self):
        self.assertTrue(self.poller.poll(1000.0))
        self.assertEqual(self.poller.reading.value_mgdl, 112.0)
        self.assertIsNone(self.poller.error)

    def test_waits_one_interval(self):
        self.poller.poll(1000.0)
        self.assertWaits(self.poller, 1000.0, INTERVAL)


class FailedFetch(PollerTestCase):
    def test_keeps_the_last_reading(self):
        # The value staying up with a climbing age is the intended
        # behaviour: going blank mid-session is the dangerous failure.
        poller = Poller(FakeClient(reading(112.0), LibreLinkError("down")), INTERVAL)
        poller.poll(1000.0)
        self.assertFalse(poller.poll(1100.0))
        self.assertEqual(poller.reading.value_mgdl, 112.0)
        self.assertEqual(poller.error, "NO CONNECTION")

    def test_a_socket_error_counts_as_a_failure(self):
        # requests raises OSError subclasses for DNS and connection
        # trouble, which is the common case for a headset off the network.
        poller = Poller(FakeClient(OSError("no route")), INTERVAL)
        self.assertFalse(poller.poll(1000.0))
        self.assertEqual(poller.error, "NO CONNECTION")

    def test_backs_off_exponentially(self):
        poller = Poller(FakeClient(LibreLinkError("down")), INTERVAL)
        for attempt, expected in enumerate([120.0, 240.0, 480.0], start=1):
            with self.subTest(attempt=attempt):
                poller.poll(1000.0)
                self.assertWaits(poller, 1000.0, expected)

    def test_backoff_stops_at_the_ceiling(self):
        # Doubling without a ceiling reaches hours, and a session that
        # recovered would sit there showing a stale value.
        poller = Poller(FakeClient(LibreLinkError("down")), INTERVAL)
        for _ in range(12):
            poller.poll(1000.0)
        self.assertWaits(poller, 1000.0, MAX_BACKOFF_SEC)

    def test_recovery_resets_the_backoff(self):
        poller = Poller(
            FakeClient(
                LibreLinkError("down"),
                LibreLinkError("down"),
                reading(112.0),
            ),
            INTERVAL,
        )
        poller.poll(1000.0)
        poller.poll(1000.0)
        self.assertTrue(poller.poll(2000.0))
        self.assertWaits(poller, 2000.0, INTERVAL)
        self.assertIsNone(poller.error)


class AuthFailure(PollerTestCase):
    def setUp(self):
        self.poller = Poller(FakeClient(AuthError("bad password")), INTERVAL)
        self.poller.poll(1000.0)

    def test_says_so_on_the_face(self):
        # Distinct from NO CONNECTION on purpose: one is waited out, the
        # other needs the config fixing.
        self.assertEqual(self.poller.error, "AUTH ERROR")

    def test_waits_the_maximum_at_once(self):
        # A wrong password does not come right by being retried, so this
        # skips the doubling and goes straight to the ceiling. Retrying it
        # every minute is how an account gets locked.
        self.assertWaits(self.poller, 1000.0, MAX_BACKOFF_SEC)


class IntervalChange(PollerTestCase):
    """A reloaded config.toml can change the interval mid-session."""

    def setUp(self):
        self.poller = Poller(FakeClient(), INTERVAL)
        self.poller.poll(1000.0)
        self.poller.set_interval(300.0)

    def test_the_pending_fetch_is_left_alone(self):
        # Documented as effective from the next fetch. Rescheduling the
        # pending one would be defensible too, but silently doing neither
        # is what a change here would look like.
        self.assertWaits(self.poller, 1000.0, INTERVAL)

    def test_the_fetch_after_that_uses_the_new_interval(self):
        self.poller.poll(2000.0)
        self.assertWaits(self.poller, 2000.0, 300.0)


class FetchLog(unittest.TestCase):
    """What a session's log says about where the arrow came from.

    Whether the local trend behaves on a real arm can only be judged
    from this line -- there is no other way to see it without a headset
    and a day of readings -- so it has to say which of the two sources
    was used, and it has to not raise while saying it.
    """

    def log_line(self, entry: Reading, trend: TrendTuning | None = None) -> str:
        poller = Poller(FakeClient(entry), INTERVAL, trend)
        with self.assertLogs("vrcgm", level="INFO") as caught:
            poller.poll(0.0)
        return caught.output[0]

    def test_a_fitted_slope_is_logged_as_a_rate(self):
        self.assertIn("+1.50 mg/dL/min", self.log_line(reading(slope=1.5)))

    def test_a_fall_keeps_its_sign(self):
        self.assertIn("-2.00 mg/dL/min", self.log_line(reading(slope=-2.0)))

    def test_the_fallback_says_it_came_from_the_api(self):
        # Otherwise a session where the fit never once succeeded would
        # look exactly like one where it always did.
        self.assertIn("(API)", self.log_line(reading()))

    def test_switching_the_fit_off_is_logged_as_the_api_arrow(self):
        # The reading can be fitted; the config says not to. The log has
        # to follow the config, or it describes an arrow the face is not
        # drawing.
        line = self.log_line(reading(slope=1.5), TrendTuning(local=False))
        self.assertIn("(API)", line)
        self.assertNotIn("mg/dL/min", line)


if __name__ == "__main__":
    unittest.main()
