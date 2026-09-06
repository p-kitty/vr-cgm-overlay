"""Two throwaway overlays that draw the arm orbit mode is aiming at.

Orbit mode models the forearm as a line and puts the face on a circle
around it. Both of those are invisible, which is what makes `offset` X and
Y hard to set: you cannot see what you are moving, only guess from how the
face ends up behaving. So draw them.

  - a **line** down the modelled centreline. Set `offset` X and Y until it
    runs through the middle of your arm and stays there as you turn your
    hand. That is the whole of the X and Y adjustment, and it becomes a
    thing you look at rather than a number you guess.
  - **dots** on the circle the face travels, at the point along the arm the
    face sits at. `offset` Z slides them up and down your arm and
    `orbit_radius_m` grows the circle; set it just clear of your sleeve.
    The wide dot is the top of the wrist, and the arc the dots span is
    exactly how far `orbit_limit_deg` lets the face travel.

Dots rather than a drawn circle because an overlay is a flat quad, and a
circle across the arm is edge-on and invisible from the side, which is
where a wrist is usually looked at. Each dot is turned to face the head
instead, so the arc reads from anywhere.

This is a tuning aid, not a feature. It is off unless `display.arm_guide`
is on, and it is meant to be deleted once the placement is settled.
"""

from __future__ import annotations

import ctypes
import logging

from PIL import Image, ImageDraw

log = logging.getLogger(__name__)

# The line spans this much of the arm, centred on the face. Long enough to
# reach past the elbow, so it is obvious when it leaves the arm.
AXIS_LENGTH_M = 0.40
AXIS_WIDTH_M = 0.006

AXIS_COLOR = (0, 220, 255, 210)     # cyan: the centreline
MARKER_COLOR = (255, 80, 220, 225)  # magenta: the circle the face travels

# Dots spread over the arc the face can travel. Every overlay in the system
# shares a budget of k_unMaxOverlayCount, so this stays small enough to sit
# alongside whatever else the user runs; nine still reads as an arc.
MARKER_COUNT = 9
MARKER_WIDTH_M = 0.010
# The dot at the top of the wrist, drawn wider so zero degrees is findable.
TOP_MARKER_SCALE = 1.8

_AXIS_PX_W = 16
_AXIS_PX_H = round(_AXIS_PX_W * AXIS_LENGTH_M / AXIS_WIDTH_M)
_MARKER_PX = 64


def axis_texture() -> Image.Image:
    """A plain bar. Deliberately symmetric, so it cannot look flipped."""
    image = Image.new("RGBA", (_AXIS_PX_W, _AXIS_PX_H), AXIS_COLOR)
    return image


def marker_texture() -> Image.Image:
    """One filled dot, shared by every marker; only their widths differ."""
    image = Image.new("RGBA", (_MARKER_PX, _MARKER_PX), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.ellipse((0, 0, _MARKER_PX - 1, _MARKER_PX - 1), fill=MARKER_COLOR)
    return image


class ArmGuide:
    """The two guide overlays, created on demand and destroyed with close().

    Textures are uploaded once: neither drawing depends on any setting, so
    only the transforms and the ring's width change while tuning.
    """

    def __init__(self, overlay, key_prefix: str) -> None:
        self._overlay = overlay
        self._axis = overlay.createOverlay(f"{key_prefix}.armaxis", "Arm axis guide")
        self._markers = [
            overlay.createOverlay(f"{key_prefix}.armdot{i}", f"Orbit marker {i}")
            for i in range(MARKER_COUNT)
        ]

        # Kept alive for the life of the overlays: the compositor reads these
        # buffers after the call returns, as it does for the watch face.
        overlay.setOverlayWidthInMeters(self._axis, AXIS_WIDTH_M)
        self._axis_buffer = self._upload(self._axis, axis_texture())
        overlay.showOverlay(self._axis)

        dot = marker_texture()
        self._marker_buffers = []
        middle = MARKER_COUNT // 2
        for i, handle in enumerate(self._markers):
            scale = TOP_MARKER_SCALE if i == middle else 1.0
            overlay.setOverlayWidthInMeters(handle, MARKER_WIDTH_M * scale)
            self._marker_buffers.append(self._upload(handle, dot))
            overlay.showOverlay(handle)

        log.info("arm guide on: cyan line is the modelled arm, magenta dots the orbit")

    def _upload(self, handle, image: Image.Image):
        data = image.tobytes()
        buffer = (ctypes.c_char * len(data))()
        buffer.raw = data
        self._overlay.setOverlayRaw(handle, buffer, image.width, image.height, 4)
        return buffer

    def update(self, index: int, axis_transform, marker_transforms) -> None:
        """Point the guides at the arm the overlay is currently modelling.

        `marker_transforms` has one entry per marker, in order around the
        arc, already turned to face the head.
        """
        self._overlay.setOverlayTransformTrackedDeviceRelative(
            self._axis, index, axis_transform
        )
        for handle, transform in zip(self._markers, marker_transforms):
            self._overlay.setOverlayTransformTrackedDeviceRelative(
                handle, index, transform
            )

    def close(self) -> None:
        for handle in [self._axis, *self._markers]:
            try:
                self._overlay.destroyOverlay(handle)
            except Exception as exc:  # nothing here is worth failing a shutdown
                log.debug("could not destroy a guide overlay: %s", exc)
        log.info("arm guide off")
