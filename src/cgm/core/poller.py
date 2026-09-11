"""The fetch schedule and its backoff.

Frontend-neutral: it takes `now` as an argument and its client as a
constructor argument, so it runs the same on the fetch thread
(`cgm.core.fetcher`) or in a test with no clock at all.
"""

from __future__ import annotations

import logging
import random

from cgm.core.librelink import AuthError, LibreLinkError, LibreLinkUp
from cgm.face.renderer import TrendTuning

# The application's logger, not this module's: these lines are the run
# log the user reads, and they should not change name with the file.
log = logging.getLogger("vrcgm")

MAX_BACKOFF_SEC = 600.0


class Poller:
    """Owns the fetch schedule and its backoff.

    Repeated failures back off exponentially: hammering the API through a
    network outage or a service problem does not speed up recovery and
    only raises the odds of being cut off.
    """

    def __init__(
        self,
        client: LibreLinkUp,
        interval: float,
        trend: TrendTuning | None = None,
    ) -> None:
        self._client = client
        self._interval = interval
        # Only the log line uses this; run() always passes the config's.
        self._trend = trend or TrendTuning()
        self._next_at = 0.0
        self._failures = 0

        self.reading = None
        self.error: str | None = None

    def due(self, now: float) -> bool:
        return now >= self._next_at

    def set_interval(self, interval: float) -> None:
        """Change the fetch interval, effective from the next fetch."""
        self._interval = interval

    def set_trend(self, trend: TrendTuning) -> None:
        """Change the tuning the logged trend is worked out with."""
        self._trend = trend

    def poll(self, now: float) -> bool:
        """Attempt one fetch. True when a new reading arrived."""
        got_new = False
        try:
            latest = self._client.get_latest()
            # Say which of the three sources the arrow came from, not
            # just where it points. Which one a poll reaches can only be
            # seen against live data -- the bend needs history the API
            # is publishing on time -- and this is where it shows. A
            # bend that quietly stopped appearing would otherwise read
            # as calm glucose.
            #
            # And say how far behind the history is, because that is
            # what decides it: past BEND_MAX_SPAN_MIN of lag the third
            # point is too old to bend through, and a `(fit)` with no
            # number beside it cannot say whether that is the reason.
            #
            # Worked out before the reading is taken, because the face
            # draws the same shape: a reading this cannot describe is one
            # the draw loop would fail on every second, and it is better
            # refused here as a failed fetch, with the last good reading
            # left up to grey.
            shape = self._trend.shape_for(latest)
            lag = latest.history_lag_minutes()
            log.info(
                "fetched: %.0f mg/dL %s (%.1f min old, %s)",
                latest.value_mgdl,
                shape.describe(latest.arrow),
                latest.age_minutes(),
                "no history" if lag is None else f"history {lag:.1f} min behind",
            )
            self.reading = latest
            self.error = None
            self._failures = 0
            got_new = True
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
        except Exception:
            # Anything else is a response the client cannot read -- the
            # unofficial API changing shape is the likeliest cause, as a
            # KeyError on a renamed field -- or a bug on the way through
            # it. Raised, it would end the fetch thread for good: the face
            # would grey and stay grey even once the API came back. So it
            # is a failed fetch like any other, backed off like one, and
            # logged with its traceback, since that is the only record of
            # what the response looked like.
            self.error = "API ERROR"
            self._failures += 1
            log.exception("fetch failed (attempt %d)", self._failures)

        # Jitter keeps many clients from landing on the same instant.
        if self._failures:
            delay = min(self._interval * (2 ** self._failures), MAX_BACKOFF_SEC)
        else:
            delay = self._interval
        self._next_at = now + delay + random.uniform(0, 3)
        return got_new

