"""Deciding when a low announces itself, and making the noise.

The decision and the noise are separated on purpose. `LowAlert` is
arithmetic over a value and a clock and touches nothing; `play` is the
one call that reaches the sound card. That way the rules below can be
asserted headless, which matters because they are the rules for the
alert on a glucose reading and "it seemed to work when I tried it" is
not a standard they should be held to.

This lives in `cgm.core` because both frontends need the same answer.
The edge test used to be four lines inline in the VR draw loop; a second
frontend would have copied it, and two copies of "has it just gone low"
is how the window and the headset end up disagreeing about whether you
have already been told.

Sound rather than only haptics because sound is the channel that reaches
you *without looking at your wrist*, which is exactly the case a low
starting mid-game presents -- and because `triggerHapticPulse` is silent
on the one stack this has ever been tried on. It changes nothing about
the standing position: **the face is the real alert**, and everything
here is a supplement to it.
"""

from __future__ import annotations

import logging
from pathlib import Path

log = logging.getLogger("vrcgm")

try:
    import winsound
except ImportError:  # not Windows
    # Everything else here still works; the alert is simply mute. The
    # rest of the project assumes Windows too (fonts under
    # C:/Windows/Fonts, winmm for the timer period), so this is about
    # keeping the module importable for tests rather than portability.
    winsound = None  # type: ignore[assignment]


class LowAlert:
    """Decides whether a reading should announce a low, and how often.

    Three rules, each of which exists because of a specific way an
    alert on a glucose reading goes wrong.

    **It fires on the way in, not throughout.** A tone every minute for
    the length of a real low is the reason people mute alerts, and a
    muted alert is worse than none because it is trusted. `repeat_min`
    can turn a repeat on for anyone who would rather risk the nuisance
    than sleep through it, and it is off by default.

    **It re-arms above a margin, not at the threshold.** A reading
    hovering at the low threshold crosses it repeatedly -- the sensor's
    own noise is a couple of mg/dL, which is the same size as the moves
    being watched here -- and a bare `was_low` flag would announce every
    one of those crossings as a new low. Recovery has to be clear of the
    threshold before the next dip counts as news.

    **It says nothing without a reading.** No reading is not "not low":
    before the first fetch lands there is no information, and a failed
    fetch leaves the last value standing rather than clearing it. Either
    way, silence.
    """

    def __init__(
        self,
        low_mgdl: float,
        *,
        rearm_mgdl: float = 5.0,
        repeat_min: float = 0.0,
    ) -> None:
        self._low = low_mgdl
        self._rearm = rearm_mgdl
        self._repeat = repeat_min
        # Armed means "a low from here would be news". It starts armed:
        # the first reading of a session being low is worth saying.
        self._armed = True
        self._last_fired: float | None = None

    def set_tuning(
        self, low_mgdl: float, rearm_mgdl: float, repeat_min: float
    ) -> None:
        """Take an edited config without losing where we are.

        The armed flag survives deliberately. Nudging a threshold with
        the headset on must not re-announce a low you are already
        looking at.
        """
        self._low = low_mgdl
        self._rearm = rearm_mgdl
        self._repeat = repeat_min

    def update(self, mgdl: float | None, now: float) -> bool:
        """True when this reading should sound the alert.

        `now` is monotonic seconds; only differences are used, so which
        monotonic clock it comes from does not matter as long as one
        instance is always given the same one.
        """
        if mgdl is None:
            return False

        if mgdl >= self._low + self._rearm:
            # Clear of the threshold by the margin: the next dip is news
            # again.
            self._armed = True
            self._last_fired = None
            return False

        if mgdl >= self._low:
            # Above the threshold but inside the margin. Not low, and
            # not recovered enough to re-arm either -- this is the band
            # that stops an oscillation being announced twice.
            return False

        # Low.
        if self._armed:
            self._armed = False
            self._last_fired = now
            return True

        if self._repeat > 0 and self._last_fired is not None:
            if now - self._last_fired >= self._repeat * 60.0:
                self._last_fired = now
                return True

        return False


def play(sound_path: str = "") -> None:
    """Sound the alert once, without waiting for it to finish.

    SND_ASYNC is not optional. Without it PlaySound blocks until the
    clip ends, which stalls the VR draw loop and freezes the window's
    event loop -- for an alert whose entire purpose is to arrive while
    something else is going on.

    An empty path means the user's own configured Exclamation sound via
    MessageBeep, which is why nothing ships a WAV: a bundled tone is a
    binary in the repository that most people would want to replace
    anyway, and MessageBeep is already whatever they chose.

    Volume is the system mixer's business. Taking that on would mean an
    audio library, and the dependency list is deliberately short.
    """
    if winsound is None:
        log.debug("no winsound on this platform; the low alert is mute")
        return

    if sound_path:
        # Checked here as well as at load, because a file can be moved
        # between the two. Falling back to the beep rather than going
        # quiet: a missing file is a reason to be louder, not to skip
        # the one alert that reaches you without looking.
        if Path(sound_path).exists():
            winsound.PlaySound(
                sound_path, winsound.SND_FILENAME | winsound.SND_ASYNC
            )
            return
        log.warning("alert sound %s is gone; using the system beep", sound_path)

    winsound.MessageBeep(winsound.MB_ICONEXCLAMATION)
