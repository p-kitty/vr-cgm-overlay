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
│    cgm.main: 1s draw loop, fetch and track on threads │
└────────────────────┬──────────────────────────────────┘
                     │ SetOverlayTransformTrackedDeviceRelative
           ┌─────────▼──────────┐
           │ SteamVR Compositor │ → wrist-tracked, over every game
           └────────────────────┘
```

The same face has two places to go, and **one process runs both**.
`cgm.vr` puts it on a controller and `cgm.desk` puts it in a desktop
window; neither imports the other, and everything above them — the
login, the fetch schedule, the low alert — happens once and is shared.
So a low is announced once rather than once per screen, and the API sees
one poller however many places you are reading it.

Neither half is compulsory. `--window` leaves SteamVR alone, `--vr`
leaves the window out, and the default is both.

Why it behaves the way it does — separate fetch and draw rates, stale
readings that look stale, a trend fitted here rather than taken from the
API — is in [Why it is built this way](docs/design.md), along with the
quirks of the unofficial API the client works around.

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

That opens the desktop window and waits for SteamVR. **It does not need
SteamVR to be up**: put the headset on whenever you like and the overlay
appears, quit SteamVR and it goes, and the window carries on either way.
Start it once and leave it there.

A small **VR** appears in the window's top corner while the face is
actually on a controller, and goes away when it is not — SteamVR down, a
controller asleep, or nothing paired yet all look the same from a desk,
and none of them are faults.

Controller origins differ between Index, Touch and Vive, so assume the
first run needs tuning: see
[Placing the face in VR](docs/placement.md).

## Without a headset

The desktop window is up by default, so there is nothing to do but look
at it. `--window` is for saying you want *only* that:

```bash
vr-cgm-overlay --window
```

No SteamVR, no `openvr`, no headset — a plain `pip install -e .` is
enough, and it is also what to reach for on a machine that has no
SteamVR at all. (A plain `vr-cgm-overlay` there says so in the log and
carries on with the window.) It is the same fetching and the same face,
so everything in
[Configuration](docs/configuration.md) that is not about placement
applies here too: the colour bands, the units, the trend arrow, the
stale greying, and `config.toml` being re-read while it runs.

Useful for a second monitor while you are doing something other than
playing, and useful for anything that takes hours to show itself — a
long session's fetch schedule, a token expiring and being renewed, or
whether `trend.fast_mgdl_min` draws a readable arrow against a real day
rather than a twitchy one. None of those are VR questions, and none of
them are worth wearing a headset for as long as they take to answer.

**The window draws the history sparkline and the overlay does not**, and
that is the one thing the two frontends deliberately disagree about. See
[The history sparkline](docs/graph.md); `graph.in_window` turns it off
if you would rather have the number alone.

`alert_on_low` works here through sound. The controller buzz is the one
channel a window has no hardware for; everything about *when* to
announce a low is shared, so with both frontends up you are told once
rather than twice.

The other way round, `--vr` runs the overlay with no window at all —
worth having when the window is one more thing on a taskbar you are not
looking at.

## Settings

**Click the gear** in the top corner of the face — or right-click
anywhere on it — for a settings window: thresholds, units, polling,
alerts and the graph, in tabs. It writes `config.toml` and does
nothing else — the running app picks the edit up the same way it picks
up one made in a text editor, and it refuses to write anything the app
would not start from.

Placement is not in there, on purpose. Where the face sits on your arm
can only be judged with the headset on, so `[vr]` stays a file you edit
while looking at it: see [Placing the face in VR](docs/placement.md).

Everything is in `config.toml` either way, and it is **re-read while the
app runs** — edit it with the headset on and the face changes within a
second. Only `[account]` needs a restart (`hand` reopens the overlay by
itself, which takes about a second), and a key nothing recognises stops
the app rather than being silently ignored.

| Where to look | For |
|---|---|
| [Configuration](docs/configuration.md) | Every setting section by section: the units and colour bands, the low alert and when it fires, the trend arrow, how the file is validated and reloaded |
| [Placing the face in VR](docs/placement.md) | `offset` and `rotation_deg`, orbit mode and the arm guides, gaze fading |
| [The history sparkline](docs/graph.md) | `[graph]`: how far back it shows, the fixed axis floor, and what the trace does and does not draw |
| [Why it is built this way](docs/design.md) | The reasoning, and the LibreLinkUp API quirks |

`config.example.toml` carries the same explanations as comments beside
the values, if you would rather read it there.

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
