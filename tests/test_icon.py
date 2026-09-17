"""The app icon: every size draws, and the .ico carries each one as drawn.

Whether it looks like anything is a question for a pair of eyes. What
can be asserted is the part a build would otherwise find out late: that
the smallest size still has the green dot in it, and that the .ico holds
each size's own drawing rather than the largest one shrunk.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from PIL import Image, ImageChops

from cgm.face.icon import ICO_SIZES, draw_icon, save_ico
from cgm.face.renderer import Theme


class DrawIconTest(unittest.TestCase):
    def test_every_size_is_a_square_rgba_image(self) -> None:
        for size in ICO_SIZES:
            with self.subTest(size=size):
                image = draw_icon(size)
                self.assertEqual(image.mode, "RGBA")
                self.assertEqual(image.size, (size, size))

    def test_corners_are_transparent_and_middle_is_not(self) -> None:
        image = draw_icon(64)
        self.assertEqual(image.getpixel((0, 0))[3], 0)
        self.assertEqual(image.getpixel((32, 32))[3], 255)

    def test_the_dot_survives_at_sixteen_pixels(self) -> None:
        green = Theme().color_in_range
        pixels = draw_icon(16).get_flattened_data()
        # Close to the in-range colour somewhere, not just a green tint.
        self.assertTrue(
            any(sum(abs(p - g) for p, g in zip(px[:3], green)) < 60 for px in pixels)
        )

    def test_rejects_an_empty_size(self) -> None:
        with self.assertRaises(ValueError):
            draw_icon(0)


class SaveIcoTest(unittest.TestCase):
    def test_each_size_is_its_own_drawing(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "icon.ico"
            save_ico(path)
            with Image.open(path) as ico:
                self.assertEqual(ico.info["sizes"], {(s, s) for s in ICO_SIZES})
                for size in ICO_SIZES:
                    with self.subTest(size=size):
                        ico.size = (size, size)
                        ico.load()
                        frame = ico.convert("RGBA")
                        diff = ImageChops.difference(frame, draw_icon(size))
                        self.assertIsNone(diff.getbbox())


if __name__ == "__main__":
    unittest.main()
