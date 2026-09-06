"""Check the status palette is separable without normal colour vision.

The face is meant to read as colour before it reads as digits. That fast
path only works if the status colours stay apart for a viewer with a
colour vision deficiency too -- roughly 1 in 20 men has one, and the
red-green types collapse a warm palette onto a single olive band.

Someone with normal colour vision cannot judge that by eye, so it is
measured here instead: every pair is simulated under protanopia and
deuteranopia and its separation asserted.

Two numbers, because they answer different questions:

  - **dE**, the CIE76 distance between the simulated colours, is the
    honest measure of "can these be told apart". Dichromacy removes the
    red-green axis but leaves luminance and blue-yellow, and dE counts
    what is left. It is the floor every pair must clear.
  - **luminance contrast** is the harsher measure, and the one that still
    works if the surviving axis is degraded rather than absent, as in the
    anomalous trichromacies. It is only required of the pairs where
    colour is working alone -- where cgm.face.renderer gives both
    statuses the same marker edge, so position cannot break the tie.

Run it after touching Theme:

    python tools/check_palette.py
"""

from __future__ import annotations

import sys
from itertools import combinations

from cgm.face.renderer import STATUS_MARKERS, Theme

# Viénot, Brettel & Mollon 1999. A dichromat's response is modelled by
# projecting LMS onto the plane the missing cone leaves behind, which is
# what reduces protanopia and deuteranopia to one substitution each.
RGB_TO_LMS = (
    (17.8824, 43.5161, 4.11935),
    (3.45565, 27.1554, 3.86714),
    (0.0299566, 0.184309, 1.46709),
)
LMS_TO_RGB = (
    (0.080944, -0.130504, 0.116721),
    (-0.010248, 0.054019, -0.113615),
    (-0.000365, -0.004122, 0.693513),
)

VISION_TYPES = ("normal", "protanopia", "deuteranopia")

# A dE around 2 is the just-noticeable difference for two samples side by
# side. This is a glance at arm's length in a headset, at whatever the
# compositor's resampling leaves of the colour, so the bar is set an order
# of magnitude above that.
MIN_DELTA_E = 25.0

# Statuses sharing a marker edge cannot be separated by position, so their
# colours have to carry it alone -- and then survive being dimmed by a
# bright scene behind the overlay, which erodes chroma faster than
# luminance. Those pairs get a luminance floor as well.
MIN_LUMINANCE_CONTRAST = 2.00

# "Well above range" and "below range" call for opposite responses, so the
# pair is called out by name even though the markers separate it.
CRITICAL_PAIRS = {frozenset(("very high", "low"))}

# Whether a pair below MIN_DELTA_E is a failure or a warning depends on
# whether anything else is carrying it:
#
#   - both statuses on the same marker edge -> colour is alone -> FAIL
#   - different edges -> position carries the distinction -> WARN
#
# The warnings are not decoration. in range and low are green and red to
# match the official Libre app, which puts them on the axis red-green
# deficiency removes, and that pair is the closest on the face. It is
# accepted, not fixed: the markers are what make it safe, so the warning
# stays visible rather than being tuned away or silenced.

# Text and markers are drawn on the card, so each has to be legible there
# too. WCAG's floor for large text.
MIN_BACKGROUND_CONTRAST = 4.50


def _matmul(matrix, vector):
    return tuple(sum(row[i] * vector[i] for i in range(3)) for row in matrix)


def _to_linear(channel: float) -> float:
    c = channel / 255.0
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def _to_srgb(channel: float) -> int:
    c = min(1.0, max(0.0, channel))
    c = c * 12.92 if c <= 0.0031308 else 1.055 * c ** (1 / 2.4) - 0.055
    return round(c * 255)


def simulate(rgb: tuple[int, int, int], vision: str) -> tuple[int, int, int]:
    """Render a colour as the given vision type sees it."""
    if vision == "normal":
        return tuple(rgb)
    long_, medium, short = _matmul(RGB_TO_LMS, tuple(_to_linear(c) for c in rgb))
    if vision == "protanopia":
        long_ = 2.02344 * medium - 2.52581 * short
    elif vision == "deuteranopia":
        medium = 0.494207 * long_ + 1.24827 * short
    else:
        raise ValueError(f"unknown vision type: {vision!r}")
    return tuple(_to_srgb(c) for c in _matmul(LMS_TO_RGB, (long_, medium, short)))


def luminance(rgb: tuple[int, int, int]) -> float:
    red, green, blue = (_to_linear(c) for c in rgb)
    return 0.2126 * red + 0.7152 * green + 0.0722 * blue


def contrast(a: tuple[int, int, int], b: tuple[int, int, int]) -> float:
    lighter, darker = sorted((luminance(a), luminance(b)), reverse=True)
    return (lighter + 0.05) / (darker + 0.05)


def _to_lab(rgb: tuple[int, int, int]) -> tuple[float, float, float]:
    """sRGB to CIELAB under D65."""
    red, green, blue = (_to_linear(c) for c in rgb)
    x = 0.4124 * red + 0.3576 * green + 0.1805 * blue
    y = 0.2126 * red + 0.7152 * green + 0.0722 * blue
    z = 0.0193 * red + 0.1192 * green + 0.9505 * blue

    def f(t: float) -> float:
        return t ** (1 / 3) if t > 0.008856 else 7.787 * t + 16 / 116

    fx, fy, fz = f(x / 0.95047), f(y / 1.0), f(z / 1.08883)
    return (116 * fy - 16, 500 * (fx - fy), 200 * (fy - fz))


def delta_e(a: tuple[int, int, int], b: tuple[int, int, int]) -> float:
    """CIE76 colour difference."""
    lab_a, lab_b = _to_lab(a), _to_lab(b)
    return sum((lab_a[i] - lab_b[i]) ** 2 for i in range(3)) ** 0.5


def _shares_marker_edge(theme_status_a: str, theme_status_b: str) -> bool:
    """Do these two statuses light the same edge of the card?"""
    edge_of = {
        marker_status: marker.removesuffix("_heavy")
        for marker_status, marker in STATUS_MARKERS.items()
    }
    return edge_of[theme_status_a] == edge_of[theme_status_b]


def main() -> int:
    theme = Theme()
    # Display name -> the STATUS_MARKERS key it corresponds to.
    palette = {
        "in range": ("in_range", theme.color_in_range),
        "high": ("high", theme.color_high),
        "very high": ("very_high", theme.color_very_high),
        "low": ("low", theme.color_low),
        "stale": ("stale", theme.color_stale),
    }

    print("Simulated palette")
    print(f"  {'status':10}  {'marker':10}  {'normal':16} {'protanopia':16} deuteranopia")
    for name, (key, rgb) in palette.items():
        cells = " ".join(f"{str(simulate(rgb, v)):16}" for v in VISION_TYPES)
        print(f"  {name:10}  {STATUS_MARKERS[key]:10}  {cells}")

    failures: list[str] = []
    warnings: list[str] = []

    print("\nSeparation per pair (dE, then luminance contrast)")
    header = "  ".join(f"{v[:6]:>6}" for v in VISION_TYPES)
    print(f"  {'pair':24}  {header}   floor   {header}  floor  carried by")
    for a, b in combinations(palette, 2):
        key_a, rgb_a = palette[a]
        key_b, rgb_b = palette[b]

        des = {v: delta_e(simulate(rgb_a, v), simulate(rgb_b, v)) for v in VISION_TYPES}
        lums = {v: contrast(simulate(rgb_a, v), simulate(rgb_b, v)) for v in VISION_TYPES}

        # Colour is alone only when both statuses light the same edge.
        colour_alone = _shares_marker_edge(key_a, key_b)
        edges = f"{STATUS_MARKERS[key_a]}/{STATUS_MARKERS[key_b]}"
        carried_by = "colour only" if colour_alone else edges

        de_cells = "  ".join(f"{des[v]:6.1f}" for v in VISION_TYPES)
        lum_cells = "  ".join(f"{lums[v]:6.2f}" for v in VISION_TYPES)
        lum_floor_text = (
            f"{MIN_LUMINANCE_CONTRAST:5.2f}" if colour_alone else "    -"
        )

        marks = ""
        short = min(des.values()) < MIN_DELTA_E
        if short and colour_alone:
            failures.append(
                f"{a} vs {b}: dE {min(des.values()):.1f} < {MIN_DELTA_E:.1f} "
                "and nothing else separates them"
            )
            marks = "  <- FAIL"
        elif short:
            note = " (the pair meaning opposite things)" if (
                frozenset((a, b)) in CRITICAL_PAIRS
            ) else ""
            warnings.append(
                f"{a} vs {b}: dE {min(des.values()):.1f} < {MIN_DELTA_E:.1f}; "
                f"resting on the {edges} markers{note}"
            )
            marks = "  <- position"
        if colour_alone and min(lums.values()) < MIN_LUMINANCE_CONTRAST:
            failures.append(
                f"{a} vs {b}: luminance {min(lums.values()):.2f} "
                f"< {MIN_LUMINANCE_CONTRAST:.2f} and nothing else separates them"
            )
            marks = "  <- FAIL"

        print(
            f"  {a + ' vs ' + b:24}  {de_cells}  {MIN_DELTA_E:6.1f}   "
            f"{lum_cells}  {lum_floor_text}  {carried_by}{marks}"
        )

    print("\nLuminance contrast against the card background")
    bg = theme.color_bg[:3]
    for name, (_key, rgb) in palette.items():
        lums = {v: contrast(simulate(rgb, v), simulate(bg, v)) for v in VISION_TYPES}
        cells = "  ".join(f"{lums[v]:6.2f}" for v in VISION_TYPES)
        worst = min(lums.values())
        suffix = "" if worst >= MIN_BACKGROUND_CONTRAST else "  <- FAIL"
        print(f"  {name:24}  {cells}  {MIN_BACKGROUND_CONTRAST:5.2f}{suffix}")
        if worst < MIN_BACKGROUND_CONTRAST:
            failures.append(f"{name} vs background: {worst:.2f} < {MIN_BACKGROUND_CONTRAST:.2f}")

    if warnings:
        print("\nResting on the markers, not on colour:")
        for line in warnings:
            print(f"  WARN: {line}")
        print(
            "\n  These are accepted, not overlooked. in range and low are the\n"
            "  Libre app's green and red so the two screens agree, which puts\n"
            "  them on the axis red-green deficiency removes. Deleting a\n"
            "  marker, or giving two of these statuses the same edge, turns\n"
            "  each of these lines into a failure."
        )

    if failures:
        print()
        for line in failures:
            print(f"FAIL: {line}")
        return 1

    print("\nOK: nothing is separated by colour alone that colour cannot carry.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
