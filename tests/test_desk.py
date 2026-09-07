"""The desktop window's parts that do not need a window.

Tk is not started here. Creating a root would open a window on whoever
runs the suite, and there is nothing in the widget wiring worth that --
`set_image` puts a photo on a label and Tk either does that or does not.

What is worth asserting is the part with arithmetic and decisions in it:
flattening the face's translucency onto something opaque, resampling it
to the asked-for size, and what the title bar claims. The flattening is
where this frontend differs from the overlay in what it shows, because
the compositor has a game behind the card and a window has nothing at
all -- and the square corners it asks the face for are the same
difference seen from the other side.
"""

from __future__ import annotations

import unittest

from cgm.desk.window import BACKDROP, compose
from cgm.face.renderer import HEIGHT, WIDTH, Theme, WatchFaceRenderer
from cgm.main import _window_title


class FakeReading:
    """Only what a title needs: the value, and how it is spelled."""

    def __init__(self, mgdl: float) -> None:
        self.value_mgdl = mgdl

    def display_value(self, unit: str) -> str:
        if unit == "mmol":
            return f"{self.value_mgdl / 18.0:.1f}"
        return f"{self.value_mgdl:.0f}"


class Compose(unittest.TestCase):
    def setUp(self):
        # `rounded=False` is what `cgm.main.run` builds for the window, so
        # this is the image the window really composites.
        self.face = WatchFaceRenderer(rounded=False).render_message("CONNECTING")

    def test_the_result_is_opaque(self):
        # A window has nothing behind it. Handing Tk an image with an
        # alpha channel leaves it to guess what shows through, and what
        # it guesses is not the backdrop chosen here.
        self.assertEqual(compose(self.face, 1.0).mode, "RGB")

    def test_the_card_is_composited_rather_than_flattened(self):
        # The card itself is translucent -- color_bg is (14, 16, 22,
        # 225) -- so the pixel that proves the alpha was used is a card
        # pixel: it must be color_bg *over* the backdrop, and neither of
        # the two things it would be if the alpha were dropped or the
        # card lost.
        # Bare card: inside the frame the message card outlines itself
        # with, and clear of the centred text.
        spot = (16, 16)
        card = self.face.getpixel(spot)
        self.assertEqual(card, Theme().color_bg)
        flat = compose(self.face, 1.0).getpixel(spot)
        self.assertNotEqual(flat, BACKDROP)
        self.assertNotEqual(flat, card[:3])
        for shown, over, under in zip(flat, card[:3], BACKDROP):
            self.assertTrue(min(over, under) <= shown <= max(over, under))

    def test_the_window_card_has_no_transparent_corner(self):
        # With nothing behind the window, an arc is not a shape: it is a
        # wedge of backdrop bitten out of the corner of the picture.
        self.assertEqual(self.face.getpixel((0, 0)), Theme().color_bg)

    def test_the_vr_card_still_has_one(self):
        # The corners are the window's to square off, not the face's to
        # stop drawing. The compositor shows the game through these.
        rounded = WatchFaceRenderer().render_message("CONNECTING")
        self.assertEqual(rounded.getpixel((0, 0))[3], 0)

    def test_native_scale_keeps_the_size(self):
        self.assertEqual(compose(self.face, 1.0).size, (WIDTH, HEIGHT))

    def test_scaling_changes_the_size_in_proportion(self):
        for scale in (0.25, 0.5, 1.5, 4.0):
            with self.subTest(scale=scale):
                out = compose(self.face, scale)
                self.assertEqual(
                    out.size, (round(WIDTH * scale), round(HEIGHT * scale))
                )

    def test_the_aspect_ratio_is_kept(self):
        out = compose(self.face, 0.75)
        self.assertAlmostEqual(out.width / out.height, WIDTH / HEIGHT, places=2)

    def test_a_scaled_face_is_still_opaque(self):
        self.assertEqual(compose(self.face, 0.5).mode, "RGB")

    def test_it_never_scales_away_to_nothing(self):
        # config.toml cannot ask for this -- window.scale has a floor --
        # but a zero-sized image is a Pillow error rather than a small
        # window, so the clamp stays.
        out = compose(self.face, 0.0001)
        self.assertGreaterEqual(min(out.size), 1)


class Title(unittest.TestCase):
    """The taskbar entry, which is all there is to read when it is covered."""

    def test_a_reading_is_the_number_and_its_unit(self):
        self.assertIn("112 mg/dL", _window_title(FakeReading(112), None, "mgdl"))

    def test_the_unit_follows_the_display_setting(self):
        self.assertIn("6.2 mmol/L", _window_title(FakeReading(112), None, "mmol"))

    def test_no_arrow_in_the_title(self):
        # The face draws a fitted arrow and the API sends a bucketed
        # one; they disagree by design. A title bar cannot say which is
        # which, so it says neither.
        title = _window_title(FakeReading(112), None, "mgdl")
        for arrow in "\u2193\u2198\u2192\u2197\u2191":
            with self.subTest(arrow=arrow):
                self.assertNotIn(arrow, title)

    def test_no_reading_shows_the_error(self):
        self.assertIn("NO CONNECTION", _window_title(None, "NO CONNECTION", "mgdl"))

    def test_no_reading_and_no_error_still_says_something(self):
        self.assertIn("waiting", _window_title(None, None, "mgdl"))

    def test_the_app_is_always_named(self):
        # Whatever the state, the taskbar has to say which window it is.
        for reading, error in (
            (FakeReading(112), None),
            (None, "AUTH ERROR"),
            (None, None),
        ):
            with self.subTest(error=error):
                self.assertIn("vr-cgm-overlay", _window_title(reading, error, "mgdl"))


class Layers(unittest.TestCase):
    def test_the_window_never_imports_vr(self):
        # The point of the second frontend is that it needs neither
        # openvr nor a headset. An import of either here would make
        # `pip install -e .` no longer enough to run it.
        #
        # Read as imports rather than as text: the module docstring says
        # "cgm.vr.overlay.WristOverlay" on purpose, to point at the
        # frontend this one mirrors, and prose is not a dependency.
        import ast
        from pathlib import Path

        import cgm.desk.window as window_mod

        tree = ast.parse(
            Path(window_mod.__file__).read_text(encoding="utf-8"),
            filename=window_mod.__file__,
        )
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)

        for name in sorted(imported):
            with self.subTest(name):
                self.assertNotEqual(name.split(".")[:2], ["cgm", "vr"])
                self.assertNotEqual(name.split(".")[0], "openvr")


if __name__ == "__main__":
    unittest.main()
