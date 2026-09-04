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
rejects anything breaking `urgent_low < low < high`; raise `high_mgdl`
too when the reading is already above it. Put both back afterwards.

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
