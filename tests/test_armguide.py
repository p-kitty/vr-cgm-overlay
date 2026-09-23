"""The tuning guides, driven by an overlay that is not one.

`cgm.vr.armguide` is only ever on while somebody is setting `offset`
and `orbit_radius_m`, so it is the one part of the VR half nobody
notices is broken until they need it -- and they need it at exactly
the moment they are already confused about where the face is. It takes
an overlay rather than making one, so what it asks of the compositor
can be asserted at a desk.

What cannot be asserted here is the only thing that matters in the
end: whether the line and the dots land on an actual arm. That needs a
headset.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from cgm.vr.armguide import MARKER_COUNT, ArmGuide


class FakeOverlay:
    """Records the overlays made, what they were shown, and where."""

    def __init__(self) -> None:
        self.created: list[tuple[str, str]] = []
        self.widths: dict[str, float] = {}
        self.files: dict[str, str] = {}
        self.visible: set[str] = set()
        self.placed: dict[str, object] = {}
        self.destroyed: list[str] = []

    def createOverlay(self, key: str, name: str) -> str:  # noqa: N802 (OpenVR's name)
        self.created.append((key, name))
        return key

    def setOverlayWidthInMeters(self, handle, width) -> None:  # noqa: N802
        self.widths[handle] = width

    def setOverlayFromFile(self, handle, path) -> None:  # noqa: N802
        self.files[handle] = path

    def showOverlay(self, handle) -> None:  # noqa: N802
        self.visible.add(handle)

    def setOverlayTransformTrackedDeviceRelative(  # noqa: N802
        self, handle, index, transform
    ) -> None:
        self.placed[handle] = (index, transform)

    def destroyOverlay(self, handle) -> None:  # noqa: N802
        self.destroyed.append(handle)


class ArmGuideTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.directory = Path(self._tmp.name)
        self.overlay = FakeOverlay()
        self.guide = ArmGuide(self.overlay, "test.key", self.directory)

    def keys(self) -> list[str]:
        return [key for key, _ in self.overlay.created]

    def test_it_makes_a_line_and_a_dot_per_marker(self):
        self.assertIn("test.key.armaxis", self.keys())
        self.assertEqual(len(self.keys()), MARKER_COUNT + 1)

    def test_every_overlay_gets_a_texture_and_is_shown(self):
        # A guide overlay that was created and never shown is a tuning
        # aid that silently does not appear, which is indistinguishable
        # from the setting not having taken.
        for key in self.keys():
            self.assertIn(key, self.overlay.files, f"{key} has no texture")
            self.assertTrue(Path(self.overlay.files[key]).is_file())
            self.assertIn(key, self.overlay.visible, f"{key} was never shown")

    def test_update_places_the_line_and_every_dot(self):
        markers = [f"marker-{i}" for i in range(MARKER_COUNT)]
        self.guide.update(3, "axis", markers)

        self.assertEqual(self.overlay.placed["test.key.armaxis"], (3, "axis"))
        self.assertEqual(self.overlay.placed["test.key.armdot0"], (3, "marker-0"))
        self.assertEqual(len(self.overlay.placed), MARKER_COUNT + 1)

    def test_closing_destroys_every_one(self):
        # SteamVR keeps the key of an overlay the process never
        # destroyed, and the next run cannot create it again.
        self.guide.close()
        self.assertCountEqual(self.overlay.destroyed, self.keys())

    def test_closing_survives_an_overlay_that_will_not_go(self):
        # Nothing here is worth failing a shutdown over, and leaving the
        # rest registered because the first refused would be.
        def refuse(handle):
            if handle == "test.key.armaxis":
                raise RuntimeError("no")
            self.overlay.destroyed.append(handle)

        self.overlay.destroyOverlay = refuse
        self.guide.close()
        self.assertIn("test.key.armdot0", self.overlay.destroyed)
        self.assertEqual(len(self.overlay.destroyed), MARKER_COUNT)


if __name__ == "__main__":
    unittest.main()
