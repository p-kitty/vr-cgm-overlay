"""Make this process's stdout and stderr carry UTF-8.

The face draws its arrows as vectors, but the console and the log print
them as characters, `↘` and `↗` among them. Python hands `sys.stdout`
UTF-8 when it is attached to a real Windows console whatever the code
page is set to, and falls back to the locale encoding the moment that
stream is a pipe or a redirect. On a Japanese install the fallback is
cp932, which has `↓ ↑ →` but not `↘ ↗` -- so `--dry-run > check.txt`
died with UnicodeEncodeError, or did not, depending on nothing but which
way the glucose happened to be moving.

Redirecting is exactly what you do to keep a record of a check or to
paste one into an issue, so the fix is to name the encoding rather than
to give up the arrows.

This is stdio, not VR, so it lives here: any second frontend gets it
with one call rather than by importing the overlay's entry point.
"""

from __future__ import annotations

import sys


def _as_utf8(stream) -> bool:
    """Re-encode one text stream as UTF-8. True when it took."""
    reconfigure = getattr(stream, "reconfigure", None)
    if reconfigure is None:
        # Not a text wrapper over a real file: pythonw hands out None,
        # and a test harness or an IDE may swap in a StringIO, which
        # holds str and so has no encoding to get wrong.
        return False
    try:
        # errors="replace" is belt and braces -- UTF-8 can encode
        # everything except a lone surrogate -- but it means no
        # character printed later can take the process down either.
        reconfigure(encoding="utf-8", errors="replace")
    except (OSError, ValueError):
        # Already detached, or closed. A stream we cannot set up is not
        # worth refusing to start over.
        return False
    return True


def force_utf8_output() -> None:
    """Point stdout and stderr at UTF-8, before anything is written.

    Call it first thing in an entry point. `reconfigure` changes the
    stream in place, so anything that picks the object up afterwards
    writes through the new encoding too -- including the handler
    `logging.basicConfig` builds on `sys.stderr`, which is the half of
    this bug that does not raise: `logging` swallows the
    UnicodeEncodeError and drops the line, so a session logged to a file
    silently loses exactly the `(API)` fetches worth watching for.
    """
    _as_utf8(sys.stdout)
    _as_utf8(sys.stderr)
