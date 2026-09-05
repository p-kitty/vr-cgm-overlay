"""SteamVR overlay creation and controller tracking.

OpenVR composites overlays without touching the game process, so this
shows up over any SteamVR title with no mods.

A controller's device index changes when it sleeps, wakes or re-pairs, so
the overlay is re-attached when the index changes rather than every frame.

Placement is either fixed in controller space or, in orbit mode, computed
each pass so the face rides around the forearm towards the head. Both are
device-relative transforms, so the compositor still applies the live
controller pose at frame rate either way.
"""

from __future__ import annotations

import ctypes
import logging
import math
import time

import openvr
from PIL import Image

from armguide import ArmGuide

log = logging.getLogger(__name__)

OVERLAY_KEY = "jp.local.vrcgm.wristglucose"
OVERLAY_NAME = "VR CGM Wrist Glucose"

# How often to re-check for SteamVR while waiting for it to come up.
CONNECT_RETRY_SEC = 3.0

# How far the orbit direction moves towards its target each update. The
# transform is recomputed at the loop rate rather than the frame rate, so
# easing hides the steps; it also stops the face twitching when the head is
# nearly in line with the arm and the target direction is ill-conditioned.
ORBIT_EASE = 0.25

_IDENTITY = [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]]


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


def _euler_matrix(rotation_deg: tuple[float, float, float]) -> list[list[float]]:
    """Build a 3x3 rotation from euler angles, applied X, then Y, then Z."""
    rx, ry, rz = (math.radians(a) for a in rotation_deg)

    cx, sx = math.cos(rx), math.sin(rx)
    cy, sy = math.cos(ry), math.sin(ry)
    cz, sz = math.cos(rz), math.sin(rz)

    # R = Rz * Ry * Rx
    return [
        [cz * cy, cz * sy * sx - sz * cx, cz * sy * cx + sz * sx],
        [sz * cy, sz * sy * sx + cz * cx, sz * sy * cx - cz * sx],
        [-sy, cy * sx, cy * cx],
    ]


def _matmul(a: list[list[float]], b: list[list[float]]) -> list[list[float]]:
    return [
        [sum(a[i][k] * b[k][j] for k in range(3)) for j in range(3)] for i in range(3)
    ]


def _to_openvr(
    rotation: list[list[float]], translation: tuple[float, float, float]
) -> openvr.HmdMatrix34_t:
    matrix = openvr.HmdMatrix34_t()
    for row in range(3):
        for col in range(3):
            matrix.m[row][col] = rotation[row][col]
        matrix.m[row][3] = translation[row]
    return matrix


def _make_transform(
    offset: tuple[float, float, float], rotation_deg: tuple[float, float, float]
) -> openvr.HmdMatrix34_t:
    """A fixed pose in controller space: the face is bolted to the controller.

    In controller space -Z is the direction the controller points, +Y is up
    and +X is right, so moving towards the wrist means increasing Z and
    lifting it off the back of the hand means increasing Y.
    """
    return _to_openvr(_euler_matrix(rotation_deg), offset)


def _orbit_transform(
    centre: tuple[float, float, float],
    radius: float,
    limit_deg: float,
    rotation_deg: tuple[float, float, float],
    head: tuple[float, float, float],
    previous: float | None,
) -> tuple[openvr.HmdMatrix34_t, float]:
    """Sit the face on the near side of the forearm, turned towards the head.

    A fixed transform bolts the face to the controller, but the forearm is
    not rigid relative to it. Rolling the wrist turns the hand roughly twice
    as far as the forearm follows, so a placement that lies neatly on the arm
    palm-down ends up buried inside it palm-up. Nor does it hide behind the
    arm when that happens: an overlay is composited over the scene with no
    depth test, so it visibly cuts through it.

    The arm is modelled instead as a line through `centre` along the
    controller's Z axis, and the face rides around that line to whichever
    side the head is on, the way a watch slides round a wrist. It is then
    outside the arm whatever the hand is doing, and square to the eye.

    `previous` is the angle around the arm picked last time, eased away from
    so a head turn does not snap the face across the arm. It is returned to
    be passed back in on the next call.
    """
    # The arm axis is the controller's Z, so the perpendicular part of the
    # direction to the head is just its X and Y. The whole choice is then a
    # single angle around the arm, measured from the top of the wrist.
    vx = head[0] - centre[0]
    vy = head[1] - centre[1]
    limit = math.radians(limit_deg)

    if math.hypot(vx, vy) < 1e-4:
        # The head is on the arm's axis: no side of it is nearer than any
        # other, so stay where we are.
        target = 0.0 if previous is None else previous
    else:
        # Hold it to the back of the wrist, where a watch lives. Unlimited,
        # it follows the head round to the palm side and is read edge-on.
        target = max(-limit, min(limit, math.atan2(vx, vy)))

    if previous is None:
        angle = target
    else:
        # Both ends are inside [-limit, limit], so easing between them stays
        # inside it and sweeps over the top of the arm rather than under it.
        # Only limit_deg = 180 has no forbidden side for that to matter.
        angle = previous + (target - previous) * ORBIT_EASE

    nx, ny = math.sin(angle), math.cos(angle)
    position = (centre[0] + nx * radius, centre[1] + ny * radius, centre[2])

    # Axes of the face in controller space, as columns: Z out of the face
    # towards the head, Y up the texture towards the hand, X = Y x Z.
    basis = [
        [ny, 0.0, nx],
        [-nx, 0.0, ny],
        [0.0, -1.0, 0.0],
    ]
    # rotation_deg still applies, now as a trim in the face's own frame.
    return _to_openvr(_matmul(basis, _euler_matrix(rotation_deg)), position), angle


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
        orbit: bool = False,
        orbit_radius_m: float = 0.06,
        orbit_limit_deg: float = 120.0,
        arm_guide: bool = False,
    ) -> None:
        self._hand = hand
        self._offset = offset
        self._rotation = rotation_deg
        self._flip_vertical = flip_vertical
        self._orbit = orbit
        self._orbit_radius = orbit_radius_m
        self._orbit_limit = orbit_limit_deg
        # Angle around the arm the face currently sits at, carried between
        # updates so it can be eased rather than jumped. None means "no
        # history yet", and the next update places it outright.
        self._orbit_angle: float | None = None

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
        # The tuning guides, or None. See armguide.
        self._guide: ArmGuide | None = None
        # Reused for every pose read: orbit mode reads poses on every pass
        # of the loop, and a fresh array each time is pure churn.
        self._poses = (openvr.TrackedDevicePose_t * openvr.k_unMaxTrackedDeviceCount)()

        log.info("created overlay (hand=%s, width=%.3fm)", hand, width_m)
        self.set_arm_guide(arm_guide)

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

    def _head_in_controller_space(self, index: int) -> tuple[float, float, float] | None:
        """Where the headset is, in the controller's own frame.

        None while either pose is missing, which happens for a moment around
        a wake-up. Working in controller space is what lets orbit mode stay a
        device-relative transform: the compositor keeps applying the live
        controller pose at frame rate, and only the choice of which side of
        the arm to sit on is left to this loop, where a little lag does not
        show.
        """
        self._system.getDeviceToAbsoluteTrackingPose(
            openvr.TrackingUniverseStanding, 0.0, self._poses
        )
        head = self._poses[openvr.k_unTrackedDeviceIndex_Hmd]
        hand = self._poses[index]
        if not (head.bPoseIsValid and hand.bPoseIsValid):
            return None

        h = head.mDeviceToAbsoluteTracking.m
        c = hand.mDeviceToAbsoluteTracking.m
        delta = [h[row][3] - c[row][3] for row in range(3)]
        # The rotation block is orthonormal, so its transpose inverts it.
        return tuple(
            sum(c[row][col] * delta[row] for row in range(3)) for col in range(3)
        )

    def _apply_orbit(self, index: int, head: tuple[float, float, float]) -> None:
        transform, self._orbit_angle = _orbit_transform(
            self._offset,
            self._orbit_radius,
            self._orbit_limit,
            self._rotation,
            head,
            self._orbit_angle,
        )
        self._overlay.setOverlayTransformTrackedDeviceRelative(
            self._handle, index, transform
        )

    def _apply_guide(self, index: int, head: tuple[float, float, float]) -> None:
        """Draw the arm the placement is currently modelling.

        The line is the centreline itself, so it sits at radius zero and is
        only turned towards the head to stop it vanishing edge-on. The ring
        lies in the plane across the arm, which is the controller's own XY
        plane, so it needs no rotation at all.
        """
        axis, _ = _orbit_transform(
            self._offset, 0.0, 180.0, (0.0, 0.0, 0.0), head, None
        )
        ring = _to_openvr(_IDENTITY, self._offset)
        self._guide.update(index, axis, ring, self._orbit_radius)

    def update_attachment(self) -> bool:
        """Keep the controller attachment, and in orbit mode the pose, current.

        True while a controller is present. False means there is nothing to
        attach to (powered off, for instance), which the caller can surface.

        Call this on every pass of the loop, not only when redrawing: in
        orbit mode it is what turns the face towards the head.
        """
        index = self._controller_index()
        if index is None:
            if self._attached_index is not None:
                log.info("lost the %s controller", self._hand)
                self._attached_index = None
                self._orbit_angle = None
            return False

        if index != self._attached_index:
            # The fixed transform goes on either way. In orbit mode it is
            # what shows until a head pose can be read.
            transform = _make_transform(self._offset, self._rotation)
            self._overlay.setOverlayTransformTrackedDeviceRelative(
                self._handle, index, transform
            )
            self._attached_index = index
            self._orbit_angle = None
            log.info("attached to the %s controller (index=%d)", self._hand, index)

        if self._orbit or self._guide is not None:
            head = self._head_in_controller_space(index)
            if head is not None:  # else keep what is on screen until it returns
                if self._orbit:
                    self._apply_orbit(index, head)
                if self._guide is not None:
                    self._apply_guide(index, head)
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

        In orbit mode `offset` names a point on the forearm's centreline and
        `rotation_deg` becomes a trim on the face's own axes; see
        _orbit_transform.
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

    def set_orbit(self, enabled: bool, radius_m: float, limit_deg: float) -> None:
        """Change orbit mode, applied on the next update_attachment."""
        current = (self._orbit, self._orbit_radius, self._orbit_limit)
        if current == (enabled, radius_m, limit_deg):
            return
        was_on = self._orbit
        self._orbit = enabled
        self._orbit_radius = radius_m
        self._orbit_limit = limit_deg
        self._orbit_angle = None
        if was_on and not enabled:
            self._attached_index = None  # force the fixed transform back on
        log.info(
            "orbit: %s (radius=%.3fm, limit=%.0f deg)",
            "on" if enabled else "off",
            radius_m,
            limit_deg,
        )

    def set_arm_guide(self, enabled: bool) -> None:
        """Show or hide the tuning guides, creating them the first time."""
        if enabled == (self._guide is not None):
            return
        if enabled:
            self._guide = ArmGuide(self._overlay, OVERLAY_KEY)
            if not self._orbit:
                log.info("the guides describe orbit mode, which is off")
        else:
            self._guide.close()
            self._guide = None

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
            if self._guide is not None:
                self._guide.close()
                self._guide = None
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
