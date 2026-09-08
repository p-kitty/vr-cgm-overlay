# Configuration

Every setting lives in `config.toml`, copied from `config.example.toml`
at setup. This file explains what each section does; the example file
carries the same thing as comments next to the values.

- [The settings window](#the-settings-window)
- [How the file is read](#how-the-file-is-read)
- [`[display]` — the reading itself](#display--the-reading-itself)
- [`[vr]` — the face on your arm](#vr--the-face-on-your-arm)
- [`[window]` — the desktop window](#window--the-desktop-window)
- [`[thresholds]` — the colour bands](#thresholds--the-colour-bands)
- [`[polling]` — fetching, and being told about a low](#polling--fetching-and-being-told-about-a-low)
- [`[trend]` — the arrow](#trend--the-arrow)
- [`[graph]` — the history sparkline](graph.md)

## The settings window

**Right-click the face** in the desktop window. Every section below gets
a tab, with one row per setting, labelled with the key's own name — so
what you change there is what this page explains.

It writes `config.toml` and stops. There is no separate path into the
running app: the file is saved, the watcher notices within a second, and
the change lands exactly as it would have if you had typed it into a
text editor. Save says so at the bottom of the window, and a value the
loader would refuse is reported there instead of being written, so the
window cannot leave you with a config the app will not start from.

Two things it does not do. There is **no live apply** — nothing is
written until Save, because every save is a full re-read and a value
half typed on the way to `80` is a real setting for as long as it takes
to type the next digit. And **`[vr]` is not offered**: where the face
sits on your arm cannot be judged from a desktop window while the
headset is on your head, so placement stays an edit you make in the file
while watching it move. See [Placing the face in VR](placement.md).

`vr-cgm-overlay --vr` has no window, so it has no settings window
either.

## How the file is read

**`config.toml` is re-read while the app runs**, so edits show up in the
headset within a second. Leave it running, keep the headset on, and
change one number at a time. Only the `[account]` settings need a
restart, and the log says so when one of them changes.

`hand` is the one edit that is not instant. The controller role is read
when the overlay is created, so changing it closes the overlay and opens
another -- which takes about a second and needs nothing from you. The
window, if one is up, does not blink.

**A setting nothing recognises stops the app** instead of being ignored.
A key in the wrong section, a misspelled key or a misspelled section is
otherwise accepted without a word and simply does nothing, which looks
like a broken feature rather than a typo -- and under `[thresholds]` it
means an alert that does not fire where you thought it would. The
message names what it found and always says something about it: where
the key should have gone, what it was probably meant to be, or -- when
it resembles nothing at all -- what the section does take.

```
config.toml holds settings nothing reads, so they would do nothing without saying so:
  display.window_min is not a setting; it belongs under [graph]
  graph.windowmin is not a setting; did you mean window_min?
  polling.nonsense is not a setting; [polling] takes alert_haptic, alert_on_low, ...
```

Saved mid-session this costs nothing: the reload logs `ignoring the
edited config` and keeps the settings already running. Where it does
bite is going backwards -- a `config.toml` written against a newer
commit will not start an older checkout, since the keys added since do
not exist there. Comment those out for as long as the old checkout is in
use.

`[account]` is checked the same way, with no exemption for being the
section most often pasted in from somewhere else. That is the section
most likely to arrive holding a key this does not read, and an
`api_verison` that quietly kept the default would turn up as a login the
API rejects hours later -- which is a worse morning than not starting.

## `[display]` — the reading itself

`unit` picks mg/dL or mmol/L and `stale_after_min` decides when a reading
goes grey. Two keys, and both are about the number rather than about the
screen it is on, which is what is left here now that the controller keys
have moved out.

## `[vr]` — the face on your arm

`hand` picks the controller to follow and `width_m` sets how big the
overlay is. The rest is placement, and placement is a job of its own: see
[Placing the face in VR](placement.md) for `offset`, `rotation_deg`,
orbit mode, the arm guides, and gaze fading.

Only the overlay reads this section, so `vr-cgm-overlay --window`
ignores all of it — editing a key here with no overlay running does
nothing and says nothing.

**These keys used to be in `[display]`.** A `config.toml` written before
the split does not start: every one of them is named, with `[vr]` given
as where it belongs, so the error message is the list of what to move.

## `[window]` — the desktop window

```toml
[window]
scale = 1.0
always_on_top = true
```

`scale` is a multiple of the face's own size, between `0.25` and `4.0`;
both it and `always_on_top` change while the window is up. Only the
window reads this section, so `vr-cgm-overlay --vr` ignores it.

## `[thresholds]` — the colour bands

All four are mg/dL and are used even in mmol/L mode, so changing the
display unit cannot quietly change what counts as a low.

| Reading | Colour | Marker |
|---|---|---|
| below `low_mgdl` (70) | red | bottom edge |
| `low_mgdl` to `high_mgdl` (70-180) | green | left edge |
| above `high_mgdl` to `very_high_mgdl` (181-240) | yellow | top edge |
| above `very_high_mgdl` (240) | deep orange | top edge, heavier |
| older than `stale_after_min` | grey | full outline |

Status is carried twice over. The colour gives severity, and a marker on
one edge of the card gives direction — above range lights the top, below
range the bottom, and stale outlines the whole card rather than pointing
anywhere.

The marker is there because colour alone does not reach everyone.
Red-green colour vision deficiency affects roughly 1 in 20 men and
flattens green, red and orange onto one olive band. Green and red are
kept because they are what the official Libre app uses, and a value that
means "fine" in one colour on the phone and another here would be worse
than either — but that choice is only safe because position is carrying
the distinction underneath it. Position does not depend on seeing colour
at all.

Someone with normal colour vision cannot check this by eye, so
`tools/check_palette.py` simulates the palette under protanopia and
deuteranopia. It fails on any pair that colour alone has to carry and
cannot, and warns on the pairs the markers are covering. Run it if you
change the colours — and if you remove a marker, read its warnings,
because each one becomes a real failure.

## `[polling]` — fetching, and being told about a low

`interval_sec` is how often the API is asked, and it cannot go below 30:
the sensor updates about once a minute, so polling faster returns
nothing new and risks the account being blocked.

The face going red is the alert. Everything below `alert_on_low` is a
supplement to it, for the case the face cannot cover: a low starting
while you are looking at something else.

```toml
[polling]
alert_on_low = true
alert_haptic = true
alert_sound = true
sound_path = ""
rearm_margin_mgdl = 5.0
repeat_every_min = 0.0
```

`alert_on_low` is the master switch. Under it, **`alert_haptic` buzzes
the controller** — VR only, and silent on some drivers, see
[Known limits](../README.md#known-limits) — and **`alert_sound` plays a
sound**, which works the same in VR and in `--window`. Sound is the
channel that reaches you without looking at your wrist, which is exactly
the case this is for.

An empty `sound_path` plays your Windows *Exclamation* sound, so it is
already whatever you chose. Point it at a `.wav` for something distinct.
Only `.wav`: anything else would need a decoder, and the dependency list
is deliberately short. Volume is the Windows mixer's, not this app's.

Two rules decide *when*, and both exist because an alert that cries wolf
gets muted, and a muted alert is worse than none because it is trusted.

- **It fires on the way in, not throughout.** `repeat_every_min = 0`
  means once per low. Set it to a number of minutes to be told again
  while it lasts — worth it if sleeping through one is the worry.
  The floor is 1, because a new reading only arrives about once a
  minute.
- **It waits for a real recovery before it will ring again.**
  `rearm_margin_mgdl` is how far back up the reading has to come before
  the next low counts as a new one. It is a margin on top of
  `low_mgdl`, so at the defaults the recovery mark is 75:

  | Reading | What happens |
  |---|---|
  | 68 | rings |
  | 71 | silent — over 70, but not back to 75, so this is still the same low |
  | 69 | silent — same low |
  | 76 | recovered; the next dip counts again |
  | 68 | rings |

  Without it (`0`) a reading drifting around the threshold rings on
  every crossing, and the sensor's own noise is a couple of mg/dL, so
  69-71-69 is an ordinary thing for it to do. It changes only when the
  sound fires — the face turns red at `low_mgdl` either way.

## `[trend]` — the arrow

| Setting | What it does |
|---|---|
| `local` (true) | `true` reads the arrow out of the history here; `false` uses Abbott's own `TrendArrow`, so the face and the phone show the same five arrows |
| `fast_mgdl_min` (2.0) | The rate at which a segment of the arrow stands straight up. Everything slower is in proportion, so half of it is the 45 degree diagonal |

**The arrow is bent through the last half hour**, not pointed along an
average of it. Its shaft runs through the three most recent points —
about 30 minutes ago, 15 minutes ago, and now — with the head at the
`now` end, so the bend between the two segments is the shape of that
stretch: still climbing, levelling off, or rolling over. A reading that
has been rising steadily and one that fell and has just turned around
used to draw the same arrow; they no longer do.

It says what is happening now, and deliberately not what happens next.
Nothing here knows about meals or injections, so there is no forecast
to be had at any price and the face must not imply one.

`fast_mgdl_min` is mg/dL per minute, and like the colour bands it stays
in mg/dL in mmol/L mode. It is the magnification on the whole shape
rather than a scale on one angle: the sensor's own jitter is a couple of
mg/dL, which over a 15 minute gap is about 6 degrees of segment at the
default, so lowering this to catch a slow drift also makes a flat
reading twitch. Raise it if it does.

There are three arrows, and which one gets drawn depends on how much of
the last half hour is really there. `--dry-run` prints which, and so
does the log line on every fetch — a bend that quietly stopped appearing
would otherwise read as calm glucose.

| Source | When | What is drawn |
|---|---|---|
| `bend` | three points inside the last 45 minutes | two segments, the head on the newer one |
| `fit` | three points anywhere in the last hour | one straight arrow at the fitted rate |
| `(API)` | fewer than that, or `local = false` | one of `TrendArrow`'s five positions |

The middle row is not theoretical: the history the API publishes lags
the current measurement, often by 20 to 30 minutes, and past 45 the
three most recent points stop being the last half hour.

`[trend]` is re-read while running like `[display]` is, so `local` can be
flipped with the headset on to see both arrows against the same reading.

Why the trend is worked out here at all is in
[Why it is built this way](design.md#decisions-worth-knowing).
