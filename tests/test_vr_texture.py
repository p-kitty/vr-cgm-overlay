"""Handing textures over as files, driven by an overlay that is not one.

`cgm.vr.texture` replaced `setOverlayRaw`, which SteamVR stopped
accepting after 200 calls in one process. What it does with the
overlay interface -- which file it writes, which path it names, when it
does nothing -- can be asserted at a desk. Whether the compositor then
loads the file and shows it needs a headset, and nothing here claims
otherwise.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from PIL import Image

from cgm.vr.texture import TEXTURE_DIR_NAME, TextureFiles, hand_over, texture_dir


class FakeOverlay:
    """Records every file it was pointed at, and what was in it then."""

    def __init__(self) -> None:
        self.loads: list[tuple[object, str, Image.Image]] = []

    def setOverlayFromFile(self, handle, path: str) -> None:
        # Read now: the next draw may rewrite the file, and what matters
        # is what the compositor would have found when it was called.
        with Image.open(path) as image:
            self.loads.append((handle, path, image.copy()))


def card(colour: tuple[int, int, int, int], size=(512, 440)) -> Image.Image:
    return Image.new("RGBA", size, colour)


class TextureFilesTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.directory = Path(self._tmp.name)
        self.overlay = FakeOverlay()
        self.texture = TextureFiles(self.overlay, 7, self.directory, "face")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_the_file_holds_the_image_it_was_given(self):
        image = card((10, 20, 30, 128))
        image.putpixel((0, 0), (255, 0, 0, 255))
        self.assertTrue(self.texture.set(image))

        handle, path, loaded = self.overlay.loads[0]
        self.assertEqual(handle, 7)
        self.assertEqual(loaded.mode, "RGBA")
        self.assertEqual(loaded.tobytes(), image.tobytes())

    def test_the_path_is_absolute(self):
        # The compositor is another process with its own working directory.
        self.texture.set(card((0, 0, 0, 255)))
        self.assertTrue(Path(self.overlay.loads[0][1]).is_absolute())

    def test_the_same_pixels_are_not_handed_over_twice(self):
        self.assertTrue(self.texture.set(card((1, 2, 3, 255))))
        self.assertFalse(self.texture.set(card((1, 2, 3, 255))))
        self.assertEqual(len(self.overlay.loads), 1)

    def test_a_new_size_is_a_change_even_with_the_same_bytes(self):
        # 2x1 and 1x2 of one colour are the same bytes and a different image.
        self.texture.set(card((5, 5, 5, 255), size=(2, 1)))
        self.assertTrue(self.texture.set(card((5, 5, 5, 255), size=(1, 2))))

    def test_two_calls_in_a_row_never_name_the_same_file(self):
        for shade in range(5):
            self.texture.set(card((shade, 0, 0, 255)))
        paths = [path for _, path, _ in self.overlay.loads]
        self.assertEqual(len(set(paths)), 2)
        for before, after in zip(paths, paths[1:]):
            self.assertNotEqual(before, after)

    def test_the_file_just_handed_over_is_left_alone_by_the_next(self):
        # The compositor loads asynchronously. Whatever the second draw
        # writes, the file named by the first must still hold the first.
        first = card((200, 0, 0, 255))
        self.texture.set(first)
        first_path = self.texture.last_path
        self.texture.set(card((0, 200, 0, 255)))
        with Image.open(first_path) as on_disk:
            self.assertEqual(on_disk.tobytes(), first.tobytes())

    def test_an_unchanged_frame_leaves_the_last_path_alone(self):
        self.texture.set(card((9, 9, 9, 255)))
        path = self.texture.last_path
        self.texture.set(card((9, 9, 9, 255)))
        self.assertEqual(self.texture.last_path, path)


class HandOverTest(unittest.TestCase):
    def test_writes_then_names_the_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            overlay = FakeOverlay()
            image = card((0, 0, 255, 255), size=(16, 16))
            hand_over(overlay, 3, image, Path(tmp) / "guide.png")
            handle, path, loaded = overlay.loads[0]
            self.assertEqual(handle, 3)
            self.assertEqual(Path(path), (Path(tmp) / "guide.png").resolve())
            self.assertEqual(loaded.tobytes(), image.tobytes())


class TextureDirTest(unittest.TestCase):
    def test_is_created_under_the_root_it_is_given(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = texture_dir(Path(tmp))
            self.assertEqual(path, Path(tmp) / TEXTURE_DIR_NAME)
            self.assertTrue(path.is_dir())
            # And again, when it is already there.
            self.assertEqual(texture_dir(Path(tmp)), path)

    def test_a_path_outside_ascii_is_warned_about(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertLogs("cgm.vr.texture", level="WARNING"):
                texture_dir(Path(tmp) / "デスクトップ")

    def test_an_ascii_path_is_not(self):
        with tempfile.TemporaryDirectory() as tmp:
            if not tmp.isascii():
                self.skipTest("this machine's temp directory is not ASCII")
            with self.assertNoLogs("cgm.vr.texture", level="WARNING"):
                texture_dir(Path(tmp))


if __name__ == "__main__":
    unittest.main()
