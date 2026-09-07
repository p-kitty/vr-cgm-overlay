# The history sparkline

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

Turning it on grows the card from 512x256 to 512x440. The window resizes
itself on the next frame; in VR the face keeps the width `width_m` gives
it and gets taller — 12cm rather than 7 at the default `width_m` — so
expect to revisit `offset` if you switch it on there.

## What it draws

Each of these is a rule rather than a preference:

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
- **The line is clipped to the left edge, not started late.** A fixed
  window begins a round number of minutes ago and the samples arrive on
  a fifteen-minute grid that has no reason to line up with it, so the
  oldest sample inside sits somewhere in the first quarter hour of the
  plot. The segment crossing the boundary was measured, so the point
  where it crosses is worked out and the line begins there. A history
  that genuinely does not reach that far back is left alone — that edge
  is real.
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
- **The floor gets a quiet gridline** — a solid hairline, thinner and
  fainter than the dashed threshold lines above it, saying where the
  scale starts. Eventually there will be one every 50 mg/dL in that
  colour; this is the first of them.
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

## Seeing it without a headset or a sensor

`tools/preview.py` draws the face with no network and no headset. On its
own it writes `preview-states.png`, the four states at the top of
`README.md`; `--debug` writes `preview-debug.png` instead, which is the
working sheet — every marker edge side by side, the message card, a line
breaking across a scanning gap, and the labels in mmol/L.
