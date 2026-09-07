# Why it is built this way

The decisions that shaped the app, and the unofficial API it has to live
with. `README.md` has the diagram and the everyday commands; this file
is the reasoning behind them.

## Decisions worth knowing

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
history, which used to be discarded; the arrow is read out of that
instead, and because it is a drawing rather than a glyph it can point
anywhere. Nothing extra is fetched and nothing is stored, so the trend
is right again the moment the process restarts. `TrendArrow` stays as
the fallback for a fresh sensor or a gap in scanning, and
`trend.local = false` goes back to it entirely for anyone who would
rather the face and the phone agree exactly.

**The arrow is bent through three points, not fitted through many.**
The arrow says what is happening now; it does not say what happens
next, because nothing here knows about meals or injections and without
those a forecast is not available at any price. So what it should carry
is the shape of the last half hour, and a least-squares fit is the one
thing that cannot carry it — smoothing is precisely what buries a
reading that fell and has just turned around, which comes out of a fit
identical to one that has been climbing all along. The shaft runs
through the three most recent points instead, each segment at the rate
its own two points give, so the arrow and the chart are drawing the same
stretch. That costs jitter, and knowingly: two mg/dL of sensor noise
over a fifteen minute gap is about six degrees of segment, on glucose
doing nothing. The chart flattens that and the arrow magnifies it, and
`trend.fast_mgdl_min` is the magnification.

**The fit is what is left when those three points are not there.** The
history is downsampled to a point every fifteen minutes — coarser than
the sensor's own record — and it is published behind the current
measurement, often by twenty to thirty minutes. Past forty-five the
three most recent points are no longer the last half hour, so the arrow
refuses the bend and falls back to a line fitted over the hour, which is
what it always drew before. Every fetch logs which of the three it
used, because a bend that quietly stopped appearing would read as calm
glucose.

**The same history is read twice.** The response the current value
arrives in carries about twelve hours of it, so once the trend was being
fitted from it, drawing it was free — no extra request, no cache,
nothing stored. The desktop window shows the last three hours as a
sparkline under the number; the overlay does not by default, because a
face glanced at mid-game is there to be read in half a second. What it
draws, and the rules behind each part of it, is in
[The history sparkline](graph.md).

**Credentials live in `config.toml`, which git ignores.** That file grants
access to health data; keep it out of the repository.

## LibreLinkUp API quirks

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
| `graphData` is downsampled to a point every 15 min | Three points are half an hour, which is the stretch the arrow bends through |
| `graphData` lags the current measurement, by 20-30 min | Past a 45 minute span the arrow gives up the bend and falls back to a fit |
