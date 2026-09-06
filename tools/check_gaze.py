"""Check the gaze fade without a headset.

The fade is only judged in VR by looking away from your own wrist and
deciding whether what is left is still reassuring, which is a slow and
subjective loop for catching a sign error. The properties it has to hold
are asserted here instead:

  - it follows where the head is *looking*, not where the head is, and
    not how far away it is;
  - it is symmetric, so glancing off to the left dims exactly as much as
    glancing the same distance up;
  - it never goes below the floor and never above the configured
    opacity, whatever direction you face;
  - it never reaches zero, because a face that vanished outright would
    look like the process having died;
  - and it does not fade at all while a low is on the face, which is the
    one state the colour is there to shout.

The last two are the conditions NOTES.md set for this being allowed to
exist at all, so they are the ones worth breaking the build over.

Run it after touching _gaze_alpha or _apply_gaze:

    python tools/check_gaze.py
"""

from __future__ import annotations

import math
import sys
import time

# openvr is an optional extra (`pip install -e .[vr]`), and none of this
# needs it: the fade is arithmetic and one setOverlayAlpha call. A stub
# keeps the check able to run on a core-only install, or with no SteamVR.
try:
    import openvr  # noqa: F401
except ModuleNotFoundError:
    import types

    class _HmdMatrix34_t:
        def __init__(self) -> None:
            self.m = [[0.0] * 4 for _ in range(3)]

    stub = types.ModuleType("openvr")
    stub.HmdMatrix34_t = _HmdMatrix34_t  # type: ignore[attr-defined]
    sys.modules["openvr"] = stub

from cgm.vr.overlay import (  # noqa: E402
    GAZE_SMOOTH_SEC,
    WristOverlay,
    _gaze_alpha,
)

FULL = 20.0
FADE = 45.0
MIN = 0.25

HEAD = (0.0, 0.0, 0.0)

# One update at the loop rate, for the easing to be measured against.
TICK = 1.0 / 90.0


def alpha(
    off_deg: float,
    roll_deg: float = 0.0,
    *,
    full: float = FULL,
    fade: float = FADE,
    min_alpha: float = MIN,
    distance: float = 0.5,
) -> float:
    """The alpha with the face `off_deg` away from the centre of view.

    The face is put straight down the head's +Z at `distance` and the
    view direction is swung `off_deg` off it, around whichever axis
    `roll_deg` picks. Swinging the look rather than the face is what
    makes the off-axis angle exactly the parameter, at any distance.
    """
    swing, around = math.radians(off_deg), math.radians(roll_deg)
    forward = (
        math.sin(swing) * math.cos(around),
        math.sin(swing) * math.sin(around),
        math.cos(swing),
    )
    return _gaze_alpha((0.0, 0.0, distance), HEAD, forward, full, fade, min_alpha)


class _Recorder:
    """Stands in for IVROverlay. The fade only ever sets an alpha."""

    def __init__(self) -> None:
        self.alpha: float | None = None

    def setOverlayAlpha(self, handle, value) -> None:  # noqa: N802 (OpenVR's name)
        self.alpha = value


def face(**overrides):
    """A WristOverlay with the fade wired up and nothing else.

    Its __init__ talks to SteamVR, so the object is built without one and
    given only the attributes the fade reads. That is also what keeps
    this honest: if the fade grows a dependency on the rest of the
    overlay, this stops running rather than quietly testing a fiction.
    """
    overlay = object.__new__(WristOverlay)
    state = {
        "offset": (0.0, 0.0, 0.5),
        "orbit": False,
        "orbit_angle": None,
        "orbit_radius": 0.06,
        "gaze": True,
        "gaze_full": FULL,
        "gaze_fade": FADE,
        "gaze_min": MIN,
        "gaze_factor": 1.0,
        "gaze_at": None,
        "alert": False,
        "opacity": 1.0,
        "applied_alpha": None,
    }
    state.update(overrides)
    overlay._overlay = _Recorder()
    overlay._handle = 0
    for name, value in state.items():
        setattr(overlay, f"_{name}", value)
    return overlay


def run(overlay, off_deg: float, seconds: float, tick: float = TICK) -> float:
    """Hold a gaze angle for `seconds` and return where the alpha settled.

    _apply_gaze reads the clock itself, so a tick is simulated by
    backdating when it last ran -- on every call, including the first,
    so that `seconds` really is the span being asked for. The loop's own
    overhead is microseconds against a tick of milliseconds.
    """
    swing = math.radians(off_deg)
    forward = (math.sin(swing), 0.0, math.cos(swing))
    elapsed = 0.0
    while elapsed < seconds - 1e-9:
        overlay._gaze_at = time.perf_counter() - tick
        overlay._apply_gaze(HEAD, forward)
        elapsed += tick
    return overlay._overlay.alpha


def main() -> int:
    named = [
        ("dead ahead", 0.0, 1.0),
        ("at the edge of full", FULL, 1.0),
        ("halfway through the fade", (FULL + FADE) / 2, 1.0 - (1.0 - MIN) / 2),
        ("at the edge of the fade", FADE, MIN),
        ("off to one side", 90.0, MIN),
        ("directly behind", 180.0, MIN),
    ]
    for label, off, expected in named:
        got = alpha(off)
        assert abs(got - expected) < 1e-9, f"{label}: {got:.4f}, want {expected:.4f}"
        print(f"  {label:28} {got:7.3f}")

    # It is an angle off the centre of view, so how far away the face is
    # must not enter into it. A wrist held up by your eye and a wrist
    # across the room fade identically.
    for off in (0.0, 25.0, 32.5, 44.0, 120.0):
        spread = [alpha(off, distance=d) for d in (0.05, 0.2, 0.5, 2.0, 30.0)]
        assert max(spread) - min(spread) < 1e-9, f"{off} deg depends on distance"
    print(f"  {'independent of distance':28} {5 * 5:7d} cases")

    # Symmetric: left, right, up and down are the same glance.
    for off in (0.0, 10.0, 22.0, 32.5, 44.9, 90.0, 179.0):
        spread = [alpha(off, roll) for roll in range(0, 360, 15)]
        assert max(spread) - min(spread) < 1e-9, f"{off} deg is not symmetric"
    print(f"  {'symmetric about the view':28} {7 * 24:7d} cases")

    # Never brighter than full, never dimmer than the floor, and never
    # rising as the face moves further off. Half a degree at a time, all
    # the way round.
    previous, worst = 1.0, 1.0
    for step in range(0, 361):
        got = alpha(step / 2.0)
        assert MIN <= got <= 1.0, f"{step / 2.0} deg gave {got}"
        assert got <= previous + 1e-12, f"brightened at {step / 2.0} deg"
        previous, worst = got, min(worst, got)
    assert worst == MIN, f"never reached the floor: {worst}"
    print(f"  {'monotone and bounded':28} {worst:7.3f} at worst")

    # The floor is the point. Whatever it is set to, something is left.
    for floor in (0.1, 0.25, 0.5, 1.0):
        got = min(alpha(step, min_alpha=floor) for step in range(0, 181, 5))
        assert got == floor > 0.0, f"floor {floor} came out at {got}"
    print(f"  {'the floor is never zero':28} {0.1:7.3f} lowest allowed")

    # cgm.core.config rejects full == fade, but the arithmetic must not
    # divide by zero if it ever gets there anyway.
    assert alpha(29.9, full=30.0, fade=30.0) == 1.0
    assert alpha(30.1, full=30.0, fade=30.0) == MIN
    print(f"  {'no gap between the angles':28} {'steps':>7}")

    # The face sitting exactly at the eye has no direction to measure.
    assert _gaze_alpha(HEAD, HEAD, (0.0, 0.0, 1.0), FULL, FADE, MIN) == 1.0
    print(f"  {'face at the eye':28} {1.0:7.3f}")

    # -- the fade over time --------------------------------------------------

    # Looking away settles on the floor, and looking back returns to full.
    overlay = face()
    settled = run(overlay, 90.0, 2.0)
    assert abs(settled - MIN) < 0.01, f"looking away settled at {settled:.3f}"
    settled = run(overlay, 0.0, 2.0)
    assert abs(settled - 1.0) < 0.01, f"looking back settled at {settled:.3f}"
    print(f"  {'away and back again':28} {settled:7.3f}")

    # The configured opacity is the ceiling the fade works under, not
    # something it overrides.
    overlay = face(opacity=0.6)
    assert abs(run(overlay, 0.0, 2.0) - 0.6) < 0.01, "ignored the set opacity"
    dimmed = run(overlay, 90.0, 2.0)
    assert abs(dimmed - 0.6 * MIN) < 0.01, f"floor under opacity: {dimmed:.3f}"
    print(f"  {'opacity is the ceiling':28} {dimmed:7.3f} at the floor")

    # No fading while low. This is the condition NOTES.md set: colour is
    # the alert, and dimming it at the moment it matters most inverts the
    # priority.
    overlay = face(alert=True)
    for off in (0.0, 45.0, 90.0, 180.0):
        held = run(overlay, off, 1.0)
        assert held == 1.0, f"a low faded to {held:.3f} at {off} deg"
    print(f"  {'a low never fades':28} {1.0:7.3f} held")

    # And a low arriving mid-fade takes effect at once, not over a fade.
    overlay = face()
    run(overlay, 90.0, 2.0)
    overlay.set_alert(True)
    assert overlay._overlay.alpha == 1.0, "a low arrived by fading up"
    print(f"  {'a low arrives at once':28} {1.0:7.3f}")

    # Switching the fade off must put back what was configured, or it
    # would be possible to be left dim with nothing to undim it.
    overlay = face(opacity=0.8)
    run(overlay, 90.0, 2.0)
    overlay.set_gaze(False, FULL, FADE, MIN)
    assert overlay._overlay.alpha == 0.8, "switching it off left the face dim"
    print(f"  {'switching it off restores':28} {0.8:7.3f}")

    # The easing is a rate, not a step per update, so the same span of
    # time gets to the same place whatever the loop rate was. This is
    # what stops the fade changing character when the loop stutters, and
    # 10Hz here is a bad stutter. The rates all divide the span, so each
    # one covers it exactly and the comparison is of the easing rather
    # than of where a partial tick left off.
    span = 1.5
    settled = [
        run(face(), 90.0, span, tick)
        for tick in (1.0 / 120.0, 1.0 / 60.0, 1.0 / 30.0, 1.0 / 10.0)
    ]
    spread = max(settled) - min(settled)
    assert spread < 0.005, f"the fade depends on the loop rate: {settled}"
    print(f"  {'the fade is rate based':28} {spread:7.4f} spread")

    # A time constant closes 63% of the gap, so the span above is four of
    # them and the fade is over by the end of it. Which is the number that
    # matters in the headset: look away and the face is down inside a
    # second and a half, not still visibly moving ten seconds later.
    assert abs(settled[0] - MIN) < 0.02, f"{span}s in, still at {settled[0]:.3f}"
    print(f"  {'and is done within it':28} {settled[0]:7.3f} after {span}s")

    print("gaze fade OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
