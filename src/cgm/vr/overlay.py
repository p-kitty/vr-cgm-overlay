"""SteamVR overlay creation and controller tracking.

OpenVR composites overlays without touching the game process, so this
shows up over any SteamVR title with no mods.

A controller's device index changes when it sleeps, wakes or re-pairs, so
the overlay is re-attached when the index changes rather than every frame.

Placement is either fixed in controller space or, in orbit mode, computed
each pass so the face rides around the forearm towards the head. Both are
device-relative transforms, so the compositor still applies the live
controller pose at frame rate either way.

Gaze mode, when it is on, dims the face while it is away from the centre
of view. It never dims to nothing, and never while a low is showing: see
set_alert.
"""

from __future__ import annotations

import ctypes
import logging
import math
import time

import openvr
from PIL import Image

from cgm.vr.armguide import MARKER_COUNT, ArmGuide

log = logging.getLogger(__name__)

OVERLAY_KEY = "jp.local.vrcgm.wristglucose"
OVERLAY_NAME = "VR CGM Wrist Glucose"

# How often to re-check for SteamVR while waiting for it to come up.
CONNECT_RETRY_SEC = 3.0

# Refresh rates outside this are taken as a driver reporting nonsense
# rather than as a real headset.
DISPLAY_HZ_RANGE = (30.0, 360.0)

# Time constant for the orbit easing: the angle closes about 63% of the gap
# to where it is aiming in this long. Expressed as a time rather than a
# fraction per update so the motion does not change when the loop rate does,
# and so an uneven tick does not show as an uneven slide. It also stops the
# face twitching when the head is nearly in line with the arm and the target
# direction is ill-conditioned.
ORBIT_SMOOTH_SEC = 0.12

# The same easing for the gaze fade, and slower on purpose. The orbit has
# to keep up with a head turn or the face is left facing the wrong way,
# but brightness moving at that speed reads as a flicker every time the
# eye crosses the face. A third of a second is slow enough to be a fade
# rather than a blink, and short enough that the face is up by the time a
# glance has settled on it.
GAZE_SMOOTH_SEC = 0.35

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


def _cross(a, b) -> tuple[float, float, float]:
    return (
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    )


def _billboard(
    position: tuple[float, float, float], head: tuple[float, float, float]
) -> list[list[float]]:
    """Turn a marker at `position` to face the head, all in controller space.

    A quad seen edge-on is not there. The orbit circle lies across the arm,
    so drawn as one quad it disappears from the side, which is exactly where
    a wrist gets looked at. Turning each marker to the head instead costs a
    basis per marker and makes the arc readable from anywhere.
    """
    to_head = [head[i] - position[i] for i in range(3)]
    length = math.sqrt(sum(c * c for c in to_head))
    if length < 1e-6:
        return _IDENTITY
    z_axis = tuple(c / length for c in to_head)

    # Keep the marker's up towards the hand, so it rolls with the face
    # rather than spinning on its own. Any hint does while the dot is round;
    # this one keeps a square marker square to the arm.
    hint = (0.0, 0.0, -1.0)
    x_axis = _cross(hint, z_axis)
    length = math.sqrt(sum(c * c for c in x_axis))
    if length < 1e-6:  # looking straight down the arm: pick any other hint
        x_axis = _cross((0.0, 1.0, 0.0), z_axis)
        length = math.sqrt(sum(c * c for c in x_axis))
    x_axis = tuple(c / length for c in x_axis)
    y_axis = _cross(z_axis, x_axis)

    return [[x_axis[r], y_axis[r], z_axis[r]] for r in range(3)]


def _into_controller(
    rotation, vector: list[float] | tuple[float, float, float]
) -> tuple[float, float, float]:
    """Rotate a world space vector into the frame `rotation` describes.

    The rotation block of a tracked pose is orthonormal, so its transpose
    inverts it. This applies that transpose without building it.
    """
    return tuple(
        sum(rotation[row][col] * vector[row] for row in range(3)) for col in range(3)
    )


def _gaze_alpha(
    face: tuple[float, float, float],
    head: tuple[float, float, float],
    forward: tuple[float, float, float],
    full_deg: float,
    fade_deg: float,
    min_alpha: float,
) -> float:
    """How much of the configured opacity the face has earned right now.

    All three vectors are in controller space, and `forward` is the unit
    direction the headset is looking. The angle between that and the
    direction from the head to the face is how far off the centre of view
    the face is sitting, and that angle, not where the head happens to
    be, is what the fade follows. Holding the wrist up beside your eye
    while looking elsewhere dims it, the same as looking away across the
    room does.

    Full opacity within `full_deg` of the centre of view, `min_alpha`
    past `fade_deg`, and a straight line between the two. The result
    never drops below `min_alpha`, which cgm.core.config refuses to let reach
    zero: a face that vanished outright would look exactly like the
    process having died, which is the failure this exists to avoid.
    """
    to_face = [face[i] - head[i] for i in range(3)]
    length = math.sqrt(sum(c * c for c in to_face))
    if length < 1e-6:
        # The face is at the eye. There is no direction to it to measure,
        # and it fills the view regardless, so count it as looked at.
        return 1.0

    cosine = sum(to_face[i] * forward[i] for i in range(3)) / length
    # Rounding can leave a dot product a hair outside [-1, 1], where acos
    # raises rather than saturating.
    angle = math.degrees(math.acos(max(-1.0, min(1.0, cosine))))

    if angle <= full_deg:
        return 1.0
    if angle >= fade_deg:
        return min_alpha
    travelled = (angle - full_deg) / (fade_deg - full_deg)
    return 1.0 - travelled * (1.0 - min_alpha)


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
    elapsed: float,
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
    be passed back in on the next call. `elapsed` is the time since that
    call, and is unused on the first one, when there is nothing to ease.
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
        eased = 1.0 - math.exp(-max(0.0, elapsed) / ORBIT_SMOOTH_SEC)
        angle = previous + (target - previous) * eased

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
        gaze_fade: bool = False,
        gaze_full_deg: float = 20.0,
        gaze_fade_deg: float = 45.0,
        gaze_min_alpha: float = 0.25,
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
        # When that angle was last worked out, so the easing can be a rate.
        self._orbit_at: float | None = None

        self._gaze = gaze_fade
        self._gaze_full = gaze_full_deg
        self._gaze_fade = gaze_fade_deg
        self._gaze_min = gaze_min_alpha
        # The fraction of `opacity` currently showing, eased towards what
        # the gaze angle asks for. 1.0 with the fade off, so the alpha is
        # then simply the configured one.
        self._gaze_factor = 1.0
        # When that fraction was last worked out, so the easing is a rate.
        self._gaze_at: float | None = None
        # True while a low is on the face, which pins it at full opacity.
        # See set_alert.
        self._alert = False
        # The configured opacity, before any fade is taken off it.
        self._opacity = max(0.0, min(1.0, opacity))
        # What was last handed to the compositor, so a fade that has all
        # but settled stops sending a call per frame to say so.
        self._applied_alpha: float | None = None

        self._system = _connect()
        self._overlay = openvr.VROverlay()
        self._handle = self._overlay.createOverlay(OVERLAY_KEY, OVERLAY_NAME)
        self._overlay.setOverlayWidthInMeters(self._handle, width_m)
        self._apply_alpha()
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

    def display_hz(self, fallback: float) -> float:
        """What the headset refreshes at, to pace the tracking loop against.

        Quest 3 runs at 72 by default and an Index goes to 144, so a rate
        fixed in the source is either wasted work or visible steps depending
        on whose headset it lands on. Read at startup only: changing the
        refresh rate mid-session is rare and needs a restart to take.
        """
        try:
            hz = self._system.getFloatTrackedDeviceProperty(
                openvr.k_unTrackedDeviceIndex_Hmd, openvr.Prop_DisplayFrequency_Float
            )
        except Exception as exc:  # not every driver fills this in
            log.debug("no display frequency reported: %s", exc)
            return fallback

        low, high = DISPLAY_HZ_RANGE
        if not low <= hz <= high:
            log.warning("ignoring a reported display rate of %.1fHz", hz)
            return fallback
        return hz

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

    def _device_name(self, index: int) -> str:
        """What the driver calls this controller, for the attach log line.

        A device-dependent finding here -- the low buzz staying silent, the
        offset defaults sitting wrong -- usually belongs to the driver
        presenting the controller rather than to the controller itself, and
        the same Quest 3 arrives through a different driver over Link than
        over Virtual Desktop. Naming both at attach is what lets a later
        report say which stack it came from.
        """
        names = []
        for prop in (
            openvr.Prop_ControllerType_String,
            openvr.Prop_TrackingSystemName_String,
        ):
            try:
                name = self._system.getStringTrackedDeviceProperty(index, prop)
            except Exception as exc:  # not every driver fills these in
                log.debug("no string property %d on device %d: %s", prop, index, exc)
                continue
            if name:
                names.append(name)
        return "/".join(names) or "unnamed"

    def _head_in_controller_space(
        self, index: int
    ) -> tuple[tuple[float, float, float], tuple[float, float, float]] | None:
        """Where the headset is and which way it looks, in controller space.

        None while either pose is missing, which happens for a moment around
        a wake-up. Working in controller space is what lets orbit mode stay a
        device-relative transform: the compositor keeps applying the live
        controller pose at frame rate, and only the choice of which side of
        the arm to sit on is left to this loop, where a little lag does not
        show.

        The direction is what gaze mode needs and the orbit does not. The
        orbit only cares which side of the arm the head is on; a fade has
        to know whether the face is actually being looked at.
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
        # A headset looks down its own -Z, and the rotation block is
        # orthonormal, so the negated third column is already a unit
        # vector pointing where the wearer is facing.
        forward = [-h[row][2] for row in range(3)]
        return _into_controller(c, delta), _into_controller(c, forward)

    def _apply_orbit(self, index: int, head: tuple[float, float, float]) -> None:
        # perf_counter, not monotonic: monotonic is GetTickCount64 on
        # Windows and steps in 15.6ms, which would quantise the easing.
        now = time.perf_counter()
        # A long gap, from a stall or a controller coming back, should land
        # where it is aiming rather than crawl there from wherever it was.
        elapsed = now - (self._orbit_at or now)
        self._orbit_at = now

        transform, self._orbit_angle = _orbit_transform(
            self._offset,
            self._orbit_radius,
            self._orbit_limit,
            self._rotation,
            head,
            self._orbit_angle,
            elapsed,
        )
        self._overlay.setOverlayTransformTrackedDeviceRelative(
            self._handle, index, transform
        )

    def _face_position(self) -> tuple[float, float, float]:
        """Where the face is sitting in controller space.

        In orbit mode it rides around the arm, so it is wherever the last
        update left it; with the orbit off it is the offset itself. Only
        the gaze fade asks: the transform already knows, but reading a
        position back out of an HmdMatrix34_t costs the 32 bit rounding
        for no reason when the angle is right here.
        """
        if not self._orbit or self._orbit_angle is None:
            return self._offset
        cx, cy, cz = self._offset
        return (
            cx + math.sin(self._orbit_angle) * self._orbit_radius,
            cy + math.cos(self._orbit_angle) * self._orbit_radius,
            cz,
        )

    def _apply_gaze(
        self, head: tuple[float, float, float], forward: tuple[float, float, float]
    ) -> None:
        """Ease the alpha towards what the current gaze angle asks for."""
        # perf_counter for the same reason the orbit uses it: monotonic
        # steps in 15.6ms on Windows and would quantise the easing.
        now = time.perf_counter()
        elapsed = now - (self._gaze_at or now)
        self._gaze_at = now

        if self._alert:
            # Pinned, and pinned outright rather than eased there. An
            # alert that arrived by fading up is an alert arriving late.
            self._gaze_factor = 1.0
        else:
            target = _gaze_alpha(
                self._face_position(),
                head,
                forward,
                self._gaze_full,
                self._gaze_fade,
                self._gaze_min,
            )
            eased = 1.0 - math.exp(-max(0.0, elapsed) / GAZE_SMOOTH_SEC)
            self._gaze_factor += (target - self._gaze_factor) * eased
        self._apply_alpha()

    def _apply_guide(self, index: int, head: tuple[float, float, float]) -> None:
        """Draw the arm the placement is currently modelling.

        The line is the centreline itself, so it sits at radius zero and is
        only turned towards the head to stop it vanishing edge-on. The
        markers are spread over the arc the face may travel, so the run of
        them shows orbit_radius_m and orbit_limit_deg at once.
        """
        axis, _ = _orbit_transform(
            self._offset, 0.0, 180.0, (0.0, 0.0, 0.0), head, None, 0.0
        )

        cx, cy, cz = self._offset
        limit = math.radians(self._orbit_limit)
        markers = []
        for i in range(MARKER_COUNT):
            # -1 at one end of the travel, +1 at the other, 0 in the middle.
            angle = (i / (MARKER_COUNT - 1) * 2.0 - 1.0) * limit
            position = (
                cx + math.sin(angle) * self._orbit_radius,
                cy + math.cos(angle) * self._orbit_radius,
                cz,
            )
            markers.append(_to_openvr(_billboard(position, head), position))

        self._guide.update(index, axis, markers)

    def update_attachment(self) -> bool:
        """Keep the attachment, and the computed pose and alpha, current.

        True while a controller is present. False means there is nothing to
        attach to (powered off, for instance), which the caller can surface.

        Call this on every pass of the loop, not only when redrawing: in
        orbit mode it is what turns the face towards the head, and with
        the gaze fade on it is what fades it.
        """
        index = self._controller_index()
        if index is None:
            if self._attached_index is not None:
                log.info("lost the %s controller", self._hand)
                self._attached_index = None
                self._orbit_angle = None
                self._orbit_at = None
                self._gaze_at = None
                # Bring it back at full when the controller returns. A
                # face that reappeared already dim is one more thing to
                # have to second guess.
                self._gaze_factor = 1.0
                self._apply_alpha()
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
            self._orbit_at = None
            log.info(
                "attached to the %s controller (index=%d, %s)",
                self._hand,
                index,
                self._device_name(index),
            )

        if self._orbit or self._gaze or self._guide is not None:
            pose = self._head_in_controller_space(index)
            if pose is not None:  # else keep what is on screen until it returns
                head, forward = pose
                if self._orbit:
                    self._apply_orbit(index, head)
                # After the orbit: that is what moves the face, and the
                # gaze angle is measured to wherever it left it.
                if self._gaze:
                    self._apply_gaze(head, forward)
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
        self._orbit_at = None
        if was_on and not enabled:
            self._attached_index = None  # force the fixed transform back on
        log.info(
            "orbit: %s (radius=%.3fm, limit=%.0f deg)",
            "on" if enabled else "off",
            radius_m,
            limit_deg,
        )

    def set_gaze(
        self, enabled: bool, full_deg: float, fade_deg: float, min_alpha: float
    ) -> None:
        """Change the gaze fade, applied on the next update_attachment."""
        current = (self._gaze, self._gaze_full, self._gaze_fade, self._gaze_min)
        if current == (enabled, full_deg, fade_deg, min_alpha):
            return
        self._gaze = enabled
        self._gaze_full = full_deg
        self._gaze_fade = fade_deg
        self._gaze_min = min_alpha
        self._gaze_at = None
        if not enabled:
            # Nothing else will move it back, and being left dim by
            # switching the fade off would look like a bug.
            self._gaze_factor = 1.0
            self._apply_alpha()
        log.info(
            "gaze fade: %s (full within %.0f deg, floor %.2f past %.0f deg)",
            "on" if enabled else "off",
            full_deg,
            min_alpha,
            fade_deg,
        )

    def set_alert(self, active: bool) -> None:
        """Hold the face at full opacity, whatever the gaze fade wants.

        Colour is how a low is signalled, so dimming the face at the
        moment it matters most inverts the priority. The draw loop calls
        this with the reading's own verdict.

        A no-op with the fade off, where the face is at full anyway.
        """
        if active == self._alert:
            return
        self._alert = active
        if active:
            self._gaze_factor = 1.0
            self._apply_alpha()

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

    def _apply_alpha(self) -> None:
        """Push the configured opacity, less whatever the fade is taking.

        Skips a call that would not change what is on screen. The easing
        approaches its target rather than arriving at it, so without this
        a settled fade would still be sending one call per frame to say
        the same thing.
        """
        alpha = self._opacity * self._gaze_factor
        if self._applied_alpha is not None and abs(alpha - self._applied_alpha) < 1e-3:
            return
        self._applied_alpha = alpha
        self._overlay.setOverlayAlpha(self._handle, alpha)

    def set_opacity(self, opacity: float) -> None:
        self._opacity = max(0.0, min(1.0, opacity))
        self._apply_alpha()

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
