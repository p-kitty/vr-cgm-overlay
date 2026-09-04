"""SteamVR overlay creation and controller tracking.

OpenVR composites overlays without touching the game process, so this
shows up over any SteamVR title with no mods.

A controller's device index changes when it sleeps, wakes or re-pairs, so
the overlay is re-attached when the index changes rather than every frame.
"""

from __future__ import annotations

import ctypes
import logging
import math
import time

import openvr
from PIL import Image

log = logging.getLogger(__name__)

OVERLAY_KEY = "jp.local.vrcgm.wristglucose"
OVERLAY_NAME = "VR CGM Wrist Glucose"

# How often to re-check for SteamVR while waiting for it to come up.
CONNECT_RETRY_SEC = 3.0


def _connect(retry_sec: float = CONNECT_RETRY_SEC):
    """Block until SteamVR is up, then return the IVRSystem.

    A background app cannot start SteamVR, so openvr.init fails outright
    when vrserver is not running. This process is meant to sit resident,
    and requiring it to be started after SteamVR every time is a poor
    trade for the few lines it takes to wait.

    Only "no server" is waited on. Every other init error is a real fault
    and is raised straight away.
    """
    waiting = False
    while True:
        try:
            system = openvr.init(openvr.VRApplication_Background)
        except openvr.error_code.InitError_Init_NoServerForBackgroundApp:
            if not waiting:
                log.info("SteamVR is not running; waiting for it")
                waiting = True
            time.sleep(retry_sec)
            continue

        if waiting:
            log.info("SteamVR came up")
        return system


def _make_transform(
    offset: tuple[float, float, float], rotation_deg: tuple[float, float, float]
) -> openvr.HmdMatrix34_t:
    """Build an OpenVR 3x4 transform from a translation and euler angles.

    Rotations are applied X, then Y, then Z. In controller space -Z is the
    direction the controller points, +Y is up and +X is right, so moving
    towards the wrist means increasing Z and raising it means increasing Y.
    """
    rx, ry, rz = (math.radians(a) for a in rotation_deg)

    cx, sx = math.cos(rx), math.sin(rx)
    cy, sy = math.cos(ry), math.sin(ry)
    cz, sz = math.cos(rz), math.sin(rz)

    # R = Rz * Ry * Rx
    r = [
        [cz * cy, cz * sy * sx - sz * cx, cz * sy * cx + sz * sx],
        [sz * cy, sz * sy * sx + cz * cx, sz * sy * cx - cz * sx],
        [-sy, cy * sx, cy * cx],
    ]

    matrix = openvr.HmdMatrix34_t()
    for row in range(3):
        for col in range(3):
            matrix.m[row][col] = r[row][col]
        matrix.m[row][3] = offset[row]
    return matrix


class WristOverlay:
    """A single overlay that tracks one controller.

    Use it as a context manager, or call close() without fail. If the
    process dies with the overlay still registered, SteamVR keeps the key
    and the next run cannot create it.
    """

    def __init__(
        self,
        *,
        hand: str = "left",
        width_m: float = 0.14,
        offset: tuple[float, float, float] = (0.0, 0.02, 0.10),
        rotation_deg: tuple[float, float, float] = (-40.0, 0.0, 0.0),
        opacity: float = 1.0,
        flip_vertical: bool = False,
    ) -> None:
        self._hand = hand
        self._offset = offset
        self._rotation = rotation_deg
        self._flip_vertical = flip_vertical

        self._system = _connect()
        self._overlay = openvr.VROverlay()
        self._handle = self._overlay.createOverlay(OVERLAY_KEY, OVERLAY_NAME)
        self._overlay.setOverlayWidthInMeters(self._handle, width_m)
        self._overlay.setOverlayAlpha(self._handle, opacity)
        self._overlay.showOverlay(self._handle)

        # Index we last attached to, kept to detect changes.
        self._attached_index: int | None = None
        # The compositor reads this buffer after setOverlayRaw returns, so
        # it is allocated once and overwritten in place. Handing over a
        # fresh one each frame let the old one be freed mid-upload, which
        # showed up in the headset as a flicker once a second.
        self._buffer = None
        self._buffer_size: tuple[int, int] | None = None

        log.info("created overlay (hand=%s, width=%.3fm)", hand, width_m)

    # -- controller tracking ------------------------------------------------

    def _controller_index(self) -> int | None:
        role = (
            openvr.TrackedControllerRole_LeftHand
            if self._hand == "left"
            else openvr.TrackedControllerRole_RightHand
        )
        index = self._system.getTrackedDeviceIndexForControllerRole(role)
        if index == openvr.k_unTrackedDeviceIndexInvalid:
            return None
        return index

    def update_attachment(self) -> bool:
        """Keep the controller attachment current.

        True while a controller is present. False means there is nothing to
        attach to (powered off, for instance), which the caller can surface.
        """
        index = self._controller_index()
        if index is None:
            if self._attached_index is not None:
                log.info("lost the %s controller", self._hand)
                self._attached_index = None
            return False

        if index != self._attached_index:
            transform = _make_transform(self._offset, self._rotation)
            self._overlay.setOverlayTransformTrackedDeviceRelative(
                self._handle, index, transform
            )
            self._attached_index = index
            log.info("attached to the %s controller (index=%d)", self._hand, index)
        return True

    def set_placement(
        self,
        offset: tuple[float, float, float],
        rotation_deg: tuple[float, float, float],
    ) -> None:
        """Change position and angle, applied on the next update_attachment.

        Re-attaching is how a transform is replaced: there is no separate
        "move it" call, so the index is cleared to make the next
        update_attachment set the new transform.
        """
        if offset == self._offset and rotation_deg == self._rotation:
            return
        self._offset = offset
        self._rotation = rotation_deg
        self._attached_index = None  # force a re-attach
        log.info(
            "placement: offset=[%.3f, %.3f, %.3f] rotation=[%.1f, %.1f, %.1f]",
            *offset,
            *rotation_deg,
        )

    def set_width(self, width_m: float) -> None:
        self._overlay.setOverlayWidthInMeters(self._handle, width_m)

    def set_flip_vertical(self, flip: bool) -> None:
        """Flip the texture from here on.

        set_image skips uploads that match the current pixels, so the
        change lands on the next draw, when the flipped bytes differ.
        """
        self._flip_vertical = flip

    # -- drawing ------------------------------------------------------------

    def set_image(self, image) -> None:
        """Use a PIL RGBA image as the overlay texture.

        Does nothing when the pixels match what is already on screen. The
        draw loop runs every second so the age readout stays current, but
        the face itself only changes about once a minute, and every upload
        is a chance for the compositor to show a torn frame.
        """
        if image.mode != "RGBA":
            image = image.convert("RGBA")
        if self._flip_vertical:
            image = image.transpose(Image.Transpose.FLIP_TOP_BOTTOM)

        data = image.tobytes()
        size = (image.width, image.height)

        if self._buffer_size != size:
            self._buffer = (ctypes.c_char * len(data))()
            self._buffer_size = size
        elif self._buffer.raw == data:
            return

        self._buffer.raw = data  # in place: the pointer must not move
        # pyopenvr applies byref() to this itself, so hand it the ctypes
        # array as-is; wrapping it here raises a TypeError on the way in.
        self._overlay.setOverlayRaw(
            self._handle,
            self._buffer,
            image.width,
            image.height,
            4,  # RGBA
        )

    def set_opacity(self, opacity: float) -> None:
        self._overlay.setOverlayAlpha(self._handle, max(0.0, min(1.0, opacity)))

    # -- haptics ------------------------------------------------------------

    def pulse(self, duration_micros: int = 3000) -> None:
        """Buzz the controller briefly, used to flag a low reading.

        Best effort. Devices on the newer input system, such as Index
        controllers, may ignore it, so this must not be the only way an
        alert reaches the user: colour is the primary signal.
        """
        if self._attached_index is None:
            return
        try:
            self._system.triggerHapticPulse(self._attached_index, 0, duration_micros)
        except Exception as exc:  # unsupported on some devices
            log.debug("haptics unavailable: %s", exc)

    # -- SteamVR events -----------------------------------------------------

    def should_quit(self) -> bool:
        """True when SteamVR is shutting down.

        Ignoring this blocks SteamVR from exiting, so the main loop checks
        it on every pass.
        """
        event = openvr.VREvent_t()
        while self._system.pollNextEvent(event):
            if event.eventType in (
                openvr.VREvent_Quit,
                openvr.VREvent_DriverRequestedQuit,
            ):
                log.info("SteamVR asked us to quit")
                self._system.acknowledgeQuit_Exiting()
                return True
        return False

    # -- teardown -----------------------------------------------------------

    def close(self) -> None:
        try:
            if self._handle is not None:
                self._overlay.destroyOverlay(self._handle)
                self._handle = None
        finally:
            openvr.shutdown()
            log.info("destroyed the overlay and disconnected from SteamVR")

    def __enter__(self) -> "WristOverlay":
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()
