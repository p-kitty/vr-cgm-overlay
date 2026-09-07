# Placing the face in VR

Everything in `[display]` that decides where the face sits on your arm
and when it is lit. None of it applies to `--window`, which has no
controller to follow. For the settings both frontends share, see
[Configuration](configuration.md).

- [Getting it where you want it](#getting-it-where-you-want-it)
- [What the numbers mean](#what-the-numbers-mean)
- [Fixing what you actually see](#fixing-what-you-actually-see)
- [Orbit mode](#orbit-mode)
- [Tuning it with the guides](#tuning-it-with-the-guides)
- [Gaze mode](#gaze-mode)

## Getting it where you want it

Getting the watch face where you want it is trial and error. The loop is:

1. Start `vr-cgm-overlay` and put the headset on.
2. Bring up the desktop view in SteamVR so you can edit `config.toml`
   without taking the headset off.
3. Change one value, save, and look at your wrist.

`config.toml` is re-read while the app runs, so edits show up in the
headset within a second. Only `hand` and the `[account]` settings need a
restart, and the log says so when one of them changes.

Each time the placement changes, the log prints what it became:

```
placement: offset=[0.000, 0.020, 0.100] rotation=[-40.0, 0.0, 0.0]
```

## What the numbers mean

`offset` is in **metres** — `0.01` is one centimetre. `rotation_deg` is
in **degrees**. Both are relative to the controller, not to the room, so
they follow your hand around.

Point the controller away from you, like a torch. Then:

| | Raising it | Lowering it |
|---|---|---|
| `offset` X | moves right | moves left |
| `offset` Y | moves up, off the back of the hand | sinks it into your arm |
| `offset` Z | slides it back towards the elbow | slides it forward past the hand |
| `rotation_deg` X | tips the face away from you, flat onto your arm | stands it up towards your eyes |
| `rotation_deg` Y | swings the face to the right | swings it to the left |
| `rotation_deg` Z | spins the face in place, like turning a dial | spins it the other way |

## Fixing what you actually see

| It looks like | Change |
|---|---|
| Sitting on the back of the hand, not the wrist | raise `offset` Z: `0.10` → `0.14` |
| You twist your wrist right over to read it | lower `rotation_deg` X: `-40` → `-55` |
| Floating off the side of your arm | nudge `offset` X by `0.01` (1 cm) at a time |
| Buried in your arm, or clipping through it | raise `offset` Y: `0.02` → `0.04`, or turn on orbit mode |
| Digits run across your arm, not along it | put `90` or `-90` into `rotation_deg` Z |
| Upside down | `flip_vertical = true` |
| In the corner of your eye all session, not just when you look | `gaze_fade = true` |
| Too big, or too small to read | `width_m`, around `0.14` |

Controller origins differ between Index, Touch and Vive, so assume the
first run needs tuning.

## Orbit mode

A fixed placement is bolted to the controller, and that is the problem:
your forearm is not. Rolling your wrist turns your hand about twice as
far as the forearm follows it, so a face that lies neatly on your arm
palm-down is inside your arm palm-up. It does not hide behind the arm
either — SteamVR composites overlays over the scene without a depth
test, so it cuts straight through.

Orbit mode fixes it by modelling your forearm as a line and letting the
face ride around that line to whichever side your head is on, the way a
watch slides round a wrist. It is then outside your arm whatever your
hand is doing, and square to your eye without you turning your wrist to
read it.

```toml
orbit = true
offset = [0.0, -0.02, 0.10]
rotation_deg = [0.0, 0.0, 0.0]
orbit_radius_m = 0.06
orbit_limit_deg = 120.0
```

Two of the settings change meaning when it is on:

| | With orbit off | With orbit on |
|---|---|---|
| `offset` | where the face sits | a point on your forearm's centreline: X and Y say where the arm runs relative to the controller, Z how far back along it |
| `rotation_deg` | how the face is aimed | a trim on top of the aiming, which is now automatic |

`orbit_limit_deg` is how far round the arm it may travel from the top,
either way. At `120` it stops before it reaches the underside; `180`
lets it go anywhere.

## Tuning it with the guides

Both the line and the circle orbit mode works from are invisible, which
is what makes `offset` hard to set by nudging: you are moving something
you cannot see and judging it by how the face ends up behaving. So draw
them instead.

```toml
arm_guide = true
```

A **cyan line** appears down the modelled centreline of your forearm, and
a row of **magenta dots** on the circle the face travels. The dots are
turned to face you one by one, so the arc reads from any angle rather
than vanishing edge-on the way a drawn circle would. The wide dot is the
top of the wrist, and the arc the dots span is exactly how far
`orbit_limit_deg` lets the face go.

Now the settings are things you look at:

1. **`offset` X and Y** until the cyan line runs down the middle of your
   arm and stays there as you turn your hand. This is the one that was
   guesswork; with the line drawn it is not. Y usually wants to be
   negative, the controller origin sitting above your wrist.
2. **`offset` Z** until the dots ring the part of your arm you want the
   face on. Keep it near the wrist, around `0.08` to `0.12`: the model is
   most accurate there, for the reason in `NOTES.md`.
3. **`orbit_radius_m`** until the dots sit just clear of your sleeve.
4. **`rotation_deg` Z** by 90 or 180, only if the digits come out
   sideways or upside down.

Judge the line by where it crosses the dots, not by its far end. It runs
40 cm, and it is expected to splay off the arm towards the elbow.

Then set `arm_guide = false`. The guides are a tuning aid, not part of
the display.

## Gaze mode

The face is on your wrist, which means it is in view whenever your hand
is, including all the time you were not asking it anything. Gaze mode
fades it down while you are not looking at it and back up when you are,
so the number stays glanceable without also being lit in the corner of
your eye for a whole session.

```toml
gaze_fade = true
gaze_full_deg = 20.0
gaze_fade_deg = 45.0
gaze_min_alpha = 0.25
```

What it measures is the angle between where you are **looking** and
where the face is, not where your hand is. Holding your wrist up beside
your eye while looking somewhere else dims it, the same as dropping your
arm does. Within `gaze_full_deg` of the centre of your view the face is
at the full `opacity`; past `gaze_fade_deg` it is at `gaze_min_alpha`;
between the two it slides. The fade itself takes about a third of a
second in either direction, slow enough not to flicker as your eye
crosses it and quick enough to be up before a glance has settled.

Two things it will not do, on purpose:

- **It never fades to nothing.** `gaze_min_alpha` is refused below
  `0.1`. Something that vanished outright would look exactly like the
  process having died, which is the failure this whole thing exists to
  avoid, so there is always a ghost of it left.
- **It never fades a low.** While the reading is under `low_mgdl` the
  face is held at full opacity whatever you are looking at. Colour is
  how a low is signalled, and dimming it at the moment it matters most
  would invert the priority.

It is off by default. A glucose readout is not a desktop window, and
being able to see it without looking for it is most of the point — turn
this on only if you find it is one thing too many in view.

`tools/check_gaze.py` asserts both of those rules, along with the
geometry, without needing a headset.
