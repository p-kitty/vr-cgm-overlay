"""The fetch schedule and its backoff.

Frontend-neutral: it takes `now` as an argument and its client as a
constructor argument, so it runs the same under a VR draw loop, a GUI
event loop, or a test with no clock at all.
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
            self.reading = self._client.get_latest()
            self.error = None
            self._failures = 0
            got_new = True
            # Say which source the arrow came from, not just where it
            # points. Whether the local fit is actually being used can
            # only be seen against live data, and this is where it shows.
            slope = self._trend.slope_for(self.reading)
            trend = (
                f"{self.reading.arrow} (API)"
                if slope is None
                else f"{slope:+.2f} mg/dL/min"
            )
            log.info(
                "fetched: %.0f mg/dL %s (%.1f min old)",
                self.reading.value_mgdl,
                trend,
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

