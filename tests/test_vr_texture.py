"""Handing textures over as files, driven by an overlay that is not one.

`cgm.vr.texture` replaced `setOverlayRaw`, which SteamVR stopped
accepting after 200 calls in one process. What it does with the
overlay interface -- which file it writes, which path it names, when it
does nothing, which overlay is showing -- can be asserted at a desk.
Whether the compositor then loads the file and shows it without a
blink needs a headset, and nothing here claims otherwise.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from PIL import Image

from cgm.vr.texture import (
    TEXTURE_DIR_NAME,
    DoubleBuffer,
    TextureFiles,
    hand_over,
    texture_dir,
)


class FakeOverlay:
    """Records every file it was pointed at, and which overlays show.

    Hiding the last visible overlay is refused outright: that is the
    blink the double buffer exists to prevent, and an assertion here
    catches it on whichever call did it.
    """

    def __init__(self) -> None:
        self.loads: list[tuple[object, str, Image.Image]] = []
        self.visible: set = set()
        # What each overlay last had loaded into it.
        self.holding: dict = {}

    def setOverlayFromFile(self, handle, path: str) -> None:
        # Read now: the next draw may rewrite the file, and what matters
        # is what the compositor would have found when it was called.
        with Image.open(path) as image:
            self.loads.append((handle, path, image.copy()))
            self.holding[handle] = image.tobytes()

    def showOverlay(self, handle) -> None:
        self.visible.add(handle)

    def hideOverlay(self, handle) -> None:
        if self.visible == {handle}:
            raise AssertionError(f"hid {handle!r}, the only overlay showing")
        self.visible.discard(handle)

    def on_screen(self) -> bytes | None:
        """The pixels of the one overlay showing, None if it holds none."""
        (handle,) = self.visible
        return self.holding.get(handle)


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


    def test_forget_makes_the_same_pixels_go_again(self):
        self.texture.set(card((4, 4, 4, 255)))
        self.texture.forget()
        self.assertTrue(self.texture.set(card((4, 4, 4, 255))))


class FakeClock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


A, B = "overlay-a", "overlay-b"


class DoubleBufferTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.overlay = FakeOverlay()
        self.clock = FakeClock()
        self.face = DoubleBuffer(
            self.overlay,
            (A, B),
            Path(self._tmp.name),
            timeout_sec=1.0,
            clock=self.clock,
        )

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def back(self):
        return self.face.handles[1]

    def show(self, image: Image.Image) -> None:
        """Hand a frame over and have the compositor finish loading it."""
        self.face.set(image)
        self.face.loaded(self.back())

    def test_one_overlay_shows_from_the_start(self):
        self.assertEqual(self.overlay.visible, {A})

    def test_a_new_frame_loads_into_the_hidden_overlay(self):
        self.face.set(card((1, 0, 0, 255)))
        (handle, _, _) = self.overlay.loads[0]
        self.assertEqual(handle, B)
        self.assertEqual(self.overlay.visible, {A})

    def test_the_overlays_swap_once_the_load_is_done(self):
        first = card((1, 0, 0, 255))
        self.face.set(first)
        self.face.loaded(B)
        self.assertEqual(self.overlay.visible, {B})
        self.assertEqual(self.overlay.on_screen(), first.tobytes())

    def test_every_later_frame_swaps_the_same_way(self):
        # FakeOverlay refuses a hide that would leave nothing showing, so
        # getting through this is itself the check that no swap blinks.
        for shade in range(6):
            frame = card((shade, 50, 0, 255))
            self.show(frame)
            self.assertEqual(self.overlay.on_screen(), frame.tobytes())
            self.assertEqual(len(self.overlay.visible), 1)

    def test_word_from_the_overlay_on_show_changes_nothing(self):
        self.face.set(card((1, 0, 0, 255)))
        self.face.loaded(A)
        self.assertEqual(self.overlay.visible, {A})

    def test_the_same_pixels_are_not_handed_over_twice(self):
        self.show(card((2, 2, 2, 255)))
        self.face.set(card((2, 2, 2, 255)))
        self.assertEqual(len(self.overlay.loads), 1)

    def test_a_frame_arriving_mid_load_waits_its_turn(self):
        first, second = card((1, 0, 0, 255)), card((2, 0, 0, 255))
        self.face.set(first)
        self.face.set(second)
        self.assertEqual(len(self.overlay.loads), 1)
        self.face.loaded(B)
        # The first is up, and the second is loading behind it.
        self.assertEqual(self.overlay.on_screen(), first.tobytes())
        self.assertEqual(self.overlay.loads[-1][0], A)
        self.face.loaded(A)
        self.assertEqual(self.overlay.on_screen(), second.tobytes())

    def test_only_the_newest_waiting_frame_is_kept(self):
        self.face.set(card((1, 0, 0, 255)))
        self.face.set(card((2, 0, 0, 255)))
        newest = card((3, 0, 0, 255))
        self.face.set(newest)
        self.face.loaded(B)
        self.face.loaded(A)
        self.assertEqual(len(self.overlay.loads), 2)
        self.assertEqual(self.overlay.on_screen(), newest.tobytes())

    def test_a_frame_the_hidden_overlay_already_holds_swaps_at_once(self):
        # X, then Y, then X again: the overlay behind still holds X.
        x, y = card((10, 0, 0, 255)), card((20, 0, 0, 255))
        self.show(x)
        self.show(y)
        loads = len(self.overlay.loads)
        self.face.set(x)
        self.assertEqual(len(self.overlay.loads), loads)
        self.assertEqual(self.overlay.on_screen(), x.tobytes())

    def test_a_failed_load_keeps_the_face_that_is_up(self):
        first = card((1, 0, 0, 255))
        self.show(first)
        self.face.set(card((2, 0, 0, 255)))
        with self.assertLogs("cgm.vr.texture", level="WARNING"):
            self.face.failed(self.back())
        self.assertEqual(self.overlay.on_screen(), first.tobytes())

    def test_a_failed_frame_is_tried_again_when_it_comes_again(self):
        self.face.set(card((2, 0, 0, 255)))
        with self.assertLogs("cgm.vr.texture", level="WARNING"):
            self.face.failed(B)
        self.face.set(card((2, 0, 0, 255)))
        self.assertEqual(len(self.overlay.loads), 2)

    def test_a_failed_load_moves_on_to_the_frame_that_waited(self):
        self.face.set(card((1, 0, 0, 255)))
        waiting = card((2, 0, 0, 255))
        self.face.set(waiting)
        with self.assertLogs("cgm.vr.texture", level="WARNING"):
            self.face.failed(B)
        self.face.loaded(B)
        self.assertEqual(self.overlay.on_screen(), waiting.tobytes())

    def test_an_unanswered_load_is_shown_after_the_timeout(self):
        frame = card((1, 0, 0, 255))
        self.face.set(frame)
        self.clock.now += 0.5
        self.face.poll()
        self.assertEqual(self.overlay.visible, {A})
        self.clock.now += 0.6
        with self.assertLogs("cgm.vr.texture", level="WARNING"):
            self.face.poll()
        self.assertEqual(self.overlay.on_screen(), frame.tobytes())

    def test_the_timeout_is_only_complained_about_once(self):
        with self.assertLogs("cgm.vr.texture", level="WARNING") as logs:
            for shade in range(3):
                self.face.set(card((shade, 0, 0, 255)))
                self.clock.now += 2.0
                self.face.poll()
        self.assertEqual(len(logs.records), 1)

    def test_poll_with_nothing_loading_does_nothing(self):
        self.clock.now += 10.0
        self.face.poll()
        self.assertEqual(self.overlay.visible, {A})
        self.assertEqual(self.overlay.loads, [])

    def test_each_overlay_is_never_handed_the_path_it_holds(self):
        for shade in range(8):
            self.show(card((shade, 7, 7, 255)))
        by_overlay: dict = {}
        for handle, path, _ in self.overlay.loads:
            by_overlay.setdefault(handle, []).append(path)
        self.assertEqual(set(by_overlay), {A, B})
        for paths in by_overlay.values():
            for before, after in zip(paths, paths[1:]):
                self.assertNotEqual(before, after)


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
