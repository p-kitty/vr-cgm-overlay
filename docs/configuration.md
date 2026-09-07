# Configuration

Every setting lives in `config.toml`, copied from `config.example.toml`
at setup. This file explains what each section does; the example file
carries the same thing as comments next to the values.

- [How the file is read](#how-the-file-is-read)
- [`[display]` — the face and where it goes](#display--the-face-and-where-it-goes)
- [`[window]` — the desktop window](#window--the-desktop-window)
- [`[thresholds]` — the colour bands](#thresholds--the-colour-bands)
- [`[polling]` — fetching, and being told about a low](#polling--fetching-and-being-told-about-a-low)
- [`[trend]` — the arrow](#trend--the-arrow)
- [`[graph]` — the history sparkline](graph.md)

## How the file is read

**`config.toml` is re-read while the app runs**, so edits show up in the
headset within a second. Leave it running, keep the headset on, and
change one number at a time. Only `hand` and the `[account]` settings
need a restart, and the log says so when one of them changes.

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
  display.window_min is not a setting; it belongs under [graph] or [trend]
  trend.windowmin is not a setting; did you mean window_min?
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

## `[display]` — the face and where it goes

`unit` picks mg/dL or mmol/L, `width_m` sets how big the overlay is, and
`stale_after_min` decides when a reading goes grey. The rest of the
section is placement, and placement is a job of its own: see
[Placing the face in VR](placement.md) for `offset`, `rotation_deg`,
orbit mode, the arm guides, and gaze fading.

The window ignores every placement key, since it has no controller to
follow — editing `hand` there says nothing rather than asking you to
restart for it.

## `[window]` — the desktop window

```toml
[window]
scale = 1.0
always_on_top = true
```

`scale` is a multiple of the face's own size, between `0.25` and `4.0`;
both it and `always_on_top` change while the window is up. Only
`vr-cgm-overlay --window` reads this section.

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
| `local` (true) | `true` fits the slope here; `false` uses Abbott's own `TrendArrow`, so the face and the phone show the same five arrows |
| `window_min` (60) | How far back the slope is fitted over. Longer is steadier and slower to notice a turn. The floor is 45: the history arrives at one point every 15 minutes, so a shorter window cannot hold enough of them to fit |
| `fast_mgdl_min` (2.0) | The rate at which the arrow stands straight up. Everything slower is in proportion, so half of it is the 45 degree diagonal |

The rate is mg/dL per minute, and like the colour bands it stays in
mg/dL in mmol/L mode. The angle slides rather than stepping, so a slow
drift and a hard climb do not draw the same arrow. Lower it to make the
arrow react harder.

When there is too little history to fit — a fresh sensor, or a stretch
where the phone was not scanning — the arrow falls back to the API's own
value and snaps to the five official positions, whatever `local` says.
`--dry-run` prints which of the two is in use, and so does the log line
on every fetch.

`[trend]` is re-read while running like `[display]` is, so `local` can be
flipped with the headset on to see both arrows against the same reading.

Why the trend is worked out here at all is in
[Why it is built this way](design.md#decisions-worth-knowing).
