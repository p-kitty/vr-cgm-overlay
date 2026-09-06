# CLAUDE.md

Guidance for Claude Code when working in this repository.

## Project

A SteamVR overlay that shows the current blood glucose value on a
controller-tracked watch face, so it stays readable during VR gameplay.
Glucose data comes from the unofficial LibreLinkUp API.

Single Python process. See `README.md` for the architecture and for the
list of LibreLinkUp API quirks the client works around.

The code is one installed package, `src/cgm/`, in three layers:

| Layer | Holds | Needs |
|---|---|---|
| `cgm.core` | API client, config, poller, config watcher | nothing special |
| `cgm.face` | the watch face drawing | Pillow |
| `cgm.vr` | the SteamVR overlay and its arm guide | a headset |

**Put new code in the shallowest layer that can hold it.** The split
exists so a second frontend can show the same face outside VR, and that
only pays off while `cgm.vr` stays the small half. `cgm.face` imports
nothing from `cgm.core`; the one crossing is `cgm.core.poller` reaching
up for `TrendTuning`, so the fetch log cannot name a trend source the
face is not drawing.

`NOTES.md` holds what is still open: unverified paths, limits that are
not going away, decisions that still stand. Keep it out of `README.md`,
which is for people running the thing.

**Take an entry out once it is resolved.** A fixed bug belongs in its
commit message and in a comment beside the code it bit, not in a file
that grows until nobody reads it. `NOTES.md` records what is still true,
never what happened.

## Language

**Write everything in English.** This applies to:

- Commit messages
- Code comments and docstrings
- Identifiers, log messages, and user-facing strings
- Documentation, including this file and `README.md`
- `config.toml`, even though it is never committed

The only exception is text quoted from an external source that would lose
meaning in translation.

## Git workflow

**Branch before starting work.** Cut a new branch from `master` at the
start of any task. Never commit directly to `master`.

```bash
git checkout master
git checkout -b <type>/<short-description>
```

Commit freely on the working branch — small, focused commits are
preferred over one large one.

**Never merge into `master` without being told to.** Leave finished work
on its branch and say it is ready. The user decides when it merges.

**Merge with `--no-ff`.** Every merge keeps a merge commit, so a branch
stays visible as a unit of work in the history.

```bash
git merge --no-ff <branch>
```

## Commit messages

Prefix every subject line with a Conventional Commits type:

| Prefix | Use for |
|---|---|
| `feat:` | A new capability |
| `fix:` | A bug fix |
| `refactor:` | Restructuring that keeps behaviour the same |
| `docs:` | Documentation, comments, README |
| `test:` | Tests and verification tooling |
| `chore:` | Build, dependencies, licensing, config plumbing |

Keep the subject in the imperative and under about 72 characters, then
leave a blank line and explain **why** the change was made. What changed
is already in the diff; the reasoning is not.

```
refactor: drop keyring and read the password from config only

Two ways to supply one password was confusing with no real benefit.
```

## Verification

Before committing, confirm the code still runs, on Python 3.14 — the
version the project targets. All of these import `cgm`, so the package
has to be installed first (`pip install -e .`, once per checkout). None
of them need a VR headset or network access:

```bash
python -m unittest discover -s tests  # the logic that runs headless
python tools/preview.py               # every watch face state, to a PNG
python tools/check_orbit.py           # the orbit placement geometry
python tools/check_gaze.py            # the gaze fade and the rules on it
python tools/check_palette.py         # the palette under colour blindness
python -m compileall -q src tools tests
```

`tests/` covers what can be asserted without a device: timestamp
parsing, the trend fit and the arrow angle it maps to, the fetch
schedule and its backoff, config validation, the live reload and which
settings it cannot apply, and the colour thresholds. It deliberately does not mock the LibreLinkUp HTTP
calls — the real risk there is the unofficial API
changing shape, which only `--dry-run` can see — and it does not touch
`cgm.vr`.

To check the API client against the live service (needs `config.toml`):

```bash
vr-cgm-overlay --dry-run
```

Anything under `src/cgm/vr/` requires a headset and SteamVR to verify. Do
not claim overlay behaviour is confirmed without a device — say it is
untested instead.

## Constraints

- **Do not lower `polling.interval_sec` below 30.** The sensor updates
  about once a minute; polling faster yields nothing new and risks the
  account being rate limited or blocked.
- **Never commit `config.toml`.** It holds the account password. It is in
  `.gitignore`; keep it there.
- **But do edit it.** Untracked is not the same as untouchable. When a
  change adds a setting, put it in `config.toml` as well as in
  `config.example.toml` — a setting the user has to paste in by hand is
  not delivered, it is homework. Load the file once afterwards to prove
  it still parses, and say what was added. Leave values the user has
  already tuned alone unless the change is about those values.
- **Target Python 3.14, and do not lower the dependency floors in
  `pyproject.toml`.** Each one is the first release of that package
  that runs on 3.14; below them the install succeeds and the import
  fails. `src/cgm/main.py` refuses an older interpreter, so keep that
  guard and the floors in step.
- Range checks are always done in mg/dL, even when displaying mmol/L.
- This is not a medical device. Keep the disclaimer in `README.md`.
