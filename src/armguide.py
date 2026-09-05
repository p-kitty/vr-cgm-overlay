"""Two throwaway overlays that draw the arm orbit mode is aiming at.

Orbit mode models the forearm as a line and puts the face on a circle
around it. Both of those are invisible, which is what makes `offset` X and
Y hard to set: you cannot see what you are moving, only guess from how the
face ends up behaving. So draw them.

  - a **line** down the modelled centreline. Set `offset` X and Y until it
    runs through the middle of your arm and stays there as you turn your
    hand. That is the whole of the X and Y adjustment, and it becomes a
    thing you look at rather than a number you guess.
  - a **ring** on the circle the face travels, at the point along the arm
    the face sits at. `offset` Z slides it up and down your arm and
    `orbit_radius_m` grows it; set it just clear of your sleeve.

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

AXIS_COLOR = (0, 220, 255, 210)   # cyan: the centreline
RING_COLOR = (255, 80, 220, 210)  # magenta: the circle the face travels

_AXIS_PX_W = 16
_AXIS_PX_H = round(_AXIS_PX_W * AXIS_LENGTH_M / AXIS_WIDTH_M)
_RING_PX = 512
_RING_STROKE_PX = 10


def axis_texture() -> Image.Image:
    """A plain bar. Deliberately symmetric, so it cannot look flipped."""
    image = Image.new("RGBA", (_AXIS_PX_W, _AXIS_PX_H), AXIS_COLOR)
    return image


def ring_texture() -> Image.Image:
    """A circle that fills the texture, so its world radius is half the width."""
    image = Image.new("RGBA", (_RING_PX, _RING_PX), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    inset = _RING_STROKE_PX / 2
    draw.ellipse(
        (inset, inset, _RING_PX - 1 - inset, _RING_PX - 1 - inset),
        outline=RING_COLOR,
        width=_RING_STROKE_PX,
    )
    return image


class ArmGuide:
    """The two guide overlays, created on demand and destroyed with close().

    Textures are uploaded once: neither drawing depends on any setting, so
    only the transforms and the ring's width change while tuning.
    """

    def __init__(self, overlay, key_prefix: str) -> None:
        self._overlay = overlay
        self._axis = overlay.createOverlay(f"{key_prefix}.armaxis", "Arm axis guide")
        self._ring = overlay.createOverlay(f"{key_prefix}.armring", "Orbit ring guide")

        overlay.setOverlayWidthInMeters(self._axis, AXIS_WIDTH_M)
        # Kept alive for the life of the overlay: the compositor reads these
        # buffers after the call returns, as it does for the watch face.
        self._axis_buffer = self._upload(self._axis, axis_texture())
        self._ring_buffer = self._upload(self._ring, ring_texture())

        overlay.showOverlay(self._axis)
        overlay.showOverlay(self._ring)

        self._ring_radius: float | None = None
        log.info("arm guide on: cyan line is the modelled arm, magenta ring the orbit")

    def _upload(self, handle, image: Image.Image):
        data = image.tobytes()
        buffer = (ctypes.c_char * len(data))()
        buffer.raw = data
        self._overlay.setOverlayRaw(handle, buffer, image.width, image.height, 4)
        return buffer

    def update(self, index: int, axis_transform, ring_transform, radius: float) -> None:
        """Point both guides at the arm the overlay is currently modelling."""
        if radius != self._ring_radius:
            # The circle fills its texture, so the overlay has to be a
            # diameter wide for the drawn radius to be the real one.
            self._overlay.setOverlayWidthInMeters(self._ring, radius * 2.0)
            self._ring_radius = radius
        self._overlay.setOverlayTransformTrackedDeviceRelative(
            self._axis, index, axis_transform
        )
        self._overlay.setOverlayTransformTrackedDeviceRelative(
            self._ring, index, ring_transform
        )

    def close(self) -> None:
        for handle in (self._axis, self._ring):
            try:
                self._overlay.destroyOverlay(handle)
            except Exception as exc:  # nothing here is worth failing a shutdown
                log.debug("could not destroy a guide overlay: %s", exc)
        log.info("arm guide off")
