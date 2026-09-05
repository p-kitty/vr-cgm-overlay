# Notes

What is still open: paths nothing has exercised yet, limits that are not
going away, and decisions that still stand.

**Entries come out once they are resolved.** A fixed bug lives in its
commit message and in a comment beside the code it bit; repeating it here
only makes the file grow until nobody reads it. This is a list of what is
still true, not a log of what happened.

`README.md` is for people running this. This file is for people working
on it.

## Unverified paths

Nothing has exercised these yet. Each says how to check it.

- **Controller sleep and wake.** With the process running, power the
  controller off, wait, and power it back on. The log should show `lost
  the left controller` and then `attached to the left controller`, and
  the face should come back.
- **Stale greying.** Set `display.stale_after_min = 1.0` and watch the
  value and the age readout go grey within a couple of minutes. Put it
  back to `10.0`.
- **A long session.** Play for an hour or two, then check the log still
  shows `fetched:` about once a minute, with no `fetch failed` streak
  stretching the interval out.
- **Token expiry and the automatic re-login.** No quick way to reach it;
  tokens outlast any session. It will surface on its own eventually, as a
  401 followed by one re-login in the log.

To exercise the low path without waiting for a real low, set
`thresholds.low_mgdl` above the current reading and restart: the first
draw counts as a transition into a low, which colours the face red and
fires the buzz. It has to stay under `high_mgdl`, because `_validate`
rejects anything breaking `low < high < very_high`; raise
`high_mgdl` and `very_high_mgdl` too when the reading is already above
them. Put them all back afterwards.

## The modelled arm drifts from the real one towards the elbow

Orbit mode takes the forearm to run along the controller's Z axis. That
is very nearly right at the wrist and wrong at the elbow, and turning the
hand is what separates them. The carpals rotate with the radius, so the
hand and the wrist joint move as one lump, while the far end of the
forearm barely rotates at all. A line fixed in controller space therefore
holds its place where it meets the wrist and swings away from the arm
further down it.

This is not going away, and measuring the axis instead of assuming it
would not help: the error is a rotation that happens as the hand turns,
not a fixed misalignment to calibrate out.

What it does is bound where the face can sensibly sit. Keep `offset` Z
near the wrist, around 0.08 to 0.12, where the model is close to exact.
The guide line is 40cm long and will always splay off the arm near the
elbow; it is meant to be judged where it passes the markers.


## Gaze fade would have to be a mode

Desktop+ fades an overlay out when it is not being looked at, which
suits something wanted glanceable but not permanently in view. The same
trick fits here, and is cheap: orbit mode already works out where the
head is relative to the face, and `setOverlayAlpha` is already called.

It is not in. If it goes in it goes in switchable and off by default,
because a glucose readout is not a desktop window, and two things would
have to hold:

- **A floor on the alpha, never zero.** Something that vanished outright
  would look exactly like the process having died, which is the failure
  this whole thing exists to avoid.
- **No fading while low.** Colour is the alert. Dimming it at the moment
  it matters most inverts the priority.


## Haptics do not work on Quest 3

`triggerHapticPulse` is silent on Quest 3 controllers. The call neither
buzzes nor raises. The low path around it is fine: with a fake threshold
the face goes red, so `pulse()` is reached.

`triggerHapticPulse` belongs to the legacy input API and devices on the
newer input system may ignore it. Making it buzz would mean moving to
`IVRInput` haptic actions, which needs an action manifest JSON shipped
with the process and bindings per controller type. That is a lot of work
for a supplementary signal, so it is not planned: colour is the primary
alert and haptics were only ever a supplement.

## Placement defaults are tuned for one device

`offset` and `rotation_deg` have only ever been tried on Quest 3
controllers. Controller origins differ between Index, Touch and Vive, so
a first run on anything else should expect to tune them.

Orbit mode narrows this without closing it: the aiming is computed, so
`rotation_deg` is only a trim, but `offset` still has to say where the
forearm runs relative to the controller, and that is per device.

`IVRRenderModels.getComponentState` would settle it. Every controller
model carries a `handgrip` component whose pose the driver normalises to
a neutral grip, so reading it and composing it with `offset` would give
the same placement across devices. Not done: it needs the render model
name and a component state per device, and one line in `config.toml` has
covered it so far.
