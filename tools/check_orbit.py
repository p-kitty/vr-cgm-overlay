"""Check the orbit geometry without a headset.

Orbit mode is the one piece of placement that is computed rather than
typed into config.toml, and the only way to judge it in VR is to put the
headset on and turn your hand. That is a poor loop for catching a sign
error, so the properties it has to hold are asserted here instead:

  - the face stays exactly orbit_radius_m off the arm's centreline;
  - its axes stay orthonormal and right handed, or SteamVR draws it
    mirrored or skewed;
  - it points away from the arm, never into it -- the whole point;
  - it never travels past orbit_limit_deg, even across the antipode,
    where the nearest side of the arm flips from one limit to the other;
  - and the guide markers, which are turned to the head one by one so the
    orbit arc is not edge-on, come out square and actually facing it.

Run it after touching _orbit_transform:

    python tools/check_orbit.py
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

# openvr is not installed everywhere this runs, and the geometry does not
# need it: only the matrix type it returns. A stub keeps the check able to
# run on a machine with no SteamVR.
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

from armguide import MARKER_COUNT as MARKERS  # noqa: E402
from overlay import _billboard, _orbit_transform  # noqa: E402

CENTRE = (0.0, -0.02, 0.10)
RADIUS = 0.06
LIMIT = 120.0
NO_TRIM = (0.0, 0.0, 0.0)

# HmdMatrix34_t stores 32 bit floats, so anything read back out of one is
# only good to about seven digits. Angles come back as Python floats and
# are compared far more tightly.
TOL = 1e-5

# One update at the loop rate, for the easing to be measured against.
TICK = 1.0 / 90.0


def _dot(a, b):
    return sum(p * q for p, q in zip(a, b))


def _cross(a, b):
    return (
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    )


def _axes(matrix):
    return [[matrix.m[row][col] for row in range(3)] for col in range(3)]


def _assert_frame(axes, where):
    """Orthonormal and right handed, or SteamVR draws it skewed or mirrored."""
    x_axis, y_axis, z_axis = axes
    for axis, name in zip(axes, "XYZ"):
        assert abs(_dot(axis, axis) - 1) < TOL, f"{where}: {name} is not unit length"
    assert abs(_dot(x_axis, y_axis)) < TOL, f"{where}: axes are not square"
    assert abs(_dot(y_axis, z_axis)) < TOL, f"{where}: axes are not square"
    assert abs(_dot(x_axis, z_axis)) < TOL, f"{where}: axes are not square"
    assert all(
        abs(a - b) < TOL for a, b in zip(_cross(x_axis, y_axis), z_axis)
    ), f"{where}: left handed, it would render mirrored"


def place(head, previous, limit_deg=LIMIT):
    """Run one update and assert everything that must always hold."""
    matrix, angle = _orbit_transform(
        CENTRE, RADIUS, limit_deg, NO_TRIM, head, previous, TICK
    )
    axes = _axes(matrix)
    _, y_axis, z_axis = axes
    position = [matrix.m[row][3] for row in range(3)]

    _assert_frame(axes, "face")

    off_axis = math.hypot(position[0] - CENTRE[0], position[1] - CENTRE[1])
    assert abs(off_axis - RADIUS) < TOL, f"{off_axis:.4f}m off the centreline"
    assert abs(position[2] - CENTRE[2]) < TOL, "slid along the arm"

    outward = (
        (position[0] - CENTRE[0]) / RADIUS,
        (position[1] - CENTRE[1]) / RADIUS,
        0.0,
    )
    assert abs(_dot(z_axis, outward) - 1) < TOL, "the face is not turned outwards"
    assert abs(y_axis[2] + 1) < TOL, "the top of the face is not towards the hand"
    assert abs(math.degrees(angle)) <= limit_deg + 1e-9, "travelled past the limit"
    return angle


def main() -> int:
    named = [
        ("head above (top of wrist)", (0.0, 0.5, 0.10), 0.0),
        ("head to the right", (0.5, -0.02, 0.10), 90.0),
        ("head to the left", (-0.5, -0.02, 0.10), -90.0),
        ("head below, clamped", (0.0, -0.6, 0.10), LIMIT),
        ("head below and left", (-0.01, -0.6, 0.10), -LIMIT),
        ("head on the arm's axis", (0.0, -0.02, 0.30), 0.0),
    ]
    for label, head, expected in named:
        angle = math.degrees(place(head, None))
        assert abs(angle - expected) < 1e-6, f"{label}: {angle:.1f}, want {expected}"
        print(f"  {label:26} {angle:+7.1f} deg")

    # With a head pose it cannot use, it must stay where it was.
    held = place((0.0, -0.02, 0.30), math.radians(37.0))
    assert abs(math.degrees(held) - 37.0) < 1e-9, "drifted with nothing to aim at"

    # Every head direction, from every starting angle, stays inside the limit.
    for step in range(0, 360, 3):
        radians = math.radians(step)
        head = (CENTRE[0] + math.sin(radians), CENTRE[1] + math.cos(radians), 0.1)
        for previous in (None, -math.radians(LIMIT), 0.0, math.radians(LIMIT)):
            place(head, previous)
    print(f"  {'every angle within limit':26} {LIMIT:+7.1f} deg")

    # The antipode is where the nearest side flips from one limit to the
    # other. It has to cross over the top of the arm, not under it.
    angle, furthest = math.radians(LIMIT), 0.0
    for _ in range(120):
        angle = place((-1e-9, -1.0, 0.1), angle)
        furthest = max(furthest, abs(math.degrees(angle)))
    assert furthest <= LIMIT + 1e-9, f"swung {furthest:.1f} deg, under the arm"
    assert abs(math.degrees(angle) + LIMIT) < 0.5, "never reached the other side"
    print(f"  {'antipodal flip':26} {math.degrees(angle):+7.1f} deg, over the top")

    # Easing has to settle, and settle where it was aimed.
    angle, steps = 0.0, []
    for _ in range(120):
        angle = place((0.5, -0.02, 0.10), angle)
        steps.append(angle)
    assert all(b >= a for a, b in zip(steps, steps[1:])), "easing overshot"
    assert abs(math.degrees(angle) - 90.0) < 0.01, "easing did not settle"
    print(f"  {'easing settles':26} {math.degrees(angle):+7.1f} deg")

    # Guide markers: every one has to be square, and pointed at the head
    # from wherever it sits on the arc.
    heads = [
        (0.0, 0.6, 0.10),    # above the arm
        (0.6, -0.02, 0.10),  # off to one side
        (0.0, -0.7, 0.10),   # underneath
        (0.0, -0.02, 0.9),   # straight down the arm, the degenerate one
        (0.0, -0.02, -0.9),  # straight down the arm, the other way
    ]
    for head in heads:
        for step in range(MARKERS):
            angle = math.radians((step / (MARKERS - 1) * 2.0 - 1.0) * LIMIT)
            spot = (
                CENTRE[0] + math.sin(angle) * RADIUS,
                CENTRE[1] + math.cos(angle) * RADIUS,
                CENTRE[2],
            )
            axes = [list(col) for col in zip(*_billboard(spot, head))]
            _assert_frame(axes, f"marker {step}")

            to_head = [head[i] - spot[i] for i in range(3)]
            span = math.sqrt(sum(c * c for c in to_head))
            facing = _dot(axes[2], [c / span for c in to_head])
            assert facing > 1 - TOL, f"marker {step} is turned {facing:.4f} from the head"
    print(f"  {'markers face the head':26} {MARKERS * len(heads):+7d} cases")

    # The easing is a rate, not a step, so the same span of time has to get
    # to the same place whatever the loop rate was. This is what stops the
    # motion changing character when the loop is sped up or stutters.
    span = 0.3
    settled = []
    for tick in (1.0 / 90.0, 1.0 / 30.0, 1.0 / 20.0, 1.0 / 10.0):
        angle, elapsed = 0.0, 0.0
        while elapsed < span - 1e-9:
            angle = _orbit_transform(
                CENTRE, RADIUS, LIMIT, NO_TRIM, (0.5, -0.02, 0.10), angle, tick
            )[1]
            elapsed += tick
        settled.append(math.degrees(angle))
    spread = max(settled) - min(settled)
    assert spread < 0.01, f"easing depends on the loop rate: {settled}"
    print(f"  {'easing is rate based':26} {spread:+7.4f} deg spread")

    print("orbit geometry OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
