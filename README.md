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

Controller origins differ between Index, Touch and Vive, so assume the
first run needs tuning: see
[Placing the face in VR](docs/placement.md).

## Without a headset

The same watch face runs in a desktop window.

```bash
vr-cgm-overlay --window
```

No SteamVR, no `openvr`, no headset — a plain `pip install -e .` is
enough. It is the same fetching and the same face, so everything in
[Configuration](docs/configuration.md) that is not about placement
applies here too: the colour bands, the units, the trend arrow, the
stale greying, and `config.toml` being re-read while it runs.

Useful for a second monitor while you are doing something other than
playing, and useful for anything that takes hours to show itself — a
long session's fetch schedule, a token expiring and being renewed, or
whether the trend window is the right length against a real day. None of
those are VR questions, and none of them are worth wearing a headset for
as long as they take to answer.

**The window draws the history sparkline and the overlay does not**, and
that is the one thing the two frontends deliberately disagree about. See
[The history sparkline](docs/graph.md); `graph.in_window` turns it off
if you would rather have the number alone.

`alert_on_low` works here through sound. The controller buzz is the one
channel a window has no hardware for; everything about *when* to
announce a low is shared, so the window and the headset cannot disagree
about whether you have already been told.

## Settings

Everything is in `config.toml`, which is **re-read while the app runs** —
edit it with the headset on and the face changes within a second. Only
`hand` and `[account]` need a restart, and a key nothing recognises stops
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
