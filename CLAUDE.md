# CLAUDE.md

Guidance for Claude Code when working in this repository.

## Project

A SteamVR overlay that shows the current blood glucose value on a
controller-tracked watch face, so it stays readable during VR gameplay.
Glucose data comes from the unofficial LibreLinkUp API.

Single Python process. See `README.md` for the architecture and for the
list of LibreLinkUp API quirks the client works around.

## Language

**Write everything in English.** This applies to:

- Commit messages
- Code comments and docstrings
- Identifiers, log messages, and user-facing strings
- Documentation, including this file and `README.md`

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

Before committing, confirm the code still runs. Neither check needs a VR
headset or network access:

```bash
python tools/preview.py      # renders every watch face state to a PNG
python -m compileall -q src tools
```

To check the API client against the live service (needs `config.toml`):

```bash
python src/main.py --dry-run
```

Anything in `src/overlay.py` requires a headset and SteamVR to verify. Do
not claim overlay behaviour is confirmed without a device — say it is
untested instead.

## Constraints

- **Do not lower `polling.interval_sec` below 30.** The sensor updates
  about once a minute; polling faster yields nothing new and risks the
  account being rate limited or blocked.
- **Never commit `config.toml`.** It holds the account password. It is in
  `.gitignore`; keep it there.
- Range checks are always done in mg/dL, even when displaying mmol/L.
- This is not a medical device. Keep the disclaimer in `README.md`.
