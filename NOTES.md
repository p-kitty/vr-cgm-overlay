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
- **A long session.** Leave it running for an hour or two, then check
  the log still shows `fetched:` about once a minute, with no `fetch
  failed` streak stretching the interval out. `--window` counts — this
  is the fetch schedule, which the two frontends share.
- **Token expiry and the automatic re-login.** No quick way to reach it;
  tokens outlast any session. It will surface on its own eventually, as a
  401 followed by one re-login in the log. A `--window` left open for
  days is the cheap way to be there when it does.
- **`trend.fast_mgdl_min` against a real day.** It is now the most
  consequential number on the face and it has never been looked at on a
  real arm. It was a scale on one angle; with the arrow bent through
  the last half hour it is the magnification on a whole shape, and the
  sensor's own jitter goes through it too -- a couple of mg/dL over a
  fifteen minute gap is about six degrees of segment and twelve of bend,
  on glucose doing nothing at all. Too low and the arrow twitches; too
  high and a meal rise is a shrug. The default of 2.0 is a guess that
  has never been tested as a magnification.

  A session with the log open answers it, and `--window` makes that a
  desk job rather than an hour in a headset, since none of this is VR.
  Every fetch logs the rate of each segment and the angle it became --
  `+0.80/+1.20 mg/dL/min (bend, +36/+54 deg)` -- so the pair can be read
  against each other; `vr-cgm-overlay --dry-run` prints one of the same.
  **The number can only be settled this way, and taking the setting out
  is meant to follow once it is.** Until then it stays configurable.

- **How often the bend is actually available.** `graphData` itself is
  confirmed: a dry run against the live API returned 48 points over 11.9
  hours at a median gap of 15.05 minutes, which is what
  `GRAPH_RESOLUTION_MIN` records. What decides whether the arrow bends
  is not that spacing but the lag: the current reading is folded into
  the series (see `_parse_graph_data`) so it always holds the right edge,
  and every other point has to come from `graphData`, so the further
  that lags the further back the third point sits. Past
  `BEND_MAX_SPAN_MIN` the arrow gives up the bend and fits a line
  instead, which the log calls `(fit)` rather than `(bend)`.

  Measured at a lag of 26 minutes and growing a minute a minute, which
  puts the third point at 41 and the bend just inside its 45. So a day
  with a lag much past 30 draws the old straight arrow for most of it,
  and the log is the only place that shows it. Neither `(fit)` nor
  `(API)` while the sensor is scanning normally means the resolution has
  changed -- check the lag first.

  The sparkline shows the same lag from the other side. Past 30 minutes
  it exceeded `MAX_GAP_MIN` and the trace broke in front of the newest
  point, which looked like a fault and was not one; `LAST_GAP_MIN` now
  joins that one gap up to an hour. So a break there again means a lag
  over an hour, which nothing has yet seen, and is worth measuring
  rather than assuming.
- **`LAST_GAP_MIN` is a guess, and the number it is guarding against
  has never been bounded.** The sparkline joins its newest point across
  a gap of up to an hour, because that gap is `graphData` being
  published late rather than the sensor not reading. An hour was picked
  as comfortably past the largest lag anyone has seen -- 18 minutes
  measured on 2026-09-07, 30 in the entry below -- and nothing has ever
  watched the lag long enough to say how far it really goes.

  It can be wrong in both directions, and each shows differently:

  - **Too low**: the trace breaks in front of the newest point again,
    the way it did at 30. That means a lag over an hour, which would be
    news. Measure it before raising the constant -- compare the last
    `FactoryTimestamp` in `graphData` against the one on
    `glucoseMeasurement`, both in the same response.
  - **Too high**: a stretch where the phone genuinely was not scanning
    gets joined up, and the graph draws a straight line across hours
    nobody measured. That is the failure worth catching, because unlike
    a visible break it does not look wrong.

  **Neither case is being hunted.** No session is scheduled to bound the
  lag; the constant stays where it is and gets corrected if ordinary use
  turns one of the two up. Both are visible from the face itself -- a
  break in front of the newest point, or a flat run under an age that
  kept climbing -- so waiting for one costs nothing.

  The two cases are indistinguishable from inside `cgm.face.graph`,
  which sees only timestamps. If the bound turns out to need tuning
  rather than a one-off correction, the thing to reach for is the
  reading's age -- during a real scanning gap the current measurement
  is stale too, and during a publication lag it is not.
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

## Showing the graph in VR is a config switch, not yet a gesture

`graph.in_vr` is a boolean in `config.toml`. It reloads within a second
like everything else there, so it can be flipped with the headset on --
but it is flipped by alt-tabbing to a text editor, which is not the same
as being able to call the graph up when you want it and have it gone
again the rest of the time. That is what was actually asked for, and it
is not built.

Three ways to reach it, cheapest first:

- **The gaze angle is already computed.** `cgm.vr.overlay` measures how
  far the face is from the centre of view every frame, for the fade.
  Showing the graph only while you are actually looking at your wrist
  needs no new input API, works on every stack, and is the only one of
  these that cannot be broken by a driver. What it costs is that the
  card changes size as you glance at it, which moves the number: the
  overlay grows around its centre, so the digits would shift by half the
  strip's height every time. Compensating means moving `offset` in step
  with the size, which is a small piece of arithmetic and the reason
  this is not free.
- **A controller button, through `getControllerState`.** The obvious
  answer, and the one with a known risk: that is the legacy input API,
  and the legacy haptic call on the same API does nothing on this stack
  (see the buzz entry above). Whether button state fares better than
  haptics through Virtual Desktop's driver is unknown and worth ten
  minutes to find out before designing anything around it.
- **`IVRInput` with an action manifest.** Settles it for every stack and
  every controller, and is the same work the buzz entry declines: a JSON
  manifest shipped with the process and bindings per controller type. If
  both a button and the buzz end up needing it, the cost is paid once
  rather than twice, which changes the arithmetic.

**The legibility question is settled and the dismissal one is not.**
Run on a Quest 3 on 2026-09-07: eight hours of trace across a 378px plot
reads as a shape at arm's length rather than as texture under the
number, and it has been left on since. Growing the card from 7cm to 12cm
around its centre wanted `offset` Y moved 0.02m, which is the whole
retune. So nothing above is blocked on whether the graph is worth
showing -- it is.

What is still missing is the being-gone-again half. `in_vr = false`
stays the shipped default, because that verdict came from one stack with
`gaze_fade` on, which is doing the dismissing that none of the three
options above are built to do. Somebody running without the fade has a
graph that is always in the way and no way to put it down.

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

**`alert_sound` has taken the pressure off this.** The gap the silent
buzz left was that nothing reached the user who was not looking at their
wrist, and a sound covers that on every stack, since it never goes near
a controller driver. What is left here is a channel that does not work
on one setup rather than a user who cannot be told, so the Link test is
worth doing out of curiosity and no longer worth doing first.

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
