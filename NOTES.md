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
- **The fitted trend against a real day.** `graphData` itself is now
  confirmed: a dry run against the live API returned 48 points over 11.9
  hours at a median gap of 15.05 minutes, which is what
  `GRAPH_RESOLUTION_MIN` records and where the 45 minute window floor
  comes from. What is still open is whether an hour is the right window
  on a real arm — long enough not to twitch, short enough to notice a
  meal rise while it is still rising. That takes a session with the log
  open. Every fetch logs either a slope in mg/dL/min or the API arrow
  marked `(API)`, so the log says which source was used and how the
  number moved; `python src/main.py --dry-run` prints the same thing
  once. Watch for `(API)` while the sensor is scanning normally: that
  would mean the resolution has changed again.
- **The palette against real colour vision deficiency.** It is validated
  by simulation only: `tools/check_palette.py` runs the Viénot 1999 model
  and asserts the separations. That model is dichromacy — full absence of
  one cone type — and the anomalous trichromacies, which are far more
  common, are only approximated by it. Nobody with a deficiency has
  looked at the face. If someone can, the question to ask is whether in
  range and low read as different states at a glance, not whether the
  individual colours are nameable.

## In range and low are green and red on purpose, and that costs something

They match the official Libre app, because the phone is the other place
these numbers get read and having "fine" be green there and something
else here is its own hazard. It also puts the two most important states
on the axis red-green deficiency removes: simulated under deuteranopia
they are dE 13.9 apart, the closest pair on the face by some way.

What makes that survivable is that the direction is not in the colour.
In range lights the left edge and low lights the bottom one, so the two
are told apart by where the marker is even when the hues collapse.
`tools/check_palette.py` prints this as a warning rather than hiding it,
and the same pair becomes a hard failure the moment anything gives those
two statuses the same edge.

The obvious-looking fixes do not work, so do not spend the time again:

- **A more saturated red is unavailable, not merely undesirable.** Under
  protanopia `(255, 0, 0)` has 2.78 contrast against the card, well under
  the 4.5 legibility floor, because protanopes lose sensitivity to those
  wavelengths. The red in use is already about as red as stays readable.
- **Tuning within "still reads as red" is a losing trade.** The best
  available lifts the worst pair from dE 13.9 to only 22.7 — still under
  the floor — while dropping the redness from 148 to 95, which is a
  muted brick that no longer matches the app. It gives up the entire
  reason for being red and does not buy a pass.

Changing this means choosing to break with the app's colours, which is a
product decision, not a contrast one. Raise it as such.

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


## The low buzz is silent through Virtual Desktop

`triggerHapticPulse` neither buzzes nor raises with a Quest 3 running
through Virtual Desktop's SteamVR driver, which is the only stack this
has ever been tried on. The path around it is fine: with a fake threshold
the face goes red, so `pulse()` is reached.

The driver is the suspect, not the controllers. `triggerHapticPulse`
belongs to the legacy input API, and whether the call reaches the
hardware is up to whichever driver is presenting the device. Oculus Link
puts the same Quest 3 behind Meta's runtime and a different driver, so it
is a separate question, and an untried one.

- **Try it over Link before anything else.** It is the cheap test, and it
  decides whether there is a problem here to solve at all.
- **The attach line names the stack.** It logs the controller type and
  the tracking system presenting it, so a later report of no buzz says
  which driver it came from without anyone having to remember.

`IVRInput` haptic actions would settle it for every stack, but they need
an action manifest JSON shipped with the process and bindings per
controller type. That is a lot of work for a supplementary signal, so it
is not planned unless Link turns out to be silent too: colour is the
primary alert and haptics were only ever a supplement.

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
