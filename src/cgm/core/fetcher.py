"""Running the fetch schedule on a thread of its own.

`Poller.poll` makes a blocking HTTPS request, and the client gives it
fifteen seconds before it gives up. Whether that matters depends
entirely on what else the calling loop owes somebody.

The VR loop can afford to call it inline: SteamVR's compositor keeps
drawing the last frame at the live controller pose whatever this process
is doing, so a stalled loop looks like a face that is not updating, and
it was only going to update once a minute anyway. A GUI has nobody doing
that for it. Fifteen seconds of not returning to the event loop is
fifteen seconds of a window that does not repaint and that Windows
retitles "Not Responding".

So the schedule can be handed to this instead, and the loop that owns
the screen never blocks on the network.

It lives in `cgm.core` rather than beside the window because it needs
nothing but a poller and a clock. Nothing here knows what the readings
are for.
"""

from __future__ import annotations

import threading
import time

from cgm.core.poller import Poller

# How often the thread looks at the clock. The schedule it is checking
# is a minute long, so this is not about precision -- it is how quickly
# a reloaded `polling.interval_sec` starts being obeyed. A quarter of a
# second of a sleeping thread costs nothing.
TICK_SEC = 0.25


class Fetcher:
    """Drives one poller from a background thread.

    The thread owns the poller: it is the only thing that calls `poll`.
    The foreground reads `poller.reading` and `poller.error`, which the
    poller replaces with a single assignment each, and may call
    `set_interval` and `set_trend` on a config reload, which are also
    single assignments. Every one of those is one rebinding of one
    attribute, so a reader sees either the old value or the new one and
    never a half-built anything. That is why there is no lock here, and
    it is a property of what is being shared rather than luck -- adding
    state that takes two assignments to update would need one.

    The thread is a daemon and `stop()` does not join it. A fetch in
    flight is a socket read with a timeout on it, and waiting up to
    fifteen seconds to close a window nobody is looking at any more is a
    worse bargain than letting the interpreter drop it on the way out.
    """

    def __init__(self, poller: Poller) -> None:
        self._poller = poller
        self._stop = threading.Event()
        self._thread = threading.Thread(
            target=self._loop, name="cgm-fetch", daemon=True
        )

    def __enter__(self) -> "Fetcher":
        self.start()
        return self

    def __exit__(self, *exc) -> None:
        self.stop()

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        """Ask the thread to finish. It does not block."""
        self._stop.set()

    def _loop(self) -> None:
        while not self._stop.is_set():
            # monotonic rather than perf_counter: it is coarse on
            # Windows, stepping in 15.6ms, and a schedule measured in
            # minutes cannot tell. The VR loop needs the fine one
            # because it paces a frame interval with it; this does not.
            now = time.monotonic()
            if self._poller.due(now):
                self._poller.poll(now)
            # wait(), not sleep(): stop() then returns immediately
            # rather than after the rest of the tick.
            self._stop.wait(TICK_SEC)
