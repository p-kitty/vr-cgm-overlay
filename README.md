# vr-cgm-overlay

A SteamVR overlay that keeps your current blood glucose on your wrist
while you play. Readings come from LibreLinkUp.

It runs as an OpenVR overlay, so **no game needs modding or patching** —
the face composites over any SteamVR title.

![states](preview-states.png)

## Design

```
┌─ LibreLinkUp Cloud (api-jp.libreview.io and friends) ─┐
│  POST /llu/auth/login              → token, region    │
│  GET  /llu/connections             → patientId        │
│  GET  /llu/connections/{id}/graph                     │
└────────────────────┬──────────────────────────────────┘
                     │ HTTPS every 60s (jitter + exponential backoff)
┌────────────────────▼──────────────────────────────────┐
│  vr-cgm-overlay (resident, single process)            │
│                                                       │
│                                  ┌─→ cgm.vr           │
│    cgm.core     ──→ cgm.face  ───┤   pyopenvr         │
│    auth + fetch     PIL drawing  └─→ cgm.desk         │
│                                      tkinter          │
│    cgm.main: 60s fetch loop / 1s draw loop            │
└────────────────────┬──────────────────────────────────┘
                     │ SetOverlayTransformTrackedDeviceRelative
           ┌─────────▼──────────┐
           │ SteamVR Compositor │ → wrist-tracked, over every game
           └────────────────────┘
```

The same face has two places to go. `cgm.vr` puts it on a controller;
`cgm.desk` (`--window`) puts it in a desktop window. Neither imports the
other, and everything above them is shared.

### Decisions worth knowing

**Fetching and drawing run at separate rates.** The sensor updates about
once a minute, so polling faster returns nothing new and only risks being
cut off by Abbott. The age readout and controller tracking, though, need
to refresh every second.

**The last reading stays up when the network drops**, but its age keeps
climbing and it greys out past ten minutes. A display that silently
freezes mid-session is the dangerous failure, so stale has to look stale.

**Range checks are always mg/dL**, even in mmol/L mode, so switching the
display unit cannot quietly change what counts as a low.

**`config.toml` is watched, not just read at startup.** Placement can
only be judged with the headset on, and restarting for each nudge means
taking it off again. A bad or half-written edit is logged and ignored
rather than taken.

**Arrows are drawn, not typeset.** Segoe UI and the other stock Windows
fonts have no U+2197/U+2198 glyphs and render tofu boxes. The trend
matters nearly as much as the number, so it does not depend on a font.

**The trend is worked out here by default, not taken from the API.**
Abbott's own
`TrendArrow` is five buckets on thresholds it does not publish and
nothing here can adjust, so a gentle drift and a hard climb arrive as
the same arrow. The same response already carries about twelve hours of
history, which used to be discarded; a line is fitted through the last
hour of it instead, and because the arrow is a drawing rather than a
glyph it can point anywhere. An hour rather than a few minutes because
that history is downsampled to a point every fifteen minutes — coarser
than the sensor's own record, and what decides how long a window the fit
needs. Nothing extra is fetched and nothing
is stored, so the trend is right again the moment the process restarts.
`TrendArrow` stays as the fallback for a fresh sensor or a gap in
scanning, and `trend.local = false` goes back to it entirely for anyone
who would rather the face and the phone agree exactly.

**The same history is read twice.** The response the current value
arrives in carries about twelve hours of it, so once the trend was being
fitted from it, drawing it was free — no extra request, no cache,
nothing stored. The desktop window shows the last three hours as a
sparkline under the number; the overlay does not by default, because a
face glanced at mid-game is there to be read in half a second.

**Credentials live in `config.toml`, which git ignores.** That file grants
access to health data; keep it out of the repository.

### LibreLinkUp API quirks

This is an unofficial API, so the client works around the failures that
have actually been reported against existing clients.

| Quirk | Workaround |
|---|---|
| A stale `version` header is rejected with a 4xx | `api_version` in `config.toml` |
| Login answers with a region redirect | Follow `data.redirect` and log in again (once) |
| `account-id` header (SHA256 of the user id) required | Derived at login, sent on every later call |
| `Timestamp` is local time with no zone | Age is computed from `FactoryTimestamp` (UTC) |
| `Value` is in the account's own unit, which follows its country | Only `ValueInMgPerDl` is read; mmol/L is derived from it |
| No token while terms or email verification are pending | `step.type` is detected and explained |
| Tokens expire with no refresh endpoint and no warning | A 401 triggers one automatic re-login |
| `TrendArrow` is five buckets on undocumented thresholds | The trend is fitted from `graphData`; `TrendArrow` is the fallback |
| `graphData` is downsampled to a point every 15 min | The trend window has a 45 minute floor, so three points can land in it |

## Setup

Needs **Python 3.14 or newer** — any 3.14.x, nothing here cares which
patch release. An older interpreter is turned away at startup rather
than failing halfway through an import.

Name the interpreter rather than trusting whichever `python` is on PATH.
A machine with more than one Python installed will hand you the wrong
one, and installing a fresh Windows build does not change what an
already-open terminal resolves.

```bash
py -3.14 -m venv .venv
.venv\Scripts\Activate.ps1
pip install -e ".[vr]"
cp config.example.toml config.toml
```

`-e` because the package is installed from this checkout rather than
copied out of it: editing a file under `src/` changes what runs, with no
reinstall. `[vr]` adds the SteamVR bindings, which only the overlay
itself loads: `pip install -e .` on its own is enough for `--dry-run`,
`--window`, the tests and every script under `tools/`, since the two
that reach into the overlay stub the bindings out rather than needing
them.

Every later command in this file assumes that environment is active.

Put your LibreLinkUp `email` and `password` in `config.toml`.

That file holds your password, so it is excluded by `.gitignore`. Do not
share or commit it.

Check the API before involving SteamVR.

```bash
vr-cgm-overlay --dry-run
```

A glucose value on the console and a `preview.png` on disk means the API
side is done. Then leave it running.

```bash
vr-cgm-overlay
```

## Without a headset

The same watch face runs in a desktop window.

```bash
vr-cgm-overlay --window
```

No SteamVR, no `openvr`, no headset — a plain `pip install -e .` is
enough. It is the same fetching and the same face, so everything in
**Tuning** below that is not about placement applies here too: the
colour bands, the units, the trend arrow, the stale greying, and
`config.toml` being re-read while it runs.

Useful for a second monitor while you are doing something other than
playing, and useful for anything that takes hours to show itself — a
long session's fetch schedule, a token expiring and being renewed, or
whether the trend window is the right length against a real day. None of
those are VR questions, and none of them are worth wearing a headset for
as long as they take to answer.

```toml
[window]
scale = 1.0
always_on_top = true
```

`scale` is a multiple of the face's own size, between `0.25` and `4.0`;
both it and `always_on_top` change while the window is up. The placement
keys mean nothing here — there is no controller — so editing `hand` says
nothing rather than asking you to restart for it.

**The window draws the history sparkline and the overlay does not**, and
that is the one thing the two frontends deliberately disagree about. See
[The history sparkline](#the-history-sparkline); `graph.in_window` turns
it off if you would rather have the number alone.

`alert_on_low` works here through sound. The controller buzz is the one
channel a window has no hardware for; everything about *when* to
announce a low is shared, so the window and the headset cannot disagree
about whether you have already been told.

## Tuning

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

Getting the watch face where you want it is trial and error. The loop is:

1. Start `vr-cgm-overlay` and put the headset on.
2. Bring up the desktop view in SteamVR so you can edit `config.toml`
   without taking the headset off.
3. Change one value, save, and look at your wrist.

Each time the placement changes, the log prints what it became:

```
placement: offset=[0.000, 0.020, 0.100] rotation=[-40.0, 0.0, 0.0]
```

### What the numbers mean

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

### Fixing what you actually see

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

### Orbit mode

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

### Tuning it with the guides

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

### Gaze mode

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

`[thresholds]` sets the colour bands. All four are mg/dL and are used
even in mmol/L mode, so changing the display unit cannot quietly change
what counts as a low.

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

### Being told about a low

The face going red is the alert. Everything in `[polling]` below
`alert_on_low` is a supplement to it, for the case the face cannot
cover: a low starting while you are looking at something else.

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
the controller** — VR only, and silent on some drivers, see **Known
limits** — and **`alert_sound` plays a sound**, which works the same in
VR and in `--window`. Sound is the channel that reaches you without
looking at your wrist, which is exactly the case this is for.

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

`[trend]` sets how the arrow is worked out.

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

### The history sparkline

The last few hours drawn under the number, so the shape of where the
reading came from is there as well as where it is. It costs nothing to
fetch: the same response the value arrives in already carries about
twelve hours of history, and until now only the trend arrow read it.

```toml
[graph]
in_window = true
in_vr = false
window_min = 480.0
axis_high_mgdl = 300.0
```

| Setting | What it does |
|---|---|
| `in_window` (true) | Draw it in the desktop window |
| `in_vr` (false) | Draw it on the controller face |
| `window_min` (480) | How far back it shows — eight hours by default, long enough to hold a night. **`0` means all of it**: everything the response carried, about twelve hours, with the time axis running from the oldest reading to the newest. Any other value is a length, and the floor is then 30, since the history arrives at one point every 15 minutes |
| `axis_high_mgdl` (300) | The **minimum** top. It grows to the next round 50 above anything higher, so a hyper is never clipped |

The bottom of the axis is **50 mg/dL and is not a setting**. Every graph
starts in the same place, so the eye learns where the floor is once
rather than per config file, and two of these are comparable. It follows
that `low_mgdl` cannot be set below 50; the app says so at startup if it
is.

A fixed window and `0` are for different things. `0` shows the most, and
the axis is as long as the history behind it, so it never draws empty
space where a sensor had not been running yet. A fixed window holds the
axis still between fetches, which is what you want if you are glancing
rather than reading.

**The two frontends are separate settings on purpose.** A window is read
at a desk, where a few hours of history is worth the room it takes. The
overlay is glanced at mid-game, where the number in half a second is the
whole design goal, so it stays off there unless you ask for it.

Turning it on grows the card from 512x256 to 512x404. The window resizes
itself on the next frame; in VR the face keeps the width `width_m` gives
it and gets taller, so expect to revisit `offset` if you switch it on
there.

What it draws, and why each of these is a rule rather than a preference:

- **The Y axis does not shrink to the data.** Scaling to whatever the
  last few hours happened to do turns a quiet flat stretch into a
  mountain range, which makes a calm reading look alarming at exactly
  the glance this face exists for. So the bottom never moves at all,
  and the top never drops below `axis_high_mgdl`.
- **It does grow upwards, and only to hold a hyper.** A reading over
  the top takes the axis to the next round 50 above it rather than
  being flattened against the edge. That is the one case worth
  redrawing the scale for, and you can tell it has happened because a
  number appears at the top of the axis, which is not there otherwise.
  Under the floor is the other way round: a 42 is drawn on the 50 line,
  because the floor is the one part of the scale that can be relied on
  to stay put — and the digits above are saying 42 in red at the size
  of the card.
- **The target range is a band behind the line**, so where the trace
  sits reads without an axis drawn next to it. That is what the axis has
  to contain the range for: clipped against an edge, a band stops
  looking like a band and starts looking like a floor.
- **The line breaks across gaps rather than spanning them.** A stretch
  where the phone was not scanning gets no line drawn through it,
  because a line there would be measurements that were never taken.
- **Except in front of the newest point.** One response carries both the
  current measurement and the history, but they are not equally fresh:
  `graphData`'s newest entry trails `glucoseMeasurement` — 18 minutes
  when this was last measured against the live API, and 30 in
  `NOTES.md`. That is the service publishing a sample into the array
  late, not the sensor failing to read, and the proof is the reading
  sitting at the end of the gap. So the last point is joined across it,
  up to an hour. Past an hour it is left stranded, because a phone that
  really stopped scanning looks identical from here and joining across
  an afternoon would draw one that never happened.
- **The floor gets a quiet gridline**, saying where the scale starts.
  Eventually there will be one every 50 mg/dL in that colour; this is
  the first of them.
- **`low_mgdl` and `very_high_mgdl` each get a dashed line**, in the
  colour the face turns at that level — red below, deep orange above.
  They are the two the band does not mark: its lower edge is `low_mgdl`,
  but a change of shade is not a line, and `very_high_mgdl` is outside
  it altogether.
- **The newest point is marked in the status colour**, the same colour
  as the digits. It is the one place the graph and the number are the
  same fact, and it says which end is now.
- **The labels follow `display.unit` and your own clock.** The levels
  read 50 and 240 in mg/dL mode and 2.8 and 13.3 in mmol/L; the times
  along the bottom are local, and step to whatever keeps them to four
  or so. The comparisons behind all of it stay in mg/dL.
- **50, `low_mgdl` and `very_high_mgdl` are always labelled.** They are
  the scale: where it starts and the two levels it is read against. The
  floor and `low_mgdl` are only twenty apart, which is what the height
  of the plot is set by — the labels have to fit rather than the plot
  having to be filled. The top of the axis is labelled only once it has
  grown, so a number appearing there means the scale is no longer the
  one in the config.

`tools/preview.py` draws all of it — a meal rise, a quiet run, a fall
into a low, a reading off the top of the axis, a scanning gap, and a
fresh sensor with two points to its name — to `preview-states.png`,
with no network and no headset.

## Known limits

The low-glucose **buzz** is silent on a Quest 3 running through Virtual
Desktop, which is the one setup this has been tried on. It uses the
legacy haptic call, which a driver is free to ignore, so whether it
buzzes on yours is a question only running it answers. This is why
`alert_sound` exists and defaults on: a sound reaches you whatever the
controller driver decides to do with the buzz.

The **sound** goes wherever Windows is sending audio, which for most
setups — anything playing through the desktop and out to headphones
— is already where you are listening. If your headset takes its own
output device and you cannot hear the alert in VR, make it the default
device in Windows. **Both are supplements; the face itself is the real
alert.**

## Cautions

- This uses an unofficial LibreLinkUp API. Abbott does not support it, and
  the app breaks if the API changes without notice.
- **Do not use this for medical decisions.** Treat from the official app
  and a glucose meter.
- The polling interval cannot be set below 30 seconds, to avoid getting
  the account blocked.

## License

MIT License. See [LICENSE](LICENSE).
